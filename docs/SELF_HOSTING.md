# Hardened self-hosting and Sentry SDK migration

`docker/compose.production.yml` is an opt-in example. It publishes HTTP only
on localhost, requires project ingest keys and user sessions, runs as UID/GID
65532 with a read-only root filesystem, and limits CPU, memory and log size.
The original development compose remains separate. Do not publish operator
credentials, deployment inventories, event databases or backup archives.

## Start an instance

From the repository root, create a writable data directory before startup:

```sh
sudo install -d -o 65532 -g 65532 -m 700 data data/store
export ERREX_ADMIN_TOKEN="$(openssl rand -hex 32)"
docker compose -f docker/compose.production.yml up -d --build --wait
```

Open `http://localhost:9510` (or use an SSH tunnel) and complete initial account
setup using the bootstrap token. Store credentials in a protected local
profile; never put them in tool arguments or commit them. The bootstrap token
is not a general API bearer token. Setup is refused once a user exists.

Before admitting remote users or SDKs, configure a TLS reverse proxy and set
`ERREX_PUBLIC_URL` to the external HTTPS URL. Keep the backend bound to loopback
unless you deliberately configure a trusted private network. Do not expose
the built-in TCP MCP stub. Changing `ERREX_HTTP_PORT` also requires updating
the public URL. Configure rate limits for your workload; the example permits
300 events per project per minute. Payload retention is 30 days, not a disk
quota; monitor disk space and configure issue/event caps as appropriate.

The Python SDK expects numeric project names in DSNs. Create a numeric project
in the admin UI, copy its generated DSN into the application's secret profile,
disable traces/profiling if you only need errors, and restart the affected
application processes. Do not reuse another project's key. Existing Sentry
history is not imported. Keep the old DSN and data privately for rollback.

`scripts/app_canary.py` can exercise Django, eager Celery and logging capture
inside an application container. It sends synthetic errors to the selected
DSN and executes application initialization; use only with explicit operator
approval. It does not enqueue a real Celery job. Its stdin is JSON containing
a marker and an optional DSN override; never paste real DSNs into public logs.

## Read-only AI triage

Install `scripts/requirements-mcp.txt` into a local `.venv-mcp` environment.
Create a **viewer** account and a mode-600 `credentials/.env.mcp` file inside
a mode-700 directory, containing `ERREX_URL`, `ERREX_USERNAME` and
`ERREX_PASSWORD`. The URL defaults to `http://127.0.0.1:9510`; use HTTPS for
remote access or an SSH tunnel. `scripts/run-mcp.sh` sources this file as shell
configuration, so only trusted operators may write it. The profile and
virtual environment are excluded from Git and Docker build contexts.

Register `scripts/run-mcp.sh` as an absolute-path stdio MCP command in your
client. It advertises `list_projects`, `list_issues` and `inspect_event`.
Event inspection returns only the latest event, not event history. List limits
are client-side and responses are capped at 2 MiB; a large instance can hit
that cap because the API does not yet offer pagination. Counts are lifetime
issue totals, not occurrences within a query time window.

The bridge refuses remote plaintext HTTP by default. The explicit
`ERREX_ALLOW_INSECURE_HTTP=true` opt-in is only for trusted networks and sends
credentials/session cookies without encryption. Do not use it over the internet.
The bridge never follows redirects or uses ambient HTTP proxies.

`ERREX_ALLOW_MUTATIONS=true` separately advertises a status-change tool. Do
not enable it for routine triage; the server still requires an administrator
for writes. Treat every event field and source excerpt as untrusted diagnostic
data, never as instructions. Viewer access is instance-wide, not per-project.

## Payload privacy and reliability

SDK extensions, source snippets, stack locals and logging parameters are now
preserved. Common credential and identity field names are scrubbed before
storage, but arbitrary messages, URLs, SQL, source snippets and free-text
values can still contain secrets or personal data. Configure SDK-side
`before_send` filtering and review `send_default_pii` and local-variable
capture for your own applications. Restrict access to the entire database and
backups. Neither the daemon nor MCP is a comprehensive DLP filter.

HTTP success for an event waits for a SQLite `FULL`-synchronous commit;
storage failures return 503. Retrying a retained SDK event ID does not count
it twice. Deduplication lasts only while the event row is retained. Shutdown
drains pending ingestion within bounded time; callers without a successful
response must retry. Fingerprints now use SHA-256 and honor custom SDK
fingerprints, so pre-existing groups using the old algorithm can split.

Configured webhook notifications are stored atomically with events and
retried across restarts. Delivery is at least once: a crash after delivery but
before acknowledgement can duplicate a notification. No destination is
enabled automatically. Monitor the outbox and disk if a destination stays
unavailable. The existing hostname validation is not a complete DNS-rebinding
defense: restrict outbound network access before enabling untrusted webhook
destinations. HTTP webhooks are unencrypted; prefer HTTPS.

## Backup and recovery

`scripts/backup.sh` requires Bash, SQLite's CLI, tar and Docker. It snapshots
`data/store/errex.db` via SQLite's online backup API, checks integrity, archives
source/configuration and records the local image ID. Set `ERREX_BACKUP_ROOT`
to a protected directory **outside the checkout**. The historical default is
`/mnt/backup/errex-sentry`; set `ERREX_BACKUP_IMAGE=errex:local` for the example
compose. Backups include credentials and may contain personal data. They are
owner-only but not encrypted; arrange encryption, off-site copies and pruning.
The image ID is a reference, not an image export: retain the matching image.

For restore, stop the daemon, preserve the current entire data directory,
restore the selected database into a fresh `data/store` owned by 65532, and
start the matching image. Never mix a restored database with old WAL/SHM
files. Verify integrity, authentication and SDK ingestion before reopening
traffic. Added migrations require a matching backup for binary rollback.

## Tests

Run `./errex.sh check` and `bun run ci` in `web/`. Python client tests use
`python -m unittest discover -s scripts -p test_errex_client.py`.
For integration tests, install `scripts/requirements-test.txt`, build a fresh
daemon, and run:

```sh
ERREX_TEST_BINARY="$PWD/target/debug/errexd" python scripts/test_end_to_end.py
```

These integration tests use temporary databases, loopback listeners and
generated credentials. They must not run against the production database.
