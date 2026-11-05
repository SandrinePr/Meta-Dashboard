"""Remove Meta access tokens from text, URLs, and JSON before they are stored or logged."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_QUERY_NAMES = {
    "access_token",
    "fb_exchange_token",
    "client_secret",
    "app_secret",
    "appsecret_proof",
    "token",
}
_EAA = re.compile(r"EAA[A-Za-z0-9]{20,}")
_INLINE_SECRET = re.compile(
    r"(?i)(?:access_token|fb_exchange_token|client_secret|app_secret|appsecret_proof)=[^&\s\"']*"
)


def redact_text(value: str | None) -> str | None:
    """Drop secret query parameters and embedded Meta tokens. Keep the rest of the text."""
    if value is None or not isinstance(value, str) or value == "":
        return value
    cleaned = _strip_url_secrets(value) if "://" in value else value
    cleaned = _INLINE_SECRET.sub("", cleaned)
    cleaned = _EAA.sub("", cleaned)
    return cleaned


def redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_json(item) for key, item in value.items()}
    return value


def redact_json_text(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return redact_text(raw)
    return json.dumps(redact_json(parsed), ensure_ascii=False)


def scrub_connection(conn) -> int:
    """Rewrite stored text so tokens are gone. Returns the number of cells changed."""
    tables = [
        row[0]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
              AND name NOT LIKE 'search_index%'
            """
        )
    ]
    changed = 0
    for table in tables:
        columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        for column in columns:
            rows = conn.execute(
                f'SELECT rowid, "{column}" FROM "{table}" WHERE typeof("{column}") = \'text\''
            ).fetchall()
            for rowid, value in rows:
                cleaned = redact_json_text(value)
                if cleaned != value:
                    conn.execute(
                        f'UPDATE "{table}" SET "{column}" = ? WHERE rowid = ?',
                        (cleaned, rowid),
                    )
                    changed += 1
    return changed


def _strip_url_secrets(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme or not parts.query:
        return value
    kept = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _SECRET_QUERY_NAMES and "token" not in key.lower()
        and _EAA.search(item) is None
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))
