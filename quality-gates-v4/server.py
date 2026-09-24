"""Quality Gates v4 pilot server. Python 3.9+, standard library only."""
import argparse
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'quality-gates.sqlite3'
BOOTSTRAP = secrets.token_urlsafe(32)
ROLES = {'admin', 'supervisor', 'qc', 'production'}
DEPTS = ['Assembly', 'Carpentry', 'Electrical', 'Plumbing', 'Finishing']
SIGNED_ACTIONS = {'pass', 'verify', 'hold-pass', 'na', 'defer', 'release', 'approve', 'transfer'}

class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status

def require(condition, message, status=400):
    if not condition:
        raise Problem(message, status)

def connect():
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    return con

def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return salt + ':' + hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 300000).hex()

def check_password(password, stored):
    return hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)

def signing_key():
    path = DB.with_name('signing.key')
    if not path.exists():
        path.write_bytes(secrets.token_bytes(32))
        os.chmod(path, 0o600)
    return path.read_bytes()

def digital_signature(con, user, data, payload):
    """Password-backed, server-sealed electronic signature receipt."""
    row = con.execute('SELECT password FROM users WHERE id=?', (user['id'],)).fetchone()
    password = data.get('signaturePassword', '')
    require(row is not None and isinstance(password, str) and check_password(password, row['password']),
            'Digital signature failed: password is incorrect', 403)
    signed_at = time.time()
    receipt = {
        'userId': user['id'], 'name': user['name'], 'username': user['username'],
        'role': user['role'], 'at': signed_at, 'statement': 'I approve this quality decision.'
    }
    document = json.dumps({'receipt': receipt, 'payload': payload}, sort_keys=True, separators=(',', ':')).encode()
    receipt['digest'] = hashlib.sha256(document).hexdigest()
    receipt['seal'] = hmac.new(signing_key(), document, hashlib.sha256).hexdigest()
    receipt['payload'] = payload
    return receipt

def signature_valid(receipt):
    try:
        base = {k: receipt[k] for k in ('userId', 'name', 'username', 'role', 'at', 'statement')}
        document = json.dumps({'receipt': base, 'payload': receipt['payload']}, sort_keys=True, separators=(',', ':')).encode()
        return (hmac.compare_digest(receipt['digest'], hashlib.sha256(document).hexdigest()) and
                hmac.compare_digest(receipt['seal'], hmac.new(signing_key(), document, hashlib.sha256).hexdigest()))
    except (KeyError, TypeError):
        return False

def text_field(data, key, limit=2000):
    value = data.get(key, '')
    require(isinstance(value, str) and 0 < len(value.strip()) <= limit, key + ' is required')
    return value.strip()

