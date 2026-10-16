"""Instagram Business Discovery helpers (no token logging)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator
from urllib.parse import parse_qs, urlparse

from config import get_settings
from meta.client import MetaClient, MetaRequestError, MetaRequestMeta

# Default page size. Configurable via DISCOVERY_MEDIA_PAGE_SIZE; do not assume larger is cheaper.
MEDIA_PAGE_LIMIT = 25

# Fields required for comparison metrics/classification/links. Caption and media_count
# are intentionally omitted from Business Discovery to reduce total_time cost.
MEDIA_FIELDS = (
    "id",
    "media_type",
    "media_product_type",
    "permalink",
    "timestamp",
    "like_count",
    "comments_count",
)

ACCOUNT_FIELDS = (
    "id",
    "username",
    "name",
    "followers_count",
)

_TOKENISH = re.compile(r"(access_token=)[^&\s]+", re.IGNORECASE)
_LONG_HEX = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")


def safe_error_text(exc: BaseException) -> str:
    text = str(exc)
    text = _TOKENISH.sub(r"\1[redacted]", text)
    text = _LONG_HEX.sub("[redacted]", text)
    return text


def parse_meta_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None


def to_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def optional_int(value: Any) -> int | None:
    """Keep missing values as None. Preserve real zeros."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def configured_media_page_limit() -> int:
    """Page size from settings, falling back to MEDIA_PAGE_LIMIT."""
    try:
        return max(1, int(get_settings().discovery_media_page_size))
    except Exception:  # noqa: BLE001
        return MEDIA_PAGE_LIMIT


def business_discovery_fields(
    username: str,
    *,
    after: str | None = None,
    media_limit: int = MEDIA_PAGE_LIMIT,
    include_media: bool = True,
    include_account: bool = True,
) -> str:
    """Build nested Business Discovery fields.

    After the first page, callers should set include_account=False so account
    fields are not re-requested on every media page.
    """
    parts: list[str] = []
    if include_account:
        parts.append(",".join(ACCOUNT_FIELDS))
    if include_media:
        media_inner = ",".join(MEDIA_FIELDS)
        if after:
            parts.append(f"media.limit({media_limit}).after({after}){{{media_inner}}}")
        else:
            parts.append(f"media.limit({media_limit}){{{media_inner}}}")
    if not parts:
        raise ValueError("Business Discovery query needs account and/or media fields.")
    return f"business_discovery.username({username}){{{','.join(parts)}}}"


def media_after_cursor(media_block: dict[str, Any] | None) -> str | None:
    if not isinstance(media_block, dict):
        return None
    paging = media_block.get("paging")
    if not isinstance(paging, dict):
        return None
    cursors = paging.get("cursors")
    if isinstance(cursors, dict) and cursors.get("after"):
        return str(cursors["after"])
    next_url = paging.get("next")
    if isinstance(next_url, str) and next_url:
        query = parse_qs(urlparse(next_url).query)
        after = query.get("after", [None])[0]
        return str(after) if after else None
    return None


def fetch_own_account_profile(client: MetaClient, ig_user_id: str) -> dict[str, Any]:
    # Own-account path (not Business Discovery); media_count remains useful here.
    return client.get_json(
        ig_user_id,
        params={"fields": "id,username,name,followers_count,media_count"},
        use_page_token=True,
    )


def fetch_business_discovery(
    client: MetaClient,
    host_ig_id: str,
    username: str,
    *,
    after: str | None = None,
    media_limit: int = MEDIA_PAGE_LIMIT,
    include_media: bool = True,
    include_account: bool = True,
) -> tuple[dict[str, Any], MetaRequestMeta]:
    payload, request_meta = client.get_json_with_meta(
        host_ig_id,
        params={
            "fields": business_discovery_fields(
                username,
                after=after,
                media_limit=media_limit,
                include_media=include_media,
                include_account=include_account,
            )
        },
        use_page_token=True,
    )
    discovery = payload.get("business_discovery")
    if not isinstance(discovery, dict):
        raise MetaRequestError("business_discovery ontbreekt in de API-respons.")
    return discovery, request_meta


