"""Probe HTTP handling without including credentials in command lines or logs."""
import urllib.error
import urllib.request

try:
    urllib.request.urlopen('http://127.0.0.1:8081/', timeout=3).close()
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise SystemExit(1)
except OSError:
    raise SystemExit(1)
