"""Authenticated, bounded Errex API client for operator tools."""
import json
import ipaddress
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ErrexClient:
    def __init__(self, base_url, username, password, *, allow_insecure_http=False):
        url = urllib.parse.urlsplit(base_url)
        if not username or not password:
            raise ValueError('Errex username and password are required')
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Use a trusted HTTP(S) Errex URL without credentials')
        try:
            loopback = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            loopback = url.hostname == 'localhost'
        if url.scheme == 'http' and not loopback and not allow_insecure_http:
            raise ValueError('Remote Errex credentials require HTTPS; use an SSH tunnel or explicitly opt into trusted-network HTTP')
        self.base_url = base_url.rstrip('/')
        self.username, self.password = username, password
        self.cookie = None
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def _send(self, path, method='GET', payload=None):
        if not path.startswith('/api/') or path.startswith('//'):
            raise ValueError('Only Errex API paths are allowed')
        headers = {'Content-Type': 'application/json'}
        if self.cookie:
            headers['Cookie'] = self.cookie
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        with self.http.open(request, timeout=15) as response:
            body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError('API response exceeds 2 MiB; narrow the project selection')
            return (json.loads(body) if body else None), response.headers

    def login(self):
        _, headers = self._send('/api/auth/login', 'POST', {'username': self.username, 'password': self.password})
        self.cookie = headers.get('Set-Cookie', '').split(';')[0]
        if not self.cookie:
            raise RuntimeError('Errex login did not issue a session')

    def request(self, path, method='GET', payload=None):
        if not self.cookie:
            self.login()
        try:
            return self._send(path, method, payload)[0]
        except urllib.error.HTTPError as error:
            if error.code != 401:
                raise
            self.login()
            return self._send(path, method, payload)[0]

    def list_issues(self, project, limit=25):
        if not 1 <= limit <= 100:
            raise ValueError('limit must be 1..100')
        return self.request('/api/issues?' + urllib.parse.urlencode({'project': project}))[:limit]

    def get_event(self, issue_id):
        if issue_id <= 0:
            raise ValueError('issue_id must be positive')
        return self.request(f'/api/issues/{issue_id}/event')

    def set_status(self, issue_id, status):
        if issue_id <= 0 or status not in ('unresolved', 'resolved', 'muted', 'ignored'):
            raise ValueError('Invalid issue ID or status')
        return self.request(f'/api/issues/{issue_id}/status', 'PUT', {'status': status})
