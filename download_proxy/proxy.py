"""No-auth proxy on the private worker network. DNS is checked and pinned per connection.

Only HTTP GET/HEAD and HTTPS CONNECT on standard ports; redirects and media URLs
must cross the same proxy. Worker has no network with an external route.
"""
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any((not ipaddress.ip_address(row[4][0]).is_global or ipaddress.ip_address(row[4][0]).is_multicast) for row in addresses):
        raise ValueError('Non-public destination')
    return addresses


async def pipe(reader, writer):
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()


async def handle(reader, writer):
    remote = None
    try:
        head = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 15)
        method, target, version = head.split(b'\r\n', 1)[0].decode('ascii').split()
        if method == 'CONNECT':
            parts = urlsplit('https://' + target)
            port = parts.port or 443
            if port != 443 or parts.path or parts.query or parts.fragment:
                raise ValueError()
        else:
            parts = urlsplit(target)
            expected_port = 443 if parts.scheme == 'https' else 80
            port = parts.port or expected_port
            if method == 'CHECK':
                if parts.scheme not in {'http', 'https'} or port != expected_port:
                    raise ValueError()
            elif method not in {'GET', 'HEAD'} or parts.scheme != 'http' or port != 80:
                raise ValueError()
        if not parts.hostname or parts.username or parts.password:
            raise ValueError()
        # Bound DNS wait; connect to numeric validated address, never resolve again.
        try:
            addresses = await asyncio.wait_for(asyncio.to_thread(public_addresses, parts.hostname, port), 10)
        except (OSError, asyncio.TimeoutError) as exc:
            if method == 'CHECK':
                permanent = isinstance(exc, socket.gaierror) and exc.errno in {socket.EAI_NONAME, getattr(socket, 'EAI_NODATA', socket.EAI_NONAME)}
                status = b'422 Unprocessable Content' if permanent else b'503 Service Unavailable'
                writer.write(b'HTTP/1.1 ' + status + b'\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                return
            raise
        if method == 'CHECK':
            writer.write(b'HTTP/1.1 204 No Content\r\nConnection: close\r\n\r\n')
            return
        upstream, remote = await asyncio.wait_for(asyncio.open_connection(addresses[0][4][0], port), 15)
        if method == 'CONNECT':
            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()
            tasks = [asyncio.create_task(pipe(reader, remote)), asyncio.create_task(pipe(upstream, writer))]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED, timeout=3600)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        else:
            headers = []
            for line in head.split(b'\r\n')[1:-2]:
                name = line.split(b':', 1)[0].lower()
                if name in {b'content-length', b'transfer-encoding', b'upgrade'}:
                    raise ValueError()
                if name not in {b'host', b'connection', b'proxy-connection', b'proxy-authorization'}:
                    headers.append(line)
            path = parts.path or '/'
            if parts.query:
                path += '?' + parts.query
            remote.write(f'{method} {path} HTTP/1.1\r\nHost: {parts.hostname}\r\nConnection: close\r\n'.encode()
                         + b'\r\n'.join(headers) + b'\r\n\r\n')
            await remote.drain()
            await asyncio.wait_for(pipe(upstream, writer), 3600)
    except (ValueError, OSError, UnicodeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
    finally:
        if remote:
            remote.close()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def main():
    semaphore = asyncio.Semaphore(32)
    async def bounded(reader, writer):
        if semaphore.locked():
            writer.close()
            return
        async with semaphore:
            await handle(reader, writer)
    server = await asyncio.start_server(bounded, '0.0.0.0', 8080, limit=16384)
    async with server:
        await server.serve_forever()


if __name__ == '__main__':
    asyncio.run(main())
