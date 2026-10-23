import os
import secrets

from fastapi import Header, HTTPException, status


WORKER_API_TOKEN = os.environ["WORKER_API_TOKEN"]


def verify_worker_token(
    x_worker_token: str = Header(
        ...,
        alias="X-Worker-Token",
    ),
):
    if not secrets.compare_digest(
        x_worker_token,
        WORKER_API_TOKEN,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker token",
        )


DOWNLOAD_ACCESS_WORD = os.environ["DOWNLOAD_ACCESS_WORD"]

if not DOWNLOAD_ACCESS_WORD:
    raise RuntimeError("DOWNLOAD_ACCESS_WORD is not configured")


import base64
import hashlib
import hmac
import json
import time
from fastapi import Request

QUEUE_ACCESS_WORD = os.environ["QUEUE_ACCESS_WORD"]
COOKIE_NAME = "__Host-box4one-access"
ACCESS_SECONDS = 90 * 24 * 60 * 60
SIGNING_KEY = hashlib.sha256((WORKER_API_TOKEN + "\0" + DOWNLOAD_ACCESS_WORD + "\0" + QUEUE_ACCESS_WORD).encode()).digest()


def word_matches(value, expected):
    return value is not None and secrets.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


def make_access_token(mode):
    data = json.dumps({"mode": mode, "expires": int(time.time()) + ACCESS_SECONDS, "nonce": secrets.token_hex(16)}, separators=(",", ":")).encode()
    payload = base64.urlsafe_b64encode(data).decode().rstrip("=")
    signature = hmac.new(SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def webapp_mode(request):
    if request.headers.get("X-Web-App") != "1":
        return None
    token = request.cookies.get(COOKIE_NAME, "")
    try:
        payload, signature = token.split(".")
        expected = hmac.new(SIGNING_KEY, payload.encode(), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(signature, expected):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data["expires"] <= time.time() or data["mode"] not in {"download", "queue"}:
            return None
        return data["mode"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        return None


def verify_download_access(request: Request, x_download_access: str | None = Header(default=None, alias="X-Download-Access")):
    if (word_matches(x_download_access, DOWNLOAD_ACCESS_WORD)
            or word_matches(x_download_access, QUEUE_ACCESS_WORD)
            or webapp_mode(request) in {"download", "queue"}):
        return
    raise HTTPException(status_code=403, detail="Access denied")


def verify_queue_access(request: Request, x_queue_access: str | None = Header(default=None, alias="X-Queue-Access")):
    if word_matches(x_queue_access, QUEUE_ACCESS_WORD) or webapp_mode(request) == "queue":
        return
    raise HTTPException(status_code=403, detail="Access denied")
