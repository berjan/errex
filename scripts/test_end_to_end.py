"""Real SDK, authenticated MCP and restore tests against a disposable daemon.

ERREX_TEST_BINARY must point to a freshly built errexd. Never uses production data.
"""
import asyncio
import json
import http.server
import logging
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
import uuid

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from errex_client import ErrexClient


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='errex-e2e-')
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            cls.port = s.getsockname()[1]
        cls.base = f'http://127.0.0.1:{cls.port}'
        cls.token, cls.password = uuid.uuid4().hex, uuid.uuid4().hex
        cls.env = dict(os.environ, ERREX_DATA_DIR=cls.temp.name, ERREX_PORT=str(cls.port),
                       ERREX_MCP_PORT='0', ERREX_HOST='127.0.0.1', ERREX_REQUIRE_AUTH='true',
                       ERREX_ADMIN_TOKEN=cls.token, ERREX_LOG_LEVEL='error', ERREX_PUBLIC_URL=cls.base)
        cls.binary = os.environ['ERREX_TEST_BINARY']
        cls.start()
        cls.client = ErrexClient(cls.base, 'review', cls.password)
        cls.client._send('/api/auth/setup', 'POST', {'token': cls.token, 'username': 'review', 'password': cls.password})
        output = subprocess.check_output([cls.binary, 'project', 'add', '123'], env=cls.env, text=True)
        fields = dict(line.split(':', 1) for line in output.splitlines() if ':' in line)
        cls.dsn, cls.key = fields['dsn'].strip(), fields['token'].strip()

    @classmethod
    def start(cls):
        cls.process = subprocess.Popen([cls.binary], env=cls.env, stdout=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urllib.request.urlopen(cls.base + '/health', timeout=.2):
                    return
            except OSError:
                if cls.process.poll() is not None:
                    raise RuntimeError('Test daemon exited')
                time.sleep(.05)
        raise RuntimeError('Test daemon did not start')

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=30)
        cls.temp.cleanup()

    def test_01_real_sdk_exceptions_logging_and_fingerprints(self):
        with sentry_sdk.init(dsn=self.dsn, default_integrations=False,
                             integrations=[LoggingIntegration(level=None, event_level=logging.ERROR)],
                             traces_sample_rate=0, send_default_pii=False):
            for fingerprint in ('sdk-A', 'sdk-B'):
                with sentry_sdk.new_scope() as scope:
                    scope.fingerprint = [fingerprint]
                    try:
                        job_id = 'synthetic-job'
                        password = 'synthetic-secret'
                        raise ValueError('Migration synthetic exception')
                    except ValueError:
                        sentry_sdk.capture_exception()
            logging.getLogger('errex-migration').error('Synthetic logging %s', 'message')
            sentry_sdk.flush(timeout=10)
        issues = self.client.list_issues('123')
        self.assertEqual(len(issues), 3)
        logging_issues = [issue for issue in issues if issue['title'].startswith('Synthetic logging ')]
        self.assertEqual(len(logging_issues), 1)
        entry = self.client.get_event(logging_issues[0]['id'])['payload']['logentry']
        self.assertEqual(entry['message'], 'Synthetic logging %s')
        self.assertEqual(entry['params'], ['message'])
        # SDK 2.5 sends only the template and params; newer versions also send
        # formatted. Keep both losslessly, without guessing printf semantics.
        self.assertEqual(logging_issues[0]['title'], entry.get('formatted', entry['message']))
        for issue in issues:
            if 'ValueError' in issue['title']:
                payload = self.client.get_event(issue['id'])['payload']
                frame = payload['exception']['values'][-1]['stacktrace']['frames'][-1]
                self.assertIn('context_line', frame)
                self.assertEqual(frame['vars']['password'], '[Filtered]')
                self.assertIn('synthetic-job', frame['vars']['job_id'])

    def test_02_authenticated_mcp(self):
        async def check():
            params = StdioServerParameters(command=sys.executable,
                args=[str(Path(__file__).with_name('errex_mcp.py'))],
                # gitleaks:allow -- self.password is generated for this disposable daemon.
                env=dict(os.environ, ERREX_URL=self.base, ERREX_USERNAME='review', ERREX_PASSWORD=self.password, ERREX_ALLOW_MUTATIONS='true'))  # gitleaks:allow
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, {'list_projects', 'list_issues', 'inspect_event', 'set_issue_status'})
                    result = await session.call_tool('list_issues', {'project': '123'})
                    self.assertFalse(result.isError)
                    issue = self.client.list_issues('123')[0]
                    event = await session.call_tool('inspect_event', {'issue_id': issue['id']})
                    self.assertFalse(event.isError)
                    result = await session.call_tool('set_issue_status', {'issue_id': issue['id'], 'status': 'resolved'})
                    self.assertFalse(result.isError)
                    self.assertEqual(self.client.list_issues('123')[0]['status'], 'resolved')
        asyncio.run(check())

    def test_02_mcp_defaults_to_read_only(self):
        async def check():
            params = StdioServerParameters(command=sys.executable,
                args=[str(Path(__file__).with_name('errex_mcp.py'))],
                # gitleaks:allow -- self.password is generated for this disposable daemon.
                env=dict(os.environ, ERREX_URL=self.base, ERREX_USERNAME='review', ERREX_PASSWORD=self.password, ERREX_ALLOW_MUTATIONS='false'))  # gitleaks:allow
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({t.name for t in tools.tools}, {'list_projects', 'list_issues', 'inspect_event'})
        asyncio.run(check())

    def test_02_viewer_cannot_mutate_even_when_tool_is_enabled(self):
        password = uuid.uuid4().hex
        self.client.request('/api/admin/users', 'POST', {
            'username': 'triage-viewer', 'password': password, 'role': 'viewer'})
        async def check():
            env = dict(os.environ, ERREX_URL=self.base, ERREX_USERNAME='triage-viewer',
                       ERREX_ALLOW_MUTATIONS='true')
            env['ERREX_PASSWORD'] = password
            params = StdioServerParameters(command=sys.executable,
                args=[str(Path(__file__).with_name('errex_mcp.py'))], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    projects = await session.call_tool('list_projects')
                    self.assertFalse(projects.isError)
                    issue = self.client.list_issues('123')[0]
                    result = await session.call_tool('set_issue_status', {
                        'issue_id': issue['id'], 'status': 'muted'})
                    self.assertTrue(result.isError)
                    self.assertEqual(self.client.list_issues('123')[0]['status'], issue['status'])
        asyncio.run(check())

    def test_03_restart_and_backup_restore(self):
        before = self.client.list_issues('123')
        self.process.terminate()
        self.process.wait(timeout=30)
        self.start()
        self.assertEqual(self.client.list_issues('123'), before)
        source = sqlite3.connect(Path(self.temp.name) / 'errex.db')
        backup_path = Path(self.temp.name) / 'restored.db'
        backup = sqlite3.connect(backup_path)
        source.backup(backup)
        self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(backup.execute('SELECT count(*) FROM events').fetchone()[0], 3)
        backup.close()
        source.close()

    def send_event(self, event_id, message):
        payload = json.dumps({'event_id': event_id, 'message': message}).encode()
        raw = b'{}\n{"type":"event"}\n' + payload + b'\n'
        req = urllib.request.Request(self.base + '/api/123/envelope/', data=raw,
            headers={'X-Sentry-Auth': f'Sentry sentry_key={self.key}, sentry_version=7'})
        with urllib.request.urlopen(req, timeout=15) as response:
            self.assertEqual(response.status, 200)

    def test_04_storage_failure_and_retry(self):
        db = sqlite3.connect(Path(self.temp.name) / 'errex.db')
        event_id = uuid.uuid4().hex
        db.execute("CREATE TRIGGER fail_ingest BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'synthetic disk failure'); END")
        db.commit()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.send_event(event_id, 'fault injection')
        self.assertEqual(failure.exception.code, 503)
        db.execute('DROP TRIGGER fail_ingest')
        db.commit()
        self.send_event(event_id, 'fault injection')
        self.send_event(event_id, 'fault injection')
        self.assertEqual(db.execute('SELECT event_count FROM issues WHERE title=?', ('fault injection',)).fetchone()[0], 1)
        db.close()

    def test_05_alert_retries_after_restart(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            code = 503
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                self.send_response(self.code)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        db = sqlite3.connect(Path(self.temp.name) / 'errex.db')
        # Test-only DB configuration permits the loopback mock receiver.
        db.execute('UPDATE projects SET webhook_url=? WHERE name=?', (f'http://127.0.0.1:{server.server_port}/hook', '123'))
        db.commit()
        try:
            self.send_event(uuid.uuid4().hex, 'durable alert test')
            for _ in range(100):
                if db.execute('SELECT last_webhook_status FROM projects WHERE name="123"').fetchone()[0] == 503:
                    break
                time.sleep(.05)
            self.assertEqual(db.execute('SELECT count(*) FROM webhook_outbox').fetchone()[0], 1)
            self.process.terminate()
            self.process.wait(timeout=30)
            Handler.code = 200
            self.start()
            for _ in range(240):
                if db.execute('SELECT count(*) FROM webhook_outbox').fetchone()[0] == 0:
                    break
                time.sleep(.05)
            self.assertEqual(db.execute('SELECT count(*) FROM webhook_outbox').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT last_webhook_status FROM projects WHERE name="123"').fetchone()[0], 200)
        finally:
            server.shutdown()
            server.server_close()
            db.close()


if __name__ == '__main__':
    unittest.main()
