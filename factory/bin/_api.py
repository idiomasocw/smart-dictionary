"""A small client for the dictionary's factory API, standard library only.

The routine's environment needs nothing installed: these scripts run on any Python 3.9+.
- FACTORY_API is the server; the default is production.
- FACTORY_TOKEN (AUDIT_TOKEN for the audit's scripts) is sent as a bearer token when it's
  set. The cloud routines leave it unset: their environment's network secret adds the header
  after the request leaves the VM.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_API = "https://dictionary.onecultureworld.com"
FACTORY_DIR = Path(__file__).resolve().parents[1]
WORK = FACTORY_DIR / "work"

# (method, url, JSON body or None, headers) -> (HTTP status, parsed JSON body). Built-in
# generics only, which Python 3.9 evaluates at runtime; `X | None` would need 3.10.
Send = Callable[[str, str, Any, dict[str, str]], tuple[int, Any]]


class ApiError(Exception):
    """The server refused a call, or couldn't be reached."""


def urllib_send(method: str, url: str, body: Any, headers: dict[str, str]) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"title": raw.decode(errors="replace")[:300]}


class Api:
    def __init__(
        self,
        base: str | None = None,
        token: str | None = None,
        send: Send | None = None,
        token_env: str = "FACTORY_TOKEN",
    ) -> None:
        self.base = (base or os.environ.get("FACTORY_API") or DEFAULT_API).rstrip("/")
        self.token = token if token is not None else os.environ.get(token_env)
        self.send = send or urllib_send

    def call(self, method: str, path: str, body: Any = None, retries: int = 0) -> Any:
        """One API call. `retries` repeats it after network errors and 5xx answers, so use it
        only for calls that are safe to repeat."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "smart-dictionary-factory",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for attempt in range(retries + 1):
            last = attempt == retries
            try:
                status, payload = self.send(method, self.base + path, body, headers)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if last:
                    raise ApiError(f"{method} {path}: {error}") from error
                time.sleep(10 * (attempt + 1))
                continue
            if status >= 500 and not last:
                time.sleep(10 * (attempt + 1))
                continue
            if status >= 400:
                problem = payload if isinstance(payload, dict) else {}
                detail = problem.get("detail")
                raise ApiError(
                    f"{method} {path} answered {status} {problem.get('code', '')}: "
                    f"{problem.get('title', 'error')}" + (f" ({detail})" if detail else "")
                )
            return payload
        raise ApiError(f"{method} {path}: no answer")  # pragma: no cover


def utf8_output() -> None:
    """Print UTF-8. On Windows, Python writes to a pipe in the console's code page, which
    can't show IPA, so printing a server message about a pronunciation would crash."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