@dataclass(slots=True)
class DiscoveryPageResult:
    """One Business Discovery media page plus request cost metadata."""

    page_index: int
    page_size: int
    posts: list[dict[str, Any]]
    posts_in_period: list[dict[str, Any]]
    account_payload: dict[str, Any]
    cursor_before: str | None
    cursor_after: str | None
    reached_cutoff: bool
    request_meta: MetaRequestMeta
    stopped_reason: str | None = None


@dataclass(slots=True)
class DiscoveryFetchState:
    """Mutable progress for resumable discovery pagination."""

    account_payload: dict[str, Any] = field(default_factory=dict)
    pages: int = 0
    posts_collected: int = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    cursor_after: str | None = None
    seen_ids: set[str] = field(default_factory=set)
    stopped_reason: str = "voltooid"
    is_complete: bool = False


def _extract_account_payload(discovery: dict[str, Any]) -> dict[str, Any]:
    return {
        key: discovery.get(key)
        for key in ("id", "username", "name", "followers_count", "media_count")
        if key in discovery or key in {"id", "username", "name", "followers_count"}
    }


def iter_discovery_media_pages(
    client: MetaClient,
    host_ig_id: str,
    username: str,
    *,
    cutoff_utc: datetime,
    period_end: datetime | None = None,
    media_limit: int | None = None,
    max_pages: int = 40,
    resume_after: str | None = None,
    known_account: dict[str, Any] | None = None,
    known_seen_ids: set[str] | None = None,
) -> Iterator[DiscoveryPageResult]:
    """Yield media pages until cutoff/end/max, supporting resume + ID dedupe.

    Does not issue per-post requests. Account fields are requested only when
    not already known (first page or missing checkpoint account payload).
    """
    limit = media_limit if media_limit is not None else configured_media_page_limit()
    state = DiscoveryFetchState(
        account_payload=dict(known_account or {}),
        cursor_after=resume_after,
        seen_ids=set(known_seen_ids or ()),
    )

    while state.pages < max_pages:
        cursor_before = state.cursor_after
        include_account = not bool(state.account_payload.get("id"))
        discovery, request_meta = fetch_business_discovery(
            client,
            host_ig_id,
            username,
            after=cursor_before,
            media_limit=limit,
            include_account=include_account,
        )
        if include_account or not state.account_payload:
            extracted = _extract_account_payload(discovery)
            # Prefer non-empty values from this response.
            merged = dict(state.account_payload)
            for key, value in extracted.items():
                if value is not None:
                    merged[key] = value
            state.account_payload = merged

        media_block = discovery.get("media")
        if not isinstance(media_block, dict):
            state.stopped_reason = "geen media-blok"
            state.is_complete = False
            yield DiscoveryPageResult(
                page_index=state.pages + 1,
                page_size=limit,
                posts=[],
                posts_in_period=[],
                account_payload=state.account_payload,
                cursor_before=cursor_before,
                cursor_after=None,
                reached_cutoff=False,
                request_meta=request_meta,
                stopped_reason=state.stopped_reason,
            )
            return

        batch = media_block.get("data")
        if not isinstance(batch, list) or not batch:
            state.stopped_reason = "lege media-pagina"
            state.is_complete = True
            yield DiscoveryPageResult(
                page_index=state.pages + 1,
                page_size=limit,
                posts=[],
                posts_in_period=[],
                account_payload=state.account_payload,
                cursor_before=cursor_before,
                cursor_after=None,
                reached_cutoff=False,
                request_meta=request_meta,
                stopped_reason=state.stopped_reason,
            )
            return

        state.pages += 1
        reached_cutoff = False
        page_posts: list[dict[str, Any]] = []
        in_period: list[dict[str, Any]] = []
        for post in batch:
            if not isinstance(post, dict):
                continue
            external_id = str(post.get("id") or "").strip()
            if external_id and external_id in state.seen_ids:
                continue
            if external_id:
                state.seen_ids.add(external_id)
            page_posts.append(post)
            ts = parse_meta_timestamp(
                post.get("timestamp") if isinstance(post.get("timestamp"), str) else None
            )
            if ts is not None:
                if state.newest is None or ts > state.newest:
                    state.newest = ts
                if state.oldest is None or ts < state.oldest:
                    state.oldest = ts
                if ts < cutoff_utc:
                    reached_cutoff = True
                    continue
                if period_end is not None and ts > period_end:
                    continue
            in_period.append(post)

        state.posts_collected += len(in_period)
        next_after = media_after_cursor(media_block)
        state.cursor_after = next_after

        stopped: str | None = None
        if reached_cutoff:
            stopped = "cutoff bereikt"
            state.stopped_reason = stopped
            state.is_complete = True
        elif not next_after:
            stopped = "einde feed"
            state.stopped_reason = stopped
            state.is_complete = state.oldest is None or state.oldest <= cutoff_utc

        yield DiscoveryPageResult(
            page_index=state.pages,
            page_size=limit,
            posts=page_posts,
            posts_in_period=in_period,
            account_payload=state.account_payload,
            cursor_before=cursor_before,
            cursor_after=next_after,
            reached_cutoff=reached_cutoff,
            request_meta=request_meta,
            stopped_reason=stopped,
        )

        if reached_cutoff or not next_after:
            return

    state.stopped_reason = "max pagina's"
    state.is_complete = False


