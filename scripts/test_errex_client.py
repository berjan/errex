import unittest
from unittest.mock import Mock
from errex_client import ErrexClient


class ClientTests(unittest.TestCase):
    def test_requires_credentials(self):
        with self.assertRaises(ValueError):
            ErrexClient('http://localhost:9510', '', '')

    def test_no_credentials_in_url(self):
        with self.assertRaises(ValueError):
            ErrexClient('http://user:password@localhost', 'user', 'password')

    def test_remote_credentials_require_tls(self):
        for url in ('http://example.com', 'http://192.0.2.1', 'http://localhost.example.com'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                ErrexClient(url, 'user', 'password')

    def test_loopback_and_https_are_allowed(self):
        for url in ('http://localhost:9510', 'http://127.0.0.1:9510',
                    'http://[::1]:9510', 'https://errors.example.com'):
            ErrexClient(url, 'user', 'password')

    def test_plaintext_remote_requires_explicit_opt_in(self):
        ErrexClient('http://192.0.2.1:9510', 'user', 'password', allow_insecure_http=True)

    def test_redirects_are_not_followed(self):
        from errex_client import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))

    def test_response_size_is_bounded(self):
        client = ErrexClient('http://localhost:9510', 'user', 'password')
        response = Mock()
        response.read.return_value = b'x' * (2 * 1024 * 1024 + 1)
        client.http = Mock()
        client.http.open.return_value.__enter__ = Mock(return_value=response)
        client.http.open.return_value.__exit__ = Mock(return_value=False)
        with self.assertRaises(ValueError):
            client._send('/api/projects')
        response.read.assert_called_once_with(2 * 1024 * 1024 + 1)

    def test_status_is_validated_before_request(self):
        client = ErrexClient('http://localhost:9510', 'user', 'password')
        client.request = Mock()
        with self.assertRaises(ValueError):
            client.set_status(1, 'delete')
        client.request.assert_not_called()

    def test_bounded_issue_list(self):
        client = ErrexClient('http://localhost:9510', 'user', 'password')
        client.request = Mock(return_value=[{'id': i} for i in range(10)])
        self.assertEqual(len(client.list_issues('123', 2)), 2)
        client.request.assert_called_once_with('/api/issues?project=123')


if __name__ == '__main__':
    unittest.main()
