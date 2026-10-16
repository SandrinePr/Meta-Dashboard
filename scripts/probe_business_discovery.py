"""One-off probe: Instagram Business Discovery via existing Meta client.

Never prints access tokens. Missing metrics stay None (not 0).
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent
for _path in (_SCRIPTS_DIR, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from dotenv import load_dotenv

from config import get_settings
from meta.client import MetaClient, MetaClientError, MetaRequestError

OWN_HINT = "zolderidee"
COMPETITOR = "hoog.design"
SAMPLE_LIMIT = 5
MEDIA_PAGE_LIMIT = 25

MEDIA_FIELDS = (
    "id",
    "caption",
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
    "followers_count",
    "media_count",
)

_TOKENISH = re.compile(r"(access_token=)[^&\s]+", re.IGNORECASE)
_LONG_HEX = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")


def safe_error(exc: BaseException) -> str:
    text = str(exc)
    text = _TOKENISH.sub(r"\1[redacted]", text)
    text = _LONG_HEX.sub("[redacted]", text)
    return text


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def present(value: Any) -> bool:
    return value is not None and value != ""


def fmt_metric(value: Any) -> str:
    if value is None:
        return "n.b. (niet geleverd)"
    return str(value)


def summarize_fields(items: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[str, str]:
    summary: dict[str, str] = {}
    for field in fields:
        seen = sum(1 for item in items if field in item and present(item.get(field)))
        if not items:
            summary[field] = "geen posts"
        elif seen == len(items):
            summary[field] = f"aanwezig ({seen}/{len(items)})"
        elif seen == 0:
            summary[field] = f"ontbreekt (0/{len(items)})"
        else:
            summary[field] = f"deels ({seen}/{len(items)})"
    return summary


def business_discovery_fields(
    username: str,
    *,
    after: str | None = None,
    media_limit: int = MEDIA_PAGE_LIMIT,
) -> str:
    media_inner = ",".join(MEDIA_FIELDS)
    if after:
        media = f"media.limit({media_limit}).after({after}){{{media_inner}}}"
    else:
        media = f"media.limit({media_limit}){{{media_inner}}}"
    account = ",".join(ACCOUNT_FIELDS)
    return f"business_discovery.username({username}){{{account},{media}}}"


def fetch_discovery(
    client: MetaClient,
    ig_user_id: str,
    username: str,
    *,
    after: str | None = None,
    media_limit: int = MEDIA_PAGE_LIMIT,
) -> dict[str, Any]:
    payload = client.get_json(
        ig_user_id,
        params={
            "fields": business_discovery_fields(
                username,
                after=after,
                media_limit=media_limit,
            )
        },
        use_page_token=True,
    )
    discovery = payload.get("business_discovery")
    if not isinstance(discovery, dict):
        raise MetaRequestError("business_discovery ontbreekt in de API-respons.")
    return discovery


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


def resolve_own_account(client: MetaClient) -> dict[str, Any]:
    settings = get_settings()
    ig_id = (settings.meta_instagram_business_account_id or "").strip()
    if not ig_id:
        raise MetaRequestError("META_INSTAGRAM_BUSINESS_ACCOUNT_ID ontbreekt in .env.")

    own = client.get_json(
        ig_id,
        params={"fields": "id,username,name,followers_count,media_count"},
        use_page_token=True,
    )
    username = str(own.get("username") or "").strip()
    return {
        "id": str(own.get("id") or ig_id),
        "username": username or None,
        "name": own.get("name"),
        "followers_count": own.get("followers_count"),
        "media_count": own.get("media_count"),
        "matches_zolderidee": username.casefold() == OWN_HINT,
        "available_keys": sorted(k for k, v in own.items() if present(v)),
    }


def print_account(label: str, account: dict[str, Any]) -> None:
    print(f"\n=== {label} ===")
    print(f"id: {account.get('id')}")
    print(f"username: {account.get('username')}")
    if "name" in account:
        print(f"name: {account.get('name')}")
    print(f"followers_count: {fmt_metric(account.get('followers_count'))}")
    print(f"media_count: {fmt_metric(account.get('media_count'))}")
def print_sample_posts(posts: list[dict[str, Any]]) -> None:
    print(f"\n=== Sample posts ({len(posts)}) ===")
    print("N.B. like_count/comments_count = stand op meetdatum, geen historische reeks.")
    for index, post in enumerate(posts, start=1):
        caption = post.get("caption")
        caption_preview = None
        if isinstance(caption, str) and caption.strip():
            caption_preview = " ".join(caption.split())[:120]
        print(f"\n[{index}] id={post.get('id')}")
        print(f"    timestamp={post.get('timestamp')}")
        print(f"    media_type={fmt_metric(post.get('media_type'))}")
        print(f"    media_product_type={fmt_metric(post.get('media_product_type'))}")
        print(f"    permalink={fmt_metric(post.get('permalink'))}")
        print(f"    like_count={fmt_metric(post.get('like_count'))}")
        print(f"    comments_count={fmt_metric(post.get('comments_count'))}")
        print(f"    caption={fmt_metric(caption_preview)}")


def fetch_three_months(
    client: MetaClient,
    ig_user_id: str,
    username: str,
    cutoff: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    collected: list[dict[str, Any]] = []
    after: str | None = None
    pages = 0
    stopped_reason = "voltooid"
    incomplete = False
    oldest: datetime | None = None
    newest: datetime | None = None

    while True:
        discovery = fetch_discovery(
            client,
            ig_user_id,
            username,
            after=after,
            media_limit=MEDIA_PAGE_LIMIT,
        )
        media_block = discovery.get("media")
        if not isinstance(media_block, dict):
            stopped_reason = "geen media-blok in respons"
            incomplete = True
            break

        batch = media_block.get("data")
        if not isinstance(batch, list) or not batch:
            stopped_reason = "lege media-pagina"
            break

        pages += 1
        reached_cutoff = False
        for post in batch:
            if not isinstance(post, dict):
                continue
            ts = parse_ts(post.get("timestamp") if isinstance(post.get("timestamp"), str) else None)
            if ts is None:
                collected.append(post)
                continue
            if newest is None or ts > newest:
                newest = ts
            if oldest is None or ts < oldest:
                oldest = ts
            if ts < cutoff:
                reached_cutoff = True
                continue
            collected.append(post)

        if reached_cutoff:
            stopped_reason = "cutoff bereikt (posts ouder dan 3 maanden)"
            break

        after = media_after_cursor(media_block)
        if not after:
            stopped_reason = "geen verdere paging-cursor (einde feed of limiet)"
            # If oldest post is still within window, period may be incomplete.
            if oldest is None or oldest > cutoff:
                incomplete = True
            break

        if pages >= 40:
            stopped_reason = "veiligheidslimiet: max 40 pagina's"
            incomplete = True
            break

    meta = {
        "pages": pages,
        "stopped_reason": stopped_reason,
        "incomplete": incomplete,
        "oldest": oldest,
        "newest": newest,
        "cutoff": cutoff,
    }
    return collected, meta


def permission_hint(exc: MetaRequestError) -> str:
    code = exc.error_code
    message = str(exc).lower()
    if code == 190:
        return "Token verlopen of ongeldig. Vernieuw via scripts/refresh_all_tokens.py."
    if code == 10 or "permission" in message or "(#10)" in str(exc):
        return (
            "Waarschijnlijk ontbreekt Instagram-toegang voor Business Discovery "
            "(o.a. instagram_basic / Instagram Public Content Access / app review)."
        )
    if "business discovery" in message or "business_discovery" in message:
        return (
            "Business Discovery niet beschikbaar voor dit account of deze app. "
            "Vereist: gekoppelde IG Professional account + juiste app permissions."
        )
    if code == 100:
        return (
            "Ongeldige parameter of object. Controleer IG-account-ID en of de "
            "concurrent een professioneel Instagram-account is."
        )
    return "Controleer Meta App permissions en of de Page/IG-koppeling geldig is."


def main() -> int:
    load_dotenv(override=True)
    settings = get_settings()
    print("Business Discovery probe")
    print(f"graph_version: {settings.meta_graph_api_version}")
    print(f"page_id configured: {bool(settings.meta_page_id)}")
    print(f"page_token configured: {bool(settings.meta_page_access_token)}")
    print(f"ig_business_id configured: {bool(settings.meta_instagram_business_account_id)}")
    print(f"competitor: @{COMPETITOR}")

    client = MetaClient.from_settings()
    measured_at = datetime.now(timezone.utc)

    try:
        own = resolve_own_account(client)
    except MetaClientError as exc:
        print("\nOWN ACCOUNT FAILED")
        if isinstance(exc, MetaRequestError):
            print(f"status={exc.status_code} code={exc.error_code} subcode={exc.error_subcode}")
            print(f"message: {safe_error(exc)}")
            print(f"needed: {permission_hint(exc)}")
        else:
            print(f"message: {safe_error(exc)}")
        return 1

    print_account("Own Instagram account (via Meta config)", own)
    print(f"available own fields: {', '.join(own['available_keys'])}")
    if own["matches_zolderidee"]:
        print("ZolderIdee: JA — dit is het gekoppelde eigen account.")
    else:
        print(
            "ZolderIdee: NEE — gekoppelde username is "
            f"@{own.get('username') or 'onbekend'}; "
            "probe gaat verder met dit gekoppelde account als discovery-host."
        )

    print(f"\nMeetmoment (UTC): {measured_at.isoformat(timespec='seconds')}")

    # --- Small sample ---
    try:
        sample_discovery = fetch_discovery(
            client,
            own["id"],
            COMPETITOR,
            media_limit=SAMPLE_LIMIT,
        )
    except MetaClientError as exc:
        print("\nCOMPETITOR SAMPLE FAILED")
        if isinstance(exc, MetaRequestError):
            print(f"status={exc.status_code} code={exc.error_code} subcode={exc.error_subcode}")
            print(f"message: {safe_error(exc)}")
            print(f"needed: {permission_hint(exc)}")
        else:
            print(f"message: {safe_error(exc)}")
        print("\nGeen fictieve data of scraping gebruikt.")
        return 2

    sample_media = sample_discovery.get("media")
    sample_posts = []
    if isinstance(sample_media, dict) and isinstance(sample_media.get("data"), list):
        sample_posts = [p for p in sample_media["data"] if isinstance(p, dict)]

    print_account(
        f"Competitor @{COMPETITOR} (Business Discovery)",
        {
            "id": sample_discovery.get("id"),
            "username": sample_discovery.get("username"),
            "followers_count": sample_discovery.get("followers_count"),
            "media_count": sample_discovery.get("media_count"),
        },
    )
    account_field_status = {
        field: ("aanwezig" if present(sample_discovery.get(field)) else "ontbreekt")
        for field in ACCOUNT_FIELDS
    }
    print("Accountvelden:", account_field_status)
    print_sample_posts(sample_posts[:SAMPLE_LIMIT])
    print("\nPostvelden in sample:", summarize_fields(sample_posts, MEDIA_FIELDS))

    # --- Three months pagination ---
    cutoff = measured_at - timedelta(days=92)
    print(f"\n=== Paginering afgelopen ~3 maanden (cutoff {cutoff.date().isoformat()}) ===")
    try:
        posts, meta = fetch_three_months(client, own["id"], COMPETITOR, cutoff)
    except MetaClientError as exc:
        print("PAGINATION FAILED")
        if isinstance(exc, MetaRequestError):
            print(f"status={exc.status_code} code={exc.error_code} subcode={exc.error_subcode}")
            print(f"message: {safe_error(exc)}")
            print(f"needed: {permission_hint(exc)}")
        else:
            print(f"message: {safe_error(exc)}")
        return 3

    in_window = []
    for post in posts:
        ts = parse_ts(post.get("timestamp") if isinstance(post.get("timestamp"), str) else None)
        if ts is None or ts >= cutoff:
            in_window.append(post)

    oldest = meta["oldest"]
    newest = meta["newest"]
    print(f"pagina's opgehaald: {meta['pages']}")
    print(f"posts binnen venster: {len(in_window)}")
    print(f"nieuwste postdatum: {newest.isoformat() if newest else 'n.b.'}")
    print(f"oudste postdatum in feed-scan: {oldest.isoformat() if oldest else 'n.b.'}")
    if in_window:
        oldest_in = None
        for post in in_window:
            ts = parse_ts(post.get("timestamp") if isinstance(post.get("timestamp"), str) else None)
            if ts and (oldest_in is None or ts < oldest_in):
                oldest_in = ts
        print(f"oudste postdatum binnen 3 maanden: {oldest_in.isoformat() if oldest_in else 'n.b.'}")
    print(f"stopreden: {meta['stopped_reason']}")
    if meta["incomplete"]:
        print("periode volledig opgehaald: NEE")
    elif meta["stopped_reason"].startswith("cutoff"):
        print("periode volledig opgehaald: JA (feed ging verder terug dan cutoff)")
    else:
        print("periode volledig opgehaald: WAARSCHIJNLIJK JA (einde feed binnen venster)")

    print("\nPostvelden over 3-maanden selectie:", summarize_fields(in_window, MEDIA_FIELDS))

    likes_present = sum(1 for p in in_window if present(p.get("like_count")))
    comments_present = sum(1 for p in in_window if present(p.get("comments_count")))
    print(
        f"likes geleverd: {likes_present}/{len(in_window)} "
        "(ontbrekende waarden niet als 0 geteld)"
    )
    print(
        f"comments geleverd: {comments_present}/{len(in_window)} "
        "(ontbrekende waarden niet als 0 geteld)"
    )

    print("\n=== Rapportkern ===")
    print(f"eigen account: @{own.get('username')} (id {own.get('id')})")
    print(f"ZolderIdee gekoppeld: {'ja' if own['matches_zolderidee'] else 'nee'}")
    print(f"concurrent @{COMPETITOR}: succesvol uitgelezen")
    print(f"posts 3m: {len(in_window)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
