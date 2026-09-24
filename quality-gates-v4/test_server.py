import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import server as app

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        app.DB = Path(self.temp.name) / 'test.sqlite3'
        app.initialize()
        self.con = app.connect()
        self.test_password = 'long-test-password'
        with self.con:
            self.con.executemany('INSERT INTO users(id,name,username,password,role,department) VALUES(?,?,?,?,?,?)', [
                (1, 'Admin', 'admin', app.password_hash(self.test_password), 'admin', ''),
                (2, 'QC', 'qc-one', app.password_hash(self.test_password), 'qc', ''),
                (3, 'Maker', 'maker', app.password_hash(self.test_password), 'production', 'Carpentry'),
                (4, 'QC Two', 'qc-two', app.password_hash(self.test_password), 'qc', '')])
        self.admin = dict(id=1, name='Admin', username='admin', role='admin', department='')
        self.qc = dict(id=2, name='QC', username='qc-one', role='qc', department='')
        self.qc2 = dict(id=4, name='QC Two', username='qc-two', role='qc', department='')
        self.production = dict(id=3, name='Maker', username='maker', role='production', department='Carpentry')
        self.act(self.admin, action='create', inventory='T1', customer='Test', model='ET 19', options=[])
        self.tid = self.con.execute('SELECT id FROM trailers').fetchone()[0]
        self.t = self.get()
        self.key = self.t['gates'][0]['items'][0]['id']

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def get(self):
        row = self.con.execute('SELECT * FROM trailers LIMIT 1').fetchone()
        return dict(json.loads(row['body']), revision=row['revision'])

    def act(self, user, **data):
        with self.con:
            app.dispatch(self.con, user, data)

    def change(self, user, action, **extra):
        if action in app.SIGNED_ACTIONS:
            extra.setdefault('signaturePassword', self.test_password)
        self.act(user, action=action, trailer=self.tid, revision=self.get()['revision'], item=self.key, **extra)

    def test_identifiers_and_snapshot(self):
        items = [i for g in self.t['gates'] for i in g['items']]
        self.assertEqual(len(items), 323)
        self.assertEqual(len({i['id'] for i in items}), len(items))
        self.act(self.admin, action='template', version=1, item=self.key, number='NEW', description='Changed', models=['ET 23'], option='Solar')
        self.assertNotEqual(self.get()['gates'][0]['items'][0]['number'], 'NEW')
        self.act(self.admin, action='create', inventory='T2', customer='Test', model='ET 19', options=[])
        row = self.con.execute("SELECT body FROM trailers WHERE inventory='T2'").fetchone()
        self.assertNotIn(self.key, [i['id'] for g in json.loads(row[0])['gates'] for i in g['items']])

    def test_correction_requires_department_and_qc(self):
        self.change(self.qc, 'reject', reason='Gap too wide', department='Carpentry')
        with self.assertRaises(app.Problem):
            self.change(dict(self.production, department='Electrical'), 'correct', reason='Fixed')
        with self.assertRaises(app.Problem):
            self.change(self.qc, 'pass')
        self.change(self.production, 'correct', reason='Adjusted mounting')
        with self.assertRaises(app.Problem):
            self.change(self.production, 'verify', result='pass', reason='Looks good')
        self.change(self.qc, 'verify', result='rejected', reason='Still too wide')
        self.change(self.production, 'correct', reason='Replaced bracket')
        self.change(self.qc, 'verify', result='pass', reason='Measured and accepted')
        self.assertEqual(self.get()['checks'][self.key]['status'], 'pass')
        self.assertEqual(self.con.execute('SELECT count(*) FROM events').fetchone()[0], 6)

    def complete(self):
        t = self.get()
        t['checks'] = {i['id']: {'status': 'pass'} for g in t['gates'] for i in g['items']}
        self.con.execute('UPDATE trailers SET body=?,revision=revision+1 WHERE id=?', (json.dumps({k:v for k,v in t.items() if k!='revision'}), self.tid))
        self.con.commit()

    def test_operations_transfer_multi_qc_and_signatures(self):
        self.act(self.admin, action='operation-add', version=1, name='Final Detail')
        version, gates = app.latest(self.con)
        self.assertEqual(version, 2)
        self.assertEqual(gates[-1]['name'], 'Final Detail')
        self.act(self.admin, action='operation-move', version=2, operation=gates[-1]['id'], direction='up')
        version, gates = app.latest(self.con)
        new_operation = next(g for g in gates if g['name'] == 'Final Detail')
        self.act(self.admin, action='control-add', version=version, operation=new_operation['id'], number='FD-1',
                 section='Finish', description='Final finish check', models=[], option='')
        version, gates = app.latest(self.con)
        added = next(i for g in gates for i in g['items'] if i['number'] == 'FD-1')
        self.act(self.admin, action='control-edit', version=version, operation='prep', item=added['id'],
                 number='P-NEW', section='Preparation', description='Moved control', models=['ET 19'], option='Solar')
        version, gates = app.latest(self.con)
        moved = next(i for i in next(g for g in gates if g['id'] == 'prep')['items'] if i['id'] == added['id'])
        self.assertEqual((moved['number'], moved['option']), ('P-NEW', 'Solar'))
        self.change(self.qc, 'start-operation', gate=self.get()['currentOperation'])
        self.change(self.qc, 'pass')
        second = self.get()['gates'][0]['items'][1]['id']
        self.act(self.qc2, action='pass', trailer=self.tid, revision=self.get()['revision'], item=second,
                 signaturePassword=self.test_password)
        activity = self.get()['operationActivity'][self.get()['gates'][0]['id']]
        self.assertEqual({c['name'] for c in activity['contributors']}, {'QC', 'QC Two'})
        signed = self.get()['checks'][second]['signature']
        self.assertEqual(signed['username'], 'qc-two')
        self.assertEqual(len(signed['seal']), 64)
        self.assertTrue(app.signature_valid(signed))
        tampered = dict(signed, name='Different Person')
        self.assertFalse(app.signature_valid(tampered))
        target = self.get()['gates'][1]['id']
        with self.assertRaises(app.Problem):
            self.act(self.admin, action='transfer', trailer=self.tid, revision=self.get()['revision'], target=target,
                     reason='Move to next POD', signaturePassword='wrong-password')
        self.change(self.admin, 'transfer', target=target, reason='Move to next POD')
        self.assertEqual(self.get()['currentOperation'], target)

    def test_gate_deferral_and_final_approval(self):
        with self.assertRaises(app.Problem):
            self.change(self.admin, 'approve')
        self.complete()
        self.change(self.qc, 'reject', reason='Damage', department='Carpentry')
        with self.assertRaises(app.Problem):
            self.change(self.admin, 'release', gate='prep')
        self.change(self.admin, 'defer', target='bld-c', reason='Repair at shell station')
        self.change(self.admin, 'release', gate='prep')
        with self.assertRaises(app.Problem):
            self.change(self.admin, 'release', gate='bld-c')
        self.change(self.production, 'correct', reason='Repaired')
        self.change(self.qc, 'verify', result='pass', reason='Verified')
        for g in self.get()['gates']:
            self.change(self.admin, 'release', gate=g['id'])
        self.change(self.admin, 'approve')
        with self.assertRaises(app.Problem):
            self.change(self.qc, 'reject', department='Carpentry', reason='Changed')
        self.change(self.admin, 'reopen', reason='New inspection required')
        self.change(self.qc, 'hold', reason='Investigate')
        self.assertFalse(self.get()['releases'])

    def test_concurrency_and_audit(self):
        revision = self.get()['revision']
        self.change(self.qc, 'pass')
        with self.assertRaises(app.Problem) as error:
            self.act(self.qc, action='hold', trailer=self.tid, revision=revision, item=self.key, reason='Stale')
        self.assertEqual(error.exception.status, 409)
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute("UPDATE events SET action='changed'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.con.execute('DELETE FROM events')

    def test_role_validation_and_media(self):
        with self.assertRaises(app.Problem):
            self.change(self.production, 'pass')
        with self.assertRaises(app.Problem):
            self.change(self.qc, 'na', reason='Not applicable')
        self.change(self.admin, 'na', reason='No option installed')
        with self.assertRaises(app.Problem):
            self.change(self.qc, 'media', mime='text/html', content='eA==', name='bad.html')
        self.change(self.qc, 'media', mime='image/png', content='eA==', name='evidence.png')
        self.assertEqual(len(self.get()['checks'][self.key]['media']), 1)
        self.assertEqual(self.con.execute('SELECT count(*) FROM media').fetchone()[0], 1)

    def test_passwords(self):
        hashed=app.password_hash('a long test password')
        self.assertTrue(app.check_password('a long test password', hashed))
        self.assertFalse(app.check_password('wrong', hashed))

    def test_http_auth_and_shared_state(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = 'http://127.0.0.1:' + str(httpd.server_port)
        def request(path, data=None, cookie=None):
            headers = {'X-QG-Request': '1', 'Content-Type': 'application/json'}
            if cookie:
                headers['Cookie'] = cookie
            req = Request(base + path, json.dumps(data).encode() if data is not None else None, headers)
            return urlopen(req)
        try:
            with self.assertRaises(HTTPError) as error:
                request('/api/state')
            self.assertEqual(error.exception.code, 401)
            response = request('/api/login', {'username': 'admin', 'password': 'long-test-password'})
            cookie = response.headers['Set-Cookie'].split(';')[0]
            response.close()
            with request('/api/state', cookie=cookie) as result:
                self.assertEqual(json.load(result)['user']['role'], 'admin')
            with request('/api/action', {'action': 'create', 'inventory': 'HTTP-1', 'customer': 'Customer', 'model': 'ET 23', 'options': []}, cookie) as result:
                self.assertTrue(any(t['inventory'] == 'HTTP-1' for t in json.load(result)['trailers']))
            request('/api/logout', {}, cookie).close()
            with self.assertRaises(HTTPError):
                request('/api/state', cookie=cookie)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

if __name__ == '__main__':
    unittest.main()