def fetch_discovery_media_until(
    client: MetaClient,
    host_ig_id: str,
    username: str,
    *,
    cutoff_utc: datetime,
    media_limit: int | None = None,
    max_pages: int = 40,
    resume_after: str | None = None,
    known_account: dict[str, Any] | None = None,
    known_seen_ids: set[str] | None = None,
    period_end: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Paginate competitor media until posts older than cutoff appear.

    Returns account payload, deduped posts within the window, and completeness meta.
    Prefer page-by-page persistence via ``iter_discovery_media_pages`` in sync code.
    """
    collected: list[dict[str, Any]] = []
    account_payload: dict[str, Any] = dict(known_account or {})
    pages = 0
    oldest: datetime | None = None
    newest: datetime | None = None
    stopped_reason = "voltooid"
    complete = False
    last_cursor: str | None = resume_after
    request_metas: list[MetaRequestMeta] = []

    for page in iter_discovery_media_pages(
        client,
        host_ig_id,
        username,
        cutoff_utc=cutoff_utc,
        period_end=period_end,
        media_limit=media_limit,
        max_pages=max_pages,
        resume_after=resume_after,
        known_account=known_account,
        known_seen_ids=known_seen_ids,
    ):
        pages = page.page_index
        account_payload = page.account_payload
        collected.extend(page.posts_in_period)
        request_metas.append(page.request_meta)
        last_cursor = page.cursor_after
        for post in page.posts_in_period:
            ts = parse_meta_timestamp(
                post.get("timestamp") if isinstance(post.get("timestamp"), str) else None
            )
            if ts is None:
                continue
            if newest is None or ts > newest:
                newest = ts
            if oldest is None or ts < oldest:
                oldest = ts
        if page.stopped_reason:
            stopped_reason = page.stopped_reason
            complete = page.stopped_reason in {"cutoff bereikt", "lege media-pagina"} or (
                page.stopped_reason == "einde feed"
                and (oldest is None or oldest <= cutoff_utc)
            )
            if page.stopped_reason == "geen media-blok":
                complete = False
            break
        if page.reached_cutoff:
            stopped_reason = "cutoff bereikt"
            complete = True
            break
        if not page.cursor_after:
            stopped_reason = "einde feed"
            complete = oldest is None or oldest <= cutoff_utc
            break
    else:
        stopped_reason = "max pagina's"
        complete = False

    meta = {
        "pages": pages,
        "stopped_reason": stopped_reason,
        "is_complete": complete,
        "oldest": oldest,
        "newest": newest,
        "cursor_after": last_cursor,
        "request_metas": request_metas,
        "media_limit": media_limit if media_limit is not None else configured_media_page_limit(),
    }
    return account_payload, collected, meta
