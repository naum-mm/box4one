import ipaddress
import os
import socket
from http.client import HTTPConnection, HTTPException as HTTPProtocolError
from socket import getaddrinfo
from urllib.parse import urlsplit
from fastapi import HTTPException


def check_proxy_destination(parts, proxy_url):
    """Resolve at the egress proxy; the backend itself stays on the private network."""
    proxy = urlsplit(proxy_url)
    if proxy.scheme != 'http' or not proxy.hostname or proxy.username or proxy.password or proxy.path not in {'', '/'}:
        raise HTTPException(503, 'Проверка ссылки временно недоступна')
    host = parts.hostname.encode('idna').decode('ascii')
    authority = f'[{host}]' if ':' in host else host
    if parts.port is not None:
        authority += ':' + str(parts.port)
    connection = HTTPConnection(proxy.hostname, proxy.port or 80, timeout=12)
    try:
        # Only authority is sent: no source path, query parameters or user secrets.
        connection.request('CHECK', f'{parts.scheme}://{authority}', headers={'Connection': 'close'})
        response = connection.getresponse()
        if response.status in {403, 422}:
            raise HTTPException(422, 'Недопустимая ссылка')
        if response.status != 204:
            raise HTTPException(503, 'Проверка ссылки временно недоступна')
    except (OSError, HTTPProtocolError):
        raise HTTPException(503, 'Проверка ссылки временно недоступна') from None
    finally:
        connection.close()


def validate_url(url: str):
    try:
        parts = urlsplit(url)
        if (len(url) > 2048 or any(ord(c) <= 32 for c in url) or parts.scheme not in {'http', 'https'}
                or not parts.hostname or parts.username or parts.password
                or parts.port not in {None, 80 if parts.scheme == 'http' else 443}):
            raise ValueError()
        proxy_url = os.getenv('URL_VALIDATION_PROXY', '')
        if proxy_url:
            check_proxy_destination(parts, proxy_url)
        else:
            addresses = getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80), type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global or ipaddress.ip_address(a[4][0]).is_multicast for a in addresses):
                raise ValueError()
    except (ValueError, UnicodeError):
        raise HTTPException(422, 'Недопустимая ссылка') from None
    except OSError:
        # DNS/network outages are not malformed user input.
        raise HTTPException(503, 'Проверка ссылки временно недоступна') from None
    return url
