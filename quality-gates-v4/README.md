# Quality Gates v4 pilot

V4 adds server-side accounts and roles, a shared SQLite database, versioned operations and checklists, permanent control IDs, controlled POD transfers, multi-inspector QC activity, password-backed digital signature receipts, department assignment, production correction, QC verification, conditional gate passage, final approval, audit events, and authenticated evidence downloads. The existing v2/v3 files are retained.

## Run locally

Python 3.9 or later is sufficient. No packages are required.

```sh
cd "quality-gates-v4"
python3 server.py
```

Open http://127.0.0.1:8040 in your browser. On first launch, use the setup token printed in the terminal to create the administrator account. Passwords must contain at least 12 characters. Sign in, then use Team to create QC, production and supervisor accounts. Production users must have a department. No shared demo passwords are installed.

On macOS, `START-QUALITY-GATES.command` can be double-clicked to start the server and open the correct address. Do not open `index.html` directly with a `file://` address; the application needs the Python server for authentication and database access.

## Pilot acceptance scenario

1. Create a trailer as QC or supervisor. It receives a fixed checklist snapshot.
2. QC rejects a control, with an explanation and responsible department.
3. A production account in that department reports the completed correction.
4. QC accepts the correction or returns it to production with evidence.
5. One QC employee can start an operation and another can join and continue it; both identities remain visible in the operation activity. A supervisor or administrator controls transfer to another POD.
6. Quality acceptance actions require the signed-in person to re-enter their own password. The server records the signer, role, time, exact approval payload, SHA-256 digest and server HMAC seal.
7. A supervisor releases gates in order. Unchecked controls, holds and unresolved defects block release.
8. A supervisor can defer an issue to an unreleased later gate, with a reason. It remains open and blocks that target gate until resolved.
9. Final approval requires every gate released and every control passed or explicitly N/A. Modifying a control invalidates its gate release and subsequent releases. Approved records require supervisor reopening with a reason.

Checklist rules accept exact model names and optional equipment labels. Empty rules apply to all trailers. Changes create new versions and affect only new trailers. The initial 323 controls come from the full v3 file; published display numbers may still contain source numbering errors, but IDs are unique and do not depend on these labels. The newer Portrait workbook differs between its All and station tabs and has not been silently substituted as the authoritative checklist.

## Storage and collaboration

Data is stored in `data/quality-gates.sqlite3`. Media is stored separately from trailer JSON as database BLOBs, up to 10 MB per file. The pilot uses manual Refresh to retrieve other users' changes; conflicting writes return 409 instead of overwriting a newer record. There is no offline queue: a failed request must be retried after connectivity returns.

For multiple tablets, a deployment must route them to one server. The default listener is localhost. Before exposing a server beyond this computer, configure HTTPS and a trusted reverse proxy; session cookies use HttpOnly and SameSite and must additionally be made Secure at that deployment boundary. `--host` and `--port` are available for an operator-controlled deployment. This delivery does not publish a remote service.

Audit rows cannot be updated or deleted through the application or ordinary SQL because of database triggers. Signature receipts are sealed with `data/signing.key`; this key must be protected and backed up with the database. These receipts provide application-level identity confirmation and tamper evidence, but they are not a qualified certificate-based digital signature. An administrator with direct database/file access can still alter the database schema or key. Evidence uploads are type/size restricted but are not malware scanned. Storage quotas, object storage, automatic backups, password reset/account deactivation, live push updates and offline synchronization remain deployment work.

## Backup and restore

```sh
python3 server.py --backup /absolute/path/to/new-backup.sqlite3
```

This uses the SQLite backup API to include committed data and media consistently. Protect backups: they contain password hashes and session records as well as inspection data. Copy the adjacent `signing.key` into the same protected backup set; without it, historic signature seals cannot be independently revalidated. To verify or restore a backup without overwriting the current database, stop the server and start with `python3 server.py --db /absolute/path/to/backup.sqlite3`. Keep the original database until verification completes. The backup command refuses to overwrite an existing filename.

## Checks

```sh
python3 -m unittest -v test_server.py
node --check app.js
```

Tests use temporary databases. They cover unique IDs, template snapshots/applicability, role restrictions, correction and reinspection, deferred gates, final approval, stale-write rejection, audit immutability, password verification and media metadata. Browser visual and camera checks must also be completed on the actual pilot devices.
