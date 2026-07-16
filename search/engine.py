"""Local FTS search over synced SQLite content."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from db.database import get_connection

_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_HASHTAG_PATTERN = re.compile(r"#([A-Za-z0-9_]+)")
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9._]+)")

ALL_MATCH_TYPES = {"post", "comment", "tag", "hashtag", "caption"}
DEFAULT_FILTER_TYPES = {"comment", "tag", "hashtag", "caption"}


@dataclass(slots=True)
class SearchResult:
    platform: str
    entity_type: str
    entity_id: int
    published_at: str
    text: str
    hashtags: list[str]
    permalink: str | None
    thumbnail_url: str | None


def normalize_search_term(query: str) -> str:
    """Strip a leading # or @ so '#cute'/'cute' and '@x'/'x' share the FTS term."""
    cleaned = query.strip()
    if cleaned[:1] in {"#", "@"}:
        cleaned = cleaned[1:].strip()
    return cleaned


def extract_mentions(text: str | None) -> list[str]:
    """Return @mention account names (without leading @) from free text."""
    if not text:
        return []
    return _MENTION_PATTERN.findall(text)


def _build_fts_query(query: str) -> str:
    """Build a safe FTS5 query from user input."""
    normalized = normalize_search_term(query)
    terms = _TERM_PATTERN.findall(normalized)
    if not terms:
        return ""
    return " ".join(terms)


def _matches_hashtag_query(result: SearchResult, query: str) -> bool:
    """Return True when the search term matches one of the post hashtags."""
    term = normalize_search_term(query).lower()
    if not term:
        return False
    return any(tag.lower() == term for tag in result.hashtags)


def _matches_mention_query(result: SearchResult, query: str) -> bool:
    """Return True when the search term matches an @mention in the text."""
    term = normalize_search_term(query).lower()
    if not term:
        return False
    return any(term in mention.lower() for mention in extract_mentions(result.text))


def _matches_caption_query(result: SearchResult, query: str) -> bool:
    """Return True when the term appears in plain caption text (excl. #/@)."""
    term = normalize_search_term(query).lower()
    if not term:
        return False
    stripped = _MENTION_PATTERN.sub(" ", result.text or "")
    stripped = _HASHTAG_PATTERN.sub(" ", stripped)
    return term in stripped.lower()


def detect_match_types(result: SearchResult, query: str) -> set[str]:
    """Determine which parts of a result match the query (badge/filter basis).

    Comments never produce a "caption" match; their hashtag/tag matches are based
    on the comment text itself, not on hashtags inherited from the parent post.
    """
    if result.entity_type == "comment":
        matches: set[str] = {"comment"}
        term = normalize_search_term(query).lower()
        if not term:
            return matches
        comment_tags = [tag.lower() for tag in _HASHTAG_PATTERN.findall(result.text or "")]
        if any(tag == term for tag in comment_tags):
            matches.add("hashtag")
        if _matches_mention_query(result, query):
            matches.add("tag")
        return matches

    matches = set()
    if _matches_caption_query(result, query):
        matches.add("caption")
    if _matches_hashtag_query(result, query):
        matches.add("hashtag")
    if _matches_mention_query(result, query):
        matches.add("tag")
    return matches


def result_membership_types(result: SearchResult, query: str) -> set[str]:
    """Combine the base entity type with detected match types for filtering."""
    membership = {result.entity_type}
    membership.update(detect_match_types(result, query))
    return membership


def filter_by_entity_types(
    results: list[SearchResult],
    query: str,
    entity_types: set[str] | None,
) -> list[SearchResult]:
    """Keep results whose membership matches at least one selected type."""
    types = set(entity_types or DEFAULT_FILTER_TYPES)
    if not types:
        types = set(DEFAULT_FILTER_TYPES)

    filtered: list[SearchResult] = []
    for result in results:
        if result_membership_types(result, query) & types:
            filtered.append(result)
    return filtered


def search(
    query: str,
    platforms: set[str] | None = None,
    entity_types: set[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """Search local FTS index with optional platform, type, and date filters."""
    fts_query = _build_fts_query(query)
    if not fts_query:
        return []

    sql = """
        SELECT
            entity_type,
            CAST(entity_id AS INTEGER) AS entity_id,
            platform,
            text,
            hashtags,
            published_at,
            permalink,
            thumbnail_url
        FROM search_index
        WHERE search_index MATCH ?
    """
    params: list[object] = [fts_query]

    if platforms:
        placeholders = ", ".join("?" for _ in platforms)
        sql += f" AND platform IN ({placeholders})"
        params.extend(sorted(platforms))

    if start_date is not None and end_date is not None:
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        sql += " AND date(published_at) >= ? AND date(published_at) <= ?"
        params.extend([start_date.isoformat(), end_date.isoformat()])

    sql += " ORDER BY published_at DESC LIMIT ?"
    params.append(limit)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    results: list[SearchResult] = []
    for row in rows:
        hashtags = [tag for tag in (row["hashtags"] or "").split() if tag]
        results.append(
            SearchResult(
                platform=row["platform"],
                entity_type=row["entity_type"],
                entity_id=int(row["entity_id"]),
                published_at=row["published_at"],
                text=row["text"] or "",
                hashtags=hashtags,
                permalink=row["permalink"],
                thumbnail_url=row["thumbnail_url"],
            )
        )

    return filter_by_entity_types(results, query, entity_types)
