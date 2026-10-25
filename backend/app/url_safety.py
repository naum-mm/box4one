import ipaddress
import socket
from socket import getaddrinfo
from urllib.parse import urlsplit
from fastapi import HTTPException


def validate_url(url: str):
    try:
        parts = urlsplit(url)
        if (len(url) > 2048 or any(ord(c) <= 32 for c in url) or parts.scheme not in {'http', 'https'}
                or not parts.hostname or parts.username or parts.password
                or parts.port not in {None, 80, 443}):
            raise ValueError()
        addresses = getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
        if not addresses or any((not ipaddress.ip_address(a[4][0]).is_global or ipaddress.ip_address(a[4][0]).is_multicast) for a in addresses):
            raise ValueError()
    except (ValueError, OSError):
        raise HTTPException(422, 'Недопустимая или недоступная ссылка') from None
    return url
