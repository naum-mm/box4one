import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from app.url_safety import validate_url


class URLSafetyTests(unittest.TestCase):
    def test_proxy_check_sends_only_public_authority(self):
        connection = MagicMock()
        connection.getresponse.return_value.status = 204
        with patch.dict('os.environ', URL_VALIDATION_PROXY='http://download-proxy:8080'), \
                patch('app.url_safety.HTTPConnection', return_value=connection):
            self.assertEqual(validate_url('https://example.com/video?private=value'),
                             'https://example.com/video?private=value')
        connection.request.assert_called_once_with('CHECK', 'https://example.com', headers={'Connection': 'close'})
        connection.close.assert_called_once()

    def test_proxy_denial_and_dns_outage_are_distinct(self):
        for proxy_status, expected in ((403, 422), (422, 422), (503, 503)):
            with self.subTest(proxy_status=proxy_status):
                connection = MagicMock()
                connection.getresponse.return_value.status = proxy_status
                with patch.dict('os.environ', URL_VALIDATION_PROXY='http://download-proxy:8080'), \
                        patch('app.url_safety.HTTPConnection', return_value=connection), \
                        self.assertRaises(HTTPException) as ctx:
                    validate_url('https://example.com/')
                self.assertEqual(ctx.exception.status_code, expected)

    def test_local_dns_failure_is_not_invalid_user_input(self):
        with patch.dict('os.environ', URL_VALIDATION_PROXY=''), \
                patch('app.url_safety.getaddrinfo', side_effect=OSError('DNS unavailable')), \
                self.assertRaises(HTTPException) as ctx:
            validate_url('https://example.com/')
        self.assertEqual(ctx.exception.status_code, 503)
