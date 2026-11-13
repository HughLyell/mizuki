"""HTTP helpers built on the standard library.

The app uses worker threads for network I/O, then hands results back to the
GTK main loop. Keeping this module free of GI imports makes the source
adapters easy to test and easy to reuse from third-party plugins.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from . import config

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
DEFAULT_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "application/json,text/html;q=0.9,image/*;q=0.8,*/*;q=0.5",
    "Accept-Language": "en-US,en;q=0.8",
}


class NetworkError(Exception):
    """A friendly network error that can be shown in the UI."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class Cancelled(Exception):
    """Raised by :func:`download` when a cancellation event is set."""


def _merge_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    merged = dict(DEFAULT_HEADERS)
    if headers:
        merged.update({str(k): str(v) for k, v in headers.items()})
    return merged


def _url_with_params(url: str, params: Mapping[str, Any] | None) -> str:
    if not params:
        return url
    clean = {key: value for key, value in params.items() if value is not None}
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}{urllib.parse.urlencode(clean, doseq=True)}"


def _retry_delay(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait before retrying, honouring the server's Retry-After."""
    header = exc.headers.get("Retry-After") if exc.headers is not None else None
    if header:
        try:
            # Numeric form only; the HTTP-date form is rare enough to ignore.
            return max(0.0, min(float(header), 30.0))
        except ValueError:
            pass
    return 0.7 * (attempt + 1)


def _open(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 2,
):
    full_url = _url_with_params(url, params)
    request = urllib.request.Request(full_url, headers=_merge_headers(headers))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in (429, 500, 502, 503, 504) or attempt >= retries:
                break
            time.sleep(_retry_delay(exc, attempt))
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(0.7 * (attempt + 1))
        except TimeoutError as exc:
            last_error = exc
            if attempt >= retries:
                break
            time.sleep(0.7 * (attempt + 1))
    if isinstance(last_error, urllib.error.HTTPError):
        snippet = ""
        try:
            snippet = last_error.read(240).decode("utf-8", "replace").strip()
        except Exception:
            pass
        detail = f"HTTP {last_error.code}"
        if last_error.reason:
            detail += f": {last_error.reason}"
        if snippet and "html" not in snippet[:30].lower():
            detail += f" — {snippet[:160]}"
        raise NetworkError(detail, status=last_error.code) from last_error
    if isinstance(last_error, urllib.error.URLError):
        raise NetworkError(f"Network error: {last_error.reason}") from last_error
    if last_error is not None:
        raise NetworkError(f"Network error: {last_error}") from last_error
    raise NetworkError("Network request failed")


def get_bytes(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> bytes:
    response = _open(url, params=params, headers=headers, timeout=timeout)
    try:
        return response.read()
    except OSError as exc:  # includes socket timeouts while reading
        raise NetworkError(f"Connection interrupted: {exc}") from exc
    finally:
        response.close()


def get_text(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    text, _final_url = get_text_with_url(
        url, params=params, headers=headers, timeout=timeout
    )
    return text


def get_text_with_url(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[str, str]:
    response = _open(url, params=params, headers=headers, timeout=timeout)
    try:
        charset = response.headers.get_content_charset() or "utf-8"
        text = response.read().decode(charset, "replace")
        return text, response.geturl()
    except OSError as exc:
        raise NetworkError(f"Connection interrupted: {exc}") from exc
    finally:
        response.close()


def get_json(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> Any:
    raw = get_text(url, params=params, headers=headers, timeout=timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        preview = " ".join(raw[:120].split())
        raise NetworkError(f"The server returned an unexpected response ({preview!r})") from exc


def download(
    url: str,
    destination: Path,
    *,
    headers: Mapping[str, str] | None = None,
    progress: Callable[[float], None] | None = None,
    cancel=None,
    timeout: int = 60,
) -> Path:
    """Download ``url`` to ``destination`` atomically.

    ``progress`` is called from the worker thread with a fraction in [0, 1].
    ``cancel`` may be a :class:`threading.Event`; when set, the transfer stops
    and the partial file is removed.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    response = _open(url, headers=headers, timeout=timeout)
    try:
        total = int(response.headers.get("Content-Length") or 0)
        written = 0
        with open(temporary, "wb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
                if progress is not None:
                    fraction = written / total if total > 0 else min(0.95, written / (4 * 1024 * 1024))
                    try:
                        progress(fraction)
                    except Exception:
                        log.debug("Download progress callback failed", exc_info=True)
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        os.replace(temporary, destination)
        if progress is not None:
            try:
                progress(1.0)
            except Exception:
                pass
        return destination
    except Cancelled:
        temporary.unlink(missing_ok=True)
        raise
    except (OSError, NetworkError) as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, NetworkError):
            raise
        raise NetworkError(f"Download failed: {exc}") from exc
    finally:
        response.close()
