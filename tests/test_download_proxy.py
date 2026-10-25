import asyncio
import unittest
from unittest.mock import patch
from download_proxy.proxy import public_addresses, handle


class ProxyTests(unittest.TestCase):
    def test_reject_private_loopback_metadata_multicast_ipv6_and_mixed_dns(self):
        for address in ('127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.1.1', '169.254.169.254',
                        '0.0.0.0', '::1', 'fe80::1', 'fc00::1', '::ffff:127.0.0.1', '224.0.0.1'):
            with self.subTest(address=address), patch('download_proxy.proxy.socket.getaddrinfo',
                return_value=[(2, 1, 6, '', (address, 443))]):
                with self.assertRaises(ValueError):
                    public_addresses('example.com', 443)
        with patch('download_proxy.proxy.socket.getaddrinfo', return_value=[
            (2, 1, 6, '', ('93.184.216.34', 443)), (2, 1, 6, '', ('10.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                public_addresses('example.com', 443)

    def test_public_dns(self):
        with patch('download_proxy.proxy.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]):
            self.assertEqual(public_addresses('example.com', 443)[0][4][0], '93.184.216.34')


class ProxyNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = await asyncio.start_server(handle, '127.0.0.1', 0, limit=16384)
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def request(self, data):
        reader, writer = await asyncio.open_connection('127.0.0.1', self.port)
        writer.write(data); await writer.drain()
        result = await reader.read()
        writer.close(); await writer.wait_closed()
        return result

    async def test_redirect_target_private_is_rejected(self):
        for target in (b'http://127.0.0.1/admin', b'http://169.254.169.254/latest/meta-data/'):
            response = await self.request(b'GET ' + target + b' HTTP/1.1\r\nHost: ignored\r\n\r\n')
            self.assertIn(b'403 Forbidden', response)

    async def test_connect_internal_and_nonstandard_ports_rejected(self):
        for target in (b'127.0.0.1:443', b'example.com:3306', b'[::1]:443'):
            response = await self.request(b'CONNECT ' + target + b' HTTP/1.1\r\n\r\n')
            self.assertIn(b'403 Forbidden', response)
