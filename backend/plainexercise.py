"""Fetch the exercise page HTML and capture the response body.

If the site returns a Cloudflare challenge, export the browser's Cookie header
from DevTools and set it in COOKIE_HEADER before running this script.
"""

from __future__ import annotations

import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


def get_page_content(url: str, extra_headers: dict[str, str] | None = None) -> tuple[int, str, str, str]:
    headers = dict(DEFAULT_HEADERS)
    cookie_header = os.environ.get("COOKIE_HEADER", "").strip()
    if cookie_header:
        headers["Cookie"] = cookie_header
    if extra_headers:
        headers.update(extra_headers)

    request = Request(url, headers=headers)

    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            return (
                response.status,
                response.geturl(),
                response.headers.get_content_type(),
                body.decode(charset, errors="replace"),
            )
    except HTTPError as error:
        body = error.read()
        charset = error.headers.get_content_charset() or "utf-8"
        return (
            error.code,
            error.geturl(),
            error.headers.get_content_type() if error.headers else "unknown",
            body.decode(charset, errors="replace"),
        )
    except URLError as error:
        raise RuntimeError(f"Network error: {error.reason}") from error