def initialize():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with connect() as con:
        con.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
          username TEXT UNIQUE NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL, department TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id), expires REAL);
        CREATE TABLE IF NOT EXISTS templates(id INTEGER PRIMARY KEY, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trailers(id TEXT PRIMARY KEY, inventory TEXT UNIQUE, revision INTEGER, body TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, trailer_id TEXT, actor INTEGER,
          at REAL, action TEXT, body TEXT);
        CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'Audit events are immutable'); END;
        CREATE TABLE IF NOT EXISTS media(id TEXT PRIMARY KEY, trailer_id TEXT, item_id TEXT,
          name TEXT, mime TEXT, content BLOB, actor INTEGER, at REAL);
        CREATE TABLE IF NOT EXISTS attempts(address TEXT PRIMARY KEY, count INTEGER, until REAL);
        ''')
        if not con.execute('SELECT 1 FROM templates').fetchone():
            source = (ROOT.parent / 'quality-gates-dashboard-v3 copy.html').read_text()
            gates = json.loads(re.search(r'const G=(\{.*?\});\s*const ', source, re.S).group(1))
            snapshot = []
            for gid, gate in gates.items():
                items = []
                for index, item in enumerate(gate['items'], 1):
                    items.append(dict(id=f'{gid}-{index:04}', number=item['num'], section=item['section'],
                                      description=item['desc'], models=[], option=''))
                snapshot.append(dict(id=gid, name=gate['name'], items=items))
            con.execute('INSERT INTO templates(body) VALUES(?)', (json.dumps(snapshot),))
        # Normalize older template versions without changing their version number.
        for row in con.execute('SELECT id,body FROM templates').fetchall():
            gates = json.loads(row['body'])
            changed = False
            for gate in gates:
                if 'active' not in gate:
                    gate['active'] = True
                    changed = True
            if changed:
                con.execute('UPDATE templates SET body=? WHERE id=?', (json.dumps(gates), row['id']))
    os.chmod(DB, 0o600)
    signing_key()

def audit(con, user, action, tid=None, details=None):
    con.execute('INSERT INTO events(trailer_id,actor,at,action,body) VALUES(?,?,?,?,?)',
                (tid, user['id'], time.time(), action, json.dumps(details or {})))

def allowed(user, *roles):
    require(user['role'] in roles, 'Your role cannot perform this action', 403)

def latest(con):
    row = con.execute('SELECT * FROM templates ORDER BY id DESC LIMIT 1').fetchone()
    return row['id'], json.loads(row['body'])

def state(con, user):
    version, gates = latest(con)
    users = [dict(r) for r in con.execute('SELECT id,name,username,role,department FROM users')]
    trailers = [dict(json.loads(r['body']), revision=r['revision']) for r in con.execute('SELECT * FROM trailers')]
    for trailer in trailers:
        signed_records = list(trailer.get('checks', {}).values()) + list(trailer.get('releases', {}).values())
        if isinstance(trailer.get('approved'), dict):
            signed_records.append(trailer['approved'])
        for record in signed_records:
            if isinstance(record.get('signature'), dict):
                record['signature']['verifiedSeal'] = signature_valid(record['signature'])
    return dict(user=dict(user), users=users, trailers=trailers, template=dict(version=version, gates=gates), departments=DEPTS)

def touch_operation(trailer, gate_id, user):
    activity = trailer.setdefault('operationActivity', {}).setdefault(gate_id, {
        'startedBy': user['name'], 'startedById': user['id'], 'startedAt': time.time(), 'contributors': []
    })
    if not any(c['id'] == user['id'] for c in activity['contributors']):
        activity['contributors'].append({'id': user['id'], 'name': user['name'], 'at': time.time()})
    return activity

def dispatch(con, user, data):
    action = data.get('action')
    if action == 'user':
        allowed(user, 'admin')
        role = data.get('role')
        require(role in ROLES, 'Invalid role')
        dept = data.get('department', '')
        require(role != 'production' or dept in DEPTS, 'Select a department')
        password = text_field(data, 'password', 256)
        require(len(password) >= 12, 'Use at least 12 password characters')
        con.execute('INSERT INTO users(name,username,password,role,department) VALUES(?,?,?,?,?)',
                    (text_field(data, 'name', 100), text_field(data, 'username', 100).lower(), password_hash(password), role, dept))
        audit(con, user, 'user-created', details={'username': data['username'], 'role': role})
        return
    if action in ('operation-add', 'operation-edit', 'operation-move', 'control-add', 'control-edit'):
        allowed(user, 'admin')
        version, gates = latest(con)
        require(data.get('version') == version, 'Operations changed. Refresh before editing.', 409)
        if action in ('control-add', 'control-edit'):
            target = next((g for g in gates if g['id'] == data.get('operation')), None)
            require(target is not None, 'Unknown target operation')
            models = data.get('models', [])
            require(isinstance(models, list) and len(models) <= 30 and
                    all(isinstance(m, str) and 0 < len(m) <= 60 for m in models), 'Invalid models')
            values = {
                'number': text_field(data, 'number', 40),
                'section': text_field(data, 'section', 100),
                'description': text_field(data, 'description'),
                'models': models,
                'option': str(data.get('option', ''))[:100]
            }
            if action == 'control-add':
                item = {'id': 'control-' + secrets.token_hex(8), **values}
                target['items'].append(item)
                details = {'operation': target['id'], 'control': item, 'version': version + 1}
            else:
                source = next((g for g in gates if any(i['id'] == data.get('item') for i in g['items'])), None)
                require(source is not None, 'Unknown control')
                item = next(i for i in source['items'] if i['id'] == data.get('item'))
                before = {'operation': source['id'], **dict(item)}
                item.update(values)
                if source['id'] != target['id']:
                    source['items'].remove(item)
                    target['items'].append(item)
                details = {'before': before, 'after': {'operation': target['id'], **dict(item)}, 'version': version + 1}
        elif action == 'operation-add':
            gate = {'id': 'op-' + secrets.token_hex(5), 'name': text_field(data, 'name', 100), 'items': [], 'active': True}
            gates.append(gate)
            details = {'operation': gate, 'version': version + 1}
        else:
            index = next((n for n, g in enumerate(gates) if g['id'] == data.get('operation')), None)
            require(index is not None, 'Unknown operation')
            if action == 'operation-edit':
                before = {'id': gates[index]['id'], 'name': gates[index]['name'], 'active': gates[index].get('active', True)}
                gates[index]['name'] = text_field(data, 'name', 100)
                gates[index]['active'] = bool(data.get('active'))
                after = {'id': gates[index]['id'], 'name': gates[index]['name'], 'active': gates[index]['active']}
                details = {'before': before, 'after': after, 'version': version + 1}
            else:
                direction = data.get('direction')
                require(direction in ('up', 'down'), 'Invalid move')
                target = index + (-1 if direction == 'up' else 1)
                require(0 <= target < len(gates), 'Operation is already at the end')
                gates[index], gates[target] = gates[target], gates[index]
                details = {'operation': data.get('operation'), 'direction': direction, 'version': version + 1}
        require(any(g.get('active', True) for g in gates), 'At least one operation must remain active')
        con.execute('INSERT INTO templates(body) VALUES(?)', (json.dumps(gates),))
        audit(con, user, action, details=details)
        return
    if action == 'template':
        allowed(user, 'admin', 'supervisor')
        version, gates = latest(con)
        require(data.get('version') == version, 'Checklist changed. Refresh before editing.', 409)
        item = next((i for g in gates for i in g['items'] if i['id'] == data.get('item')), None)
        require(item is not None, 'Unknown item')
        models = data.get('models', [])
        require(isinstance(models, list) and len(models) <= 30 and all(isinstance(m, str) and 0 < len(m) <= 60 for m in models), 'Invalid models')
        old = dict(item)
        item.update(number=text_field(data, 'number', 40), description=text_field(data, 'description'),
                    models=models, option=str(data.get('option', ''))[:100])
        con.execute('INSERT INTO templates(body) VALUES(?)', (json.dumps(gates),))
        audit(con, user, 'checklist-published', details={'before': old, 'after': item, 'version': version + 1})
        return
    if action == 'create':
        allowed(user, 'admin', 'supervisor', 'qc')
        model = text_field(data, 'model', 60)
        options = data.get('options', [])
        require(isinstance(options, list) and len(options) <= 100 and all(isinstance(v, str) and len(v) <= 100 for v in options), 'Invalid options')
        version, gates = latest(con)
        gates = [gate for gate in gates if gate.get('active', True)]
        for gate in gates:
            gate['items'] = [i for i in gate['items'] if (not i['models'] or model in i['models']) and (not i['option'] or i['option'] in options)]
        require(gates and any(g['items'] for g in gates), 'No applicable controls for this model')
        tid = secrets.token_hex(12)
        trailer = dict(id=tid, inventory=text_field(data, 'inventory', 100), customer=text_field(data, 'customer', 100),
                       model=model, options=options, version=version, gates=gates, checks={}, releases={}, approved=False,
                       currentOperation=gates[0]['id'], operationActivity={}, created=time.time())
        con.execute('INSERT INTO trailers VALUES(?,?,?,?)', (tid, trailer['inventory'], 1, json.dumps(trailer)))
        audit(con, user, 'trailer-created', tid, {'version': version, 'model': model, 'options': options})
        return
    tid = data.get('trailer')
    row = con.execute('SELECT * FROM trailers WHERE id=?', (tid,)).fetchone()
    require(row is not None, 'Trailer not found', 404)
    require(data.get('revision') == row['revision'], 'Another user changed this trailer. Refresh and retry.', 409)
    trailer = json.loads(row['body'])
    trailer.setdefault('currentOperation', trailer['gates'][0]['id'] if trailer.get('gates') else None)
    trailer.setdefault('operationActivity', {})
    if action == 'reopen':
        allowed(user, 'supervisor', 'admin')
        require(trailer['approved'], 'Trailer is not approved')
        reason = text_field(data, 'reason')
        trailer['approved'] = False
        audit(con, user, action, tid, {'reason': reason})
    else:
        require(not trailer['approved'], 'Reopen this trailer before making changes')
        gates = trailer['gates']
        ids = [g['id'] for g in gates]
        if action in ('start-operation', 'transfer'):
            allowed(user, 'qc', 'supervisor', 'admin') if action == 'start-operation' else allowed(user, 'supervisor', 'admin')
            ids = [g['id'] for g in trailer['gates']]
            if action == 'start-operation':
                gid = data.get('gate')
                require(gid in ids, 'Unknown operation')
                require(gid == trailer['currentOperation'], 'A supervisor or administrator must transfer the POD first', 403)
                activity = touch_operation(trailer, gid, user)
                trailer['currentOperation'] = gid
                audit(con, user, action, tid, {'gate': gid, 'activity': activity})
            else:
                target = data.get('target')
                require(target in ids and target != trailer['currentOperation'], 'Choose another operation')
                reason = text_field(data, 'reason')
                signature = digital_signature(con, user, data, {
                    'action': action, 'trailer': tid, 'from': trailer['currentOperation'], 'to': target,
                    'reason': reason, 'revision': row['revision']
                })
                before = trailer['currentOperation']
                trailer['currentOperation'] = target
                audit(con, user, action, tid, {'from': before, 'to': target, 'reason': reason, 'signature': signature})
        elif action in ('release', 'approve'):
            allowed(user, 'supervisor', 'admin')
            signature = digital_signature(con, user, data, {
                'action': action, 'trailer': tid, 'gate': data.get('gate'), 'revision': row['revision']
            })
            if action == 'release':
                gid = data.get('gate')
                require(gid in ids, 'Unknown gate')
                index = ids.index(gid)
                require(all(g in trailer['releases'] for g in ids[:index]), 'Release preceding gates first')
                controls = [(n, i) for n, g in enumerate(gates) for i in g['items'] if n <= index]
                for n, item in controls:
                    c = trailer['checks'].get(item['id'], {})
                    deferred = c.get('deferredTo')
                    require(c.get('status') in ('pass', 'na') or (deferred in ids and ids.index(deferred) > index),
                            'An incomplete or open control blocks this gate: ' + item['id'])
                trailer['releases'][gid] = {'by': user['name'], 'at': time.time(), 'signature': signature}
            else:
                require(all(g in trailer['releases'] for g in ids), 'Release every gate first')
                require(all(trailer['checks'].get(i['id'], {}).get('status') in ('pass', 'na') for g in gates for i in g['items']), 'Open items block final approval')
                trailer['approved'] = {'by': user['name'], 'at': time.time(), 'signature': signature}
            audit(con, user, action, tid, {'gate': data.get('gate'), 'signature': signature})
        else:
            match = next(((n, i) for n, g in enumerate(gates) for i in g['items'] if i['id'] == data.get('item')), None)
            require(match is not None, 'Unknown control')
            gate_index, item = match
            key = item['id']
            current = trailer['checks'].get(key, {'status': 'pending'})
            before = dict(current)
            signature = None
            if action in SIGNED_ACTIONS:
                signature = digital_signature(con, user, data, {
                    'action': action, 'trailer': tid, 'item': key, 'result': data.get('result'),
                    'reason': data.get('reason', ''), 'revision': row['revision']
                })
            if action == 'pass':
                allowed(user, 'qc', 'supervisor', 'admin')
                require(current['status'] in ('pending', 'pass'), 'Use QC verification for an existing issue')
                current.update(status='pass')
            elif action == 'reject':
                allowed(user, 'qc', 'supervisor', 'admin')
                dept = data.get('department')
                require(dept in DEPTS, 'Select responsible department')
                current.update(status='rejected', department=dept, reason=text_field(data, 'reason'), openedAt=time.time())
                current.pop('deferredTo', None)
            elif action == 'hold':
                allowed(user, 'qc', 'supervisor', 'admin')
                current.update(status='hold', reason=text_field(data, 'reason'), openedAt=time.time())
                current.pop('deferredTo', None)
            elif action == 'na':
                allowed(user, 'supervisor', 'admin')
                require(current['status'] == 'pending', 'N/A only applies to unchecked controls')
                current.update(status='na', reason=text_field(data, 'reason'))
            elif action == 'correct':
                allowed(user, 'production')
                require(current['status'] == 'rejected' and current.get('department') == user['department'], 'Only the assigned department can report this correction', 403)
                current.update(status='verification', correction=text_field(data, 'reason'), correctedBy=user['name'])
            elif action == 'verify':
                allowed(user, 'qc', 'supervisor', 'admin')
                require(current['status'] == 'verification', 'No correction awaiting QC verification')
                require(data.get('result') in ('pass', 'rejected'), 'Invalid result')
                current.update(status=data['result'], verification=text_field(data, 'reason'))
                if current['status'] == 'pass':
                    current.pop('deferredTo', None)
            elif action == 'defer':
                allowed(user, 'supervisor', 'admin')
                target = data.get('target')
                require(current['status'] in ('rejected', 'hold', 'verification'), 'Only open issues can be deferred')
                require(target in ids and ids.index(target) > gate_index, 'Choose a later gate')
                require(target not in trailer['releases'], 'Target gate is already released')
                current.update(deferredTo=target, deferralReason=text_field(data, 'reason'), deferredBy=user['name'])
            elif action == 'hold-pass':
                allowed(user, 'supervisor', 'admin')
                require(current['status'] == 'hold', 'Not on hold')
                current.update(status='pass', decision=text_field(data, 'reason'))
                current.pop('deferredTo', None)
            elif action == 'media':
                allowed(user, 'qc', 'supervisor', 'admin', 'production')
                require(user['role'] != 'production' or current.get('department') == user['department'], 'Not your department', 403)
                mime = data.get('mime')
                require(mime in ('image/jpeg', 'image/png', 'image/webp', 'video/mp4', 'video/webm', 'audio/webm', 'audio/mpeg', 'audio/mp4', 'audio/ogg'), 'Unsupported media type')
                try:
                    content = base64.b64decode(data.get('content', ''), validate=True)
                except Exception:
                    raise Problem('Invalid media')
                require(0 < len(content) <= 10 * 1024 * 1024, 'Media limit is 10 MB')
                mid = secrets.token_hex(16)
                name = text_field(data, 'name', 200)
                con.execute('INSERT INTO media VALUES(?,?,?,?,?,?,?,?)', (mid, tid, key, name, mime, content, user['id'], time.time()))
                current['media'] = current.get('media', []) + [{'id': mid, 'name': name}]
            else:
                raise Problem('Unknown action')
            current.update(by=user['name'], at=time.time())
            if signature:
                current['signature'] = signature
            if user['role'] in ('qc', 'supervisor', 'admin'):
                touch_operation(trailer, gates[gate_index]['id'], user)
            trailer['checks'][key] = current
            if action != 'media':
                trailer['releases'] = {g: v for g, v in trailer['releases'].items() if ids.index(g) < gate_index}
            audit(con, user, action, tid, {'item': key, 'before': before, 'after': current})
    con.execute('UPDATE trailers SET body=?, revision=revision+1 WHERE id=?', (json.dumps(trailer), tid))

class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, mime='application/json', cookie=None):
        if mime == 'application/json':
            body = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Referrer-Policy', 'no-referrer')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def auth(self, con):
        cookies = dict(p.strip().split('=', 1) for p in self.headers.get('Cookie', '').split(';') if '=' in p)
        token = hashlib.sha256(cookies.get('qg_session', '').encode()).hexdigest()
        row = con.execute('SELECT u.id,u.name,u.username,u.role,u.department FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>?', (token, time.time())).fetchone()
        require(row is not None, 'Please sign in', 401)
        return dict(row)

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if path in ('/', '/app.js', '/style.css'):
                file = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}[path]
                mime = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript', '/style.css': 'text/css'}[path]
                return self.send(200, (ROOT / file).read_bytes(), mime)
            with connect() as con:
                if path == '/api/setup':
                    return self.send(200, {'required': not bool(con.execute('SELECT 1 FROM users').fetchone())})
                user = self.auth(con)
                if path == '/api/state':
                    return self.send(200, state(con, user))
                if path.startswith('/api/history/'):
                    tid = path.rsplit('/', 1)[1]
                    rows = con.execute('SELECT e.*,u.name FROM events e JOIN users u ON u.id=e.actor WHERE trailer_id=? ORDER BY e.id DESC', (tid,))
                    return self.send(200, [dict(r, body=json.loads(r['body'])) for r in rows])
                if path.startswith('/api/media/'):
                    row = con.execute('SELECT * FROM media WHERE id=?', (path.rsplit('/', 1)[1],)).fetchone()
                    require(row is not None, 'Not found', 404)
                    return self.send(200, row['content'], row['mime'])
                raise Problem('Not found', 404)
        except Problem as e:
            self.send(e.status, {'error': e.message})

    def do_POST(self):
        try:
            require(self.headers.get('X-QG-Request') == '1', 'Invalid request origin', 403)
            origin = self.headers.get('Origin')
            require(not origin or urlsplit(origin).netloc == self.headers.get('Host'), 'Invalid origin', 403)
            length = int(self.headers.get('Content-Length', '0'))
            require(0 < length <= 15 * 1024 * 1024, 'Request too large', 413)
            data = json.loads(self.rfile.read(length))
            require(isinstance(data, dict), 'Invalid request')
            path = urlsplit(self.path).path
            with connect() as con:
                con.execute('BEGIN IMMEDIATE')
                if path == '/api/setup':
                    require(not con.execute('SELECT 1 FROM users').fetchone(), 'Already initialized', 409)
                    require(hmac.compare_digest(str(data.get('token', '')), BOOTSTRAP), 'Use the setup token printed by the server', 403)
                    password = text_field(data, 'password', 256)
                    require(len(password) >= 12, 'Use at least 12 password characters')
                    con.execute('INSERT INTO users(name,username,password,role,department) VALUES(?,?,?,?,?)', (text_field(data, 'name', 100), text_field(data, 'username', 100).lower(), password_hash(password), 'admin', ''))
                    return self.send(200, {'ok': True})
                if path == '/api/login':
                    address = self.client_address[0]
                    attempt = con.execute('SELECT * FROM attempts WHERE address=?', (address,)).fetchone()
                    require(not attempt or attempt['count'] < 10 or attempt['until'] < time.time(), 'Too many attempts. Try again in 15 minutes.', 429)
                    row = con.execute('SELECT * FROM users WHERE username=?', (text_field(data, 'username', 100).lower(),)).fetchone()
                    if not row or not check_password(text_field(data, 'password', 256), row['password']):
                        count = attempt['count'] + 1 if attempt and attempt['until'] > time.time() else 1
                        con.execute('INSERT OR REPLACE INTO attempts VALUES(?,?,?)', (address, count, time.time() + 900))
                        con.commit()
                        raise Problem('Invalid username or password', 401)
                    con.execute('DELETE FROM attempts WHERE address=?', (address,))
                    token = secrets.token_urlsafe(32)
                    con.execute('INSERT INTO sessions VALUES(?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], time.time() + 28800))
                    return self.send(200, {'ok': True}, cookie=f'qg_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800')
                user = self.auth(con)
                if path == '/api/logout':
                    con.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],))
                    return self.send(200, {'ok': True}, cookie='qg_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0')
                require(path == '/api/action', 'Not found', 404)
                dispatch(con, user, data)
                result = state(con, user)
            self.send(200, result)
        except Problem as e:
            self.send(e.status, {'error': e.message})
        except sqlite3.IntegrityError:
            self.send(409, {'error': 'Inventory number or username already exists; no changes saved.'})
        except (ValueError, TypeError, KeyError):
            self.send(400, {'error': 'Invalid request data'})
        except Exception:
            self.log_error('Request failed')
            self.send(500, {'error': 'Server error. No changes saved; retry or contact administrator.'})

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8040)
    parser.add_argument('--db', type=Path, default=DB)
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    DB = args.db.resolve()
    initialize()
    if args.backup:
        require(not args.backup.exists(), 'Choose a new backup filename')
        with connect() as source, sqlite3.connect(args.backup) as destination:
            source.backup(destination)
        os.chmod(args.backup, 0o600)
        print('Backup saved:', args.backup)
    else:
        print(f'Quality Gates v4: http://{args.host}:{args.port}', flush=True)
        with connect() as con:
            if not con.execute('SELECT 1 FROM users').fetchone():
                print('First administrator setup token: ' + BOOTSTRAP, flush=True)
        ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
