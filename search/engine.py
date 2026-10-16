"""Local FTS search over synced SQLite content."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher

from db.database import get_connection
from db.repository import list_own_account_ids

_TERM_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_HASHTAG_PATTERN = re.compile(r"#([A-Za-z0-9_]+)")
_MENTION_PATTERN = re.compile(r"@([A-Za-z0-9._]+)")
_NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")

# Fuzzy token match: tolerate small typos (e.g. bork ≈ borek).
FUZZY_RATIO_THRESHOLD = 0.8
# Wider FTS/LIKE candidate pool; Python matching + limit trim afterwards.
_CANDIDATE_LIMIT_FACTOR = 10
_CANDIDATE_LIMIT_MIN = 200

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
    account_name: str | None = None
    account_username: str | None = None
    author_name: str | None = None


def normalize_search_term(query: str) -> str:
    """Strip a leading # or @ so '#cute'/'cute' and '@x'/'x' share the FTS term."""
    cleaned = query.strip()
    if cleaned[:1] in {"#", "@"}:
        cleaned = cleaned[1:].strip()
    return cleaned


def compact_normalize(value: str | None) -> str:
    """Lowercase and strip spaces, hyphens, underscores, hashtags and punctuation.

    ``puls groen``, ``#puls-groen`` and ``pulsgroen`` all become ``pulsgroen``.
    """
    if not value:
        return ""
    return _NON_ALNUM_PATTERN.sub("", value.lower())


def extract_mentions(text: str | None) -> list[str]:
    """Return @mention account names (without leading @) from free text."""
    if not text:
        return []
    return _MENTION_PATTERN.findall(text)


def _query_tokens(query: str) -> list[str]:
    """Alphanumeric tokens from the user query (leading #/@ already stripped)."""
    return _TERM_PATTERN.findall(normalize_search_term(query))


def _build_fts_query(query: str) -> str:
    """Build an FTS5 prefix query. Multi-word terms use AND so each part must hit."""
    tokens = _query_tokens(query)
    compact = compact_normalize(query)
    seen: set[str] = set()

    def _star(term: str) -> str | None:
        cleaned = term.strip()
        if len(cleaned) < 2 or cleaned in seen:
            return None
        seen.add(cleaned)
        return f"{cleaned}*"

    if len(tokens) > 1:
        head_parts = [part for token in tokens[:-1] if (part := _star(token))]
        last_stars = [
            star for form in _last_token_search_forms(tokens) if (star := _star(form))
        ]
        if last_stars:
            last_clause = (
                last_stars[0] if len(last_stars) == 1 else f"({' OR '.join(last_stars)})"
            )
            and_clause = " AND ".join([*head_parts, last_clause])
        else:
            and_clause = " AND ".join(head_parts)
        compact_stars = [
            star
            for variant in sorted(_compact_query_variants([t.lower() for t in tokens]))
            if (star := _star(variant))
        ]
        if compact_stars:
            return f"({and_clause}) OR {' OR '.join(compact_stars)}"
        return and_clause

    parts: list[str] = []
    for token in tokens:
        part = _star(token)
        if part:
            parts.append(part)
    compact_part = _star(compact) if compact else None
    if compact_part:
        parts.append(compact_part)
    return " OR ".join(parts)


def _plural_stem(token: str) -> str:
    """Strip a trailing Dutch plural ``s`` / ``en`` from the last query word."""
    last = compact_normalize(token) or token.lower()
    if last.endswith("s") and len(last) > 4:
        return last[:-1]
    if last.endswith("en") and len(last) > 6:
        return last[:-2]
    return last


def _last_token_search_forms(tokens: list[str]) -> list[str]:
    """Last-word prefixes including small plural stems (``interieur`` / ``interieurs``)."""
    if not tokens:
        return []
    last = compact_normalize(tokens[-1]) or tokens[-1].lower()
    stem = _plural_stem(tokens[-1])
    forms = {stem, stem + "s", stem + "en", last}
    return sorted(form for form in forms if len(form) >= 2)


def _like_seed(query: str) -> str:
    """LIKE fallback seed. Multi-word queries use the compact name stem.

    ``Mint Interieur`` must not use seed ``mint``: that also matches
    ``Droominterieur``. Single-term ``pulsgroen`` still uses a short seed so
    spaced ``PULS Groen`` can be found.
    """
    compact = compact_normalize(query)
    if not compact:
        return ""
    tokens = _query_tokens(query)
    if len(tokens) > 1:
        variants = _compact_query_variants([token.lower() for token in tokens])
        return min(variants, key=len) if variants else compact
    return compact[:4] if len(compact) >= 4 else compact


def _fuzzy_token_match(query: str, candidate: str) -> bool:
    """True when query is a prefix/substring of candidate or close (typo-tolerant)."""
    if not query or not candidate:
        return False
    if len(query) < 2:
        return query == candidate
    if query in candidate or candidate.startswith(query):
        return True
    # ``mintinterieur`` must not fuzzy-match ``interieur``: they share a long
    # suffix, but the leftover prefix is another glued word, not a typo.
    if query.endswith(candidate) and len(query) - len(candidate) >= 3:
        return False
    if candidate.endswith(query) and len(candidate) - len(query) >= 3:
        return False
    # Avoid fuzzy on very short pairs / large length gaps (noise).
    if len(query) < 3 or abs(len(query) - len(candidate)) > max(2, len(query) // 2):
        return False
    return SequenceMatcher(None, query, candidate).ratio() >= FUZZY_RATIO_THRESHOLD


def _compact_query_variants(tokens: list[str]) -> set[str]:
    """Compact forms of a query including small last-token plurals (``s`` / ``en``).

    Last-token only, so ``puls groen`` does not lose the ``en`` of ``groen``.
    """
    if not tokens:
        return set()
    stem = _plural_stem(tokens[-1])
    last_forms = {stem, stem + "s", stem + "en"}
    prefix = "".join(compact_normalize(token) for token in tokens[:-1])
    return {prefix + form for form in last_forms if len(prefix + form) >= 2}


def _compound_name_hit(
    variants: set[str], candidate: str, *, allow_infix: bool
) -> bool:
    """True when ``candidate`` is the compact name, a small plural, or a compound."""
    if not candidate or not variants:
        return False
    for variant in variants:
        if candidate.startswith(variant):
            return True
        if allow_infix and variant in candidate:
            return True
    return False


def _compound_phrase_match(tokens: list[str], compact_tokens: list[str]) -> bool:
    """Match a multi-word query as one name: spaces, concatenation, small plurals.

    ``Mint Interieur`` hits ``MintInterieurs``, ``#mintinterieur`` and
    ``PULS Groenprojecten``-style compounds, but not ``Droominterieur`` or a
    caption that only contains ``interieur``.
    """
    variants = _compact_query_variants(tokens)
    compact_query = "".join(compact_normalize(token) for token in tokens)
    if not variants or not compact_tokens:
        return False

    for index, start_token in enumerate(compact_tokens):
        if _compound_name_hit(variants, start_token, allow_infix=True):
            return True
        combined = start_token
        for token in compact_tokens[index + 1 :]:
            combined += token
            if _compound_name_hit(variants, combined, allow_infix=False):
                return True
            if len(combined) >= len(compact_query) and not compact_query.startswith(
                combined
            ):
                break
            if not compact_query.startswith(combined) and not any(
                variant.startswith(combined) for variant in variants
            ):
                break
    return False


def _field_matches(query: str, field: str | None) -> bool:
    """Match query against one field using original + compact + fuzzy token checks.

    Multi-word queries require every part of the name (``Mint`` and
    ``Interieur``). Spaces and small plurals (``s`` / ``en``) are ignored, but
    another name such as ``Droominterieur`` or a caption with only
    ``interieur`` does not match.
    """
    if not field:
        return False

    original_query = normalize_search_term(query).strip().lower()
    compact_query = compact_normalize(query)
    field_lower = field.lower()
    compact_field = compact_normalize(field)
    tokens = [token.lower() for token in _query_tokens(query)]
    field_tokens = _TERM_PATTERN.findall(field_lower)
    compact_field_tokens = [compact_normalize(token) for token in field_tokens if token]

    if original_query and original_query in field_lower:
        return True

    if _compound_phrase_match(tokens, compact_field_tokens):
        return True

    # Multi-word queries are one name. Do not match each word independently:
    # ``mint`` is a substring of ``droominterieur``, so AND-of-tokens would
    # still return Droominterieur for ``Mint Interieur``.
    if len(tokens) > 1:
        return False

    if compact_query and compact_query in compact_field:
        return True
    if not tokens:
        return False

    def _token_hits(needle: str) -> bool:
        needle_l = needle.lower().strip()
        if len(needle_l) < 2:
            return False
        compact_needle = compact_normalize(needle_l) or needle_l
        for token in field_tokens:
            if _fuzzy_token_match(needle_l, token):
                return True
        for token in compact_field_tokens:
            if token and _fuzzy_token_match(compact_needle, token):
                return True
        return False

    # Single-term queries: allow partial/fuzzy token hits (``bore`` → ``borek``).
    return _token_hits(tokens[0]) or _token_hits(compact_query)


def _searchable_fields(result: SearchResult) -> list[str]:
    """All fields that should participate in free-text matching."""
    fields = [
        result.text or "",
        " ".join(result.hashtags),
        result.platform or "",
        result.account_name or "",
        result.account_username or "",
        result.author_name or "",
    ]
    return [field for field in fields if field]


def result_matches_query(result: SearchResult, query: str) -> bool:
    """Return True when the result matches via original or normalized/fuzzy logic.

    Matching is evaluated against the combined searchable haystack so multi-word
    names (``puls groen`` → ``pulsgroen``, ``Mint Interieur`` → ``#mintinterieur``)
    stay one phrase instead of matching each word on its own.
    """
    if not normalize_search_term(query).strip() and not compact_normalize(query):
        return False
    haystack = " ".join(_searchable_fields(result))
    return _field_matches(query, haystack)

def matches_hashtag_query(result: SearchResult, query: str) -> bool:
    """Return True when the search term matches one of the post hashtags."""
    if not compact_normalize(query) and not normalize_search_term(query).strip():
        return False
    return any(_field_matches(query, tag) for tag in result.hashtags)


def _matches_mention_query(result: SearchResult, query: str) -> bool:
    """Return True when the search term matches an @mention in the text."""
    if not compact_normalize(query) and not normalize_search_term(query).strip():
        return False
    return any(_field_matches(query, mention) for mention in extract_mentions(result.text))


def _matches_caption_query(result: SearchResult, query: str) -> bool:
    """Return True when the term appears in plain caption text (excl. #/@)."""
    if not compact_normalize(query) and not normalize_search_term(query).strip():
        return False
    stripped = _MENTION_PATTERN.sub(" ", result.text or "")
    stripped = _HASHTAG_PATTERN.sub(" ", stripped)
    return _field_matches(query, stripped)


def detect_match_types(result: SearchResult, query: str) -> set[str]:
    """Determine which parts of a result match the query (badge/filter basis).

    Comments never produce a "caption" match; their hashtag/tag matches are based
    on the comment text itself, not on hashtags inherited from the parent post.
    """
    if result.entity_type == "comment":
        matches: set[str] = {"comment"}
        if not normalize_search_term(query).strip() and not compact_normalize(query):
            return matches
        comment_tags = _HASHTAG_PATTERN.findall(result.text or "")
        if any(_field_matches(query, tag) for tag in comment_tags):
            matches.add("hashtag")
        if _matches_mention_query(result, query):
            matches.add("tag")
        return matches

    matches = set()
    if _matches_caption_query(result, query):
        matches.add("caption")
    if matches_hashtag_query(result, query):
        matches.add("hashtag")
    if _matches_mention_query(result, query):
        matches.add("tag")
    # Account / platform hits count as caption-style content matches for filtering.
    if not matches:
        extra_fields = [
            result.platform or "",
            result.account_name or "",
            result.account_username or "",
            result.author_name or "",
        ]
        if any(_field_matches(query, field) for field in extra_fields if field):
            matches.add("caption")
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


def _dedupe_results(results: list[SearchResult]) -> list[SearchResult]:
    """Keep first occurrence of each (entity_type, entity_id)."""
    seen: set[tuple[str, int]] = set()
    unique: list[SearchResult] = []
    for result in results:
        key = (result.entity_type, result.entity_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(result)
    return unique


def _row_to_result(row) -> SearchResult:
    hashtags = [tag for tag in (row["hashtags"] or "").split() if tag]
    return SearchResult(
        platform=row["platform"],
        entity_type=row["entity_type"],
        entity_id=int(row["entity_id"]),
        published_at=row["published_at"],
        text=row["text"] or "",
        hashtags=hashtags,
        permalink=row["permalink"],
        thumbnail_url=row["thumbnail_url"],
        account_name=row["account_name"] if "account_name" in row.keys() else None,
        account_username=(
            row["account_username"] if "account_username" in row.keys() else None
        ),
        author_name=row["author_name"] if "author_name" in row.keys() else None,
    )


def _own_account_ids_for_platforms(platforms: set[str] | None) -> list[int]:
    """Own dashboard account IDs for the selected platforms (never competitors)."""
    with get_connection() as conn:
        return list_own_account_ids(conn, platforms=platforms)


def _own_account_sql_filter(
    account_ids: list[int],
    *,
    post_alias: str = "p",
    comment_post_alias: str = "cp",
) -> tuple[str, list[object]]:
    """Restrict hits to posts/comments belonging to own accounts."""
    if not account_ids:
        return " AND 1 = 0", []
    placeholders = ", ".join("?" for _ in account_ids)
    clause = f"""
      AND (
        (hits.entity_type = 'post' AND {post_alias}.account_id IN ({placeholders}))
        OR (hits.entity_type = 'comment' AND {comment_post_alias}.account_id IN ({placeholders}))
      )
    """
    return clause, [*account_ids, *account_ids]


def get_recent_posts(
    *,
    platforms: set[str] | None = None,
    limit: int = 20,
) -> list[SearchResult]:
    """Return the newest own-account posts from the local database (no search query)."""
    platforms = platforms or {"instagram", "facebook"}
    if not platforms:
        platforms = {"instagram", "facebook"}

    account_ids = _own_account_ids_for_platforms(platforms)
    if not account_ids:
        return []

    plat_ph = ", ".join("?" for _ in platforms)
    acc_ph = ", ".join("?" for _ in account_ids)
    sql = f"""
        SELECT
            'post' AS entity_type,
            p.id AS entity_id,
            p.platform AS platform,
            p.text AS text,
            COALESCE(
                (
                    SELECT GROUP_CONCAT(h.tag, ' ')
                    FROM post_hashtags ph
                    JOIN hashtags h ON h.id = ph.hashtag_id
                    WHERE ph.post_id = p.id
                ),
                ''
            ) AS hashtags,
            p.published_at AS published_at,
            p.permalink AS permalink,
            p.thumbnail_url AS thumbnail_url,
            a.name AS account_name,
            a.username AS account_username,
            NULL AS author_name
        FROM posts p
        LEFT JOIN accounts a ON a.id = p.account_id
        WHERE p.platform IN ({plat_ph})
          AND p.account_id IN ({acc_ph})
          AND p.published_at IS NOT NULL
          AND p.published_at != ''
        ORDER BY p.published_at DESC
        LIMIT ?
    """
    params: list[object] = [*sorted(platforms), *account_ids, limit]
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_result(row) for row in rows]



def _append_common_filters(
    sql: str,
    params: list[object],
    *,
    platforms: set[str] | None,
    start_date: date | None,
    end_date: date | None,
    table: str = "search_index",
) -> tuple[str, list[object]]:
    """Append optional platform/date filters. Both platforms → no platform SQL filter."""
    if platforms and platforms < {"instagram", "facebook"}:
        placeholders = ", ".join("?" for _ in platforms)
        sql += f" AND {table}.platform IN ({placeholders})"
        params.extend(sorted(platforms))

    if start_date is not None and end_date is not None:
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        sql += (
            f" AND date({table}.published_at) >= ?"
            f" AND date({table}.published_at) <= ?"
        )
        params.extend([start_date.isoformat(), end_date.isoformat()])
    return sql, params


_ENRICH_SELECT = """
    SELECT
        hits.entity_type AS entity_type,
        CAST(hits.entity_id AS INTEGER) AS entity_id,
        hits.platform AS platform,
        hits.text AS text,
        hits.hashtags AS hashtags,
        hits.published_at AS published_at,
        hits.permalink AS permalink,
        hits.thumbnail_url AS thumbnail_url,
        COALESCE(ap.name, ac.name) AS account_name,
        COALESCE(ap.username, ac.username) AS account_username,
        c.author_name AS author_name
    FROM (
        {inner_sql}
    ) AS hits
    LEFT JOIN posts AS p
        ON hits.entity_type = 'post'
        AND CAST(hits.entity_id AS INTEGER) = p.id
    LEFT JOIN accounts AS ap
        ON p.account_id = ap.id
    LEFT JOIN comments AS c
        ON hits.entity_type = 'comment'
        AND CAST(hits.entity_id AS INTEGER) = c.id
    LEFT JOIN posts AS cp
        ON c.post_id = cp.id
    LEFT JOIN accounts AS ac
        ON cp.account_id = ac.id
"""


def search(
    query: str,
    platforms: set[str] | None = None,
    entity_types: set[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
) -> list[SearchResult]:
    """Search local FTS index with optional platform, type, and date filters.

    Retrieval casts a wide net (prefix FTS + LIKE seed). Matching is decided in
    Python via original text, compact normalization, and fuzzy token comparison.
    Platforms default to both Instagram and Facebook unless explicitly restricted.

    FTS5 MATCH cannot be mixed with JOINs in one statement, so candidates are
    selected from ``search_index`` first and then enriched with account fields.
    """
    fts_query = _build_fts_query(query)
    like_seed = _like_seed(query)
    if not fts_query and not like_seed:
        return []

    account_ids = _own_account_ids_for_platforms(platforms)
    if not account_ids:
        return []
    own_filter_sql, own_filter_params = _own_account_sql_filter(account_ids)

    candidate_limit = max(limit * _CANDIDATE_LIMIT_FACTOR, _CANDIDATE_LIMIT_MIN)
    rows_by_key: dict[tuple[str, int], object] = {}

    def _store(rows) -> None:
        for row in rows:
            key = (row["entity_type"], int(row["entity_id"]))
            if key not in rows_by_key:
                rows_by_key[key] = row

    with get_connection() as conn:
        # 1) FTS prefix candidates (no JOINs — required by FTS5).
        if fts_query:
            fts_inner = """
                SELECT
                    entity_type, entity_id, platform, text, hashtags,
                    published_at, permalink, thumbnail_url
                FROM search_index
                WHERE search_index MATCH ?
            """
            fts_params: list[object] = [fts_query]
            fts_inner, fts_params = _append_common_filters(
                fts_inner,
                fts_params,
                platforms=platforms,
                start_date=start_date,
                end_date=end_date,
            )
            fts_inner += " ORDER BY published_at DESC LIMIT ?"
            fts_params.append(candidate_limit)
            fts_sql = _ENRICH_SELECT.format(inner_sql=fts_inner)
            fts_sql += own_filter_sql
            fts_params.extend(own_filter_params)
            _store(conn.execute(fts_sql, fts_params).fetchall())

        # 2) LIKE seed candidates (text/hashtags/platform + account/author fields).
        if like_seed:
            seed = f"%{like_seed.lower()}%"
            like_sql = """
                SELECT
                    search_index.entity_type AS entity_type,
                    CAST(search_index.entity_id AS INTEGER) AS entity_id,
                    search_index.platform AS platform,
                    search_index.text AS text,
                    search_index.hashtags AS hashtags,
                    search_index.published_at AS published_at,
                    search_index.permalink AS permalink,
                    search_index.thumbnail_url AS thumbnail_url,
                    COALESCE(ap.name, ac.name) AS account_name,
                    COALESCE(ap.username, ac.username) AS account_username,
                    c.author_name AS author_name
                FROM search_index
                LEFT JOIN posts AS p
                    ON search_index.entity_type = 'post'
                    AND CAST(search_index.entity_id AS INTEGER) = p.id
                LEFT JOIN accounts AS ap
                    ON p.account_id = ap.id
                LEFT JOIN comments AS c
                    ON search_index.entity_type = 'comment'
                    AND CAST(search_index.entity_id AS INTEGER) = c.id
                LEFT JOIN posts AS cp
                    ON c.post_id = cp.id
                LEFT JOIN accounts AS ac
                    ON cp.account_id = ac.id
                WHERE (
                    lower(search_index.text) LIKE ?
                    OR lower(search_index.hashtags) LIKE ?
                    OR lower(search_index.platform) LIKE ?
                    OR lower(COALESCE(ap.name, '')) LIKE ?
                    OR lower(COALESCE(ap.username, '')) LIKE ?
                    OR lower(COALESCE(ac.name, '')) LIKE ?
                    OR lower(COALESCE(ac.username, '')) LIKE ?
                    OR lower(COALESCE(c.author_name, '')) LIKE ?
                )
            """
            like_params: list[object] = [seed] * 8
            like_sql, like_params = _append_common_filters(
                like_sql,
                like_params,
                platforms=platforms,
                start_date=start_date,
                end_date=end_date,
            )
            # Own-account restriction (same aliases as enrich joins).
            like_acc_ph = ", ".join("?" for _ in account_ids)
            like_sql += f"""
              AND (
                (search_index.entity_type = 'post' AND p.account_id IN ({like_acc_ph}))
                OR (search_index.entity_type = 'comment' AND cp.account_id IN ({like_acc_ph}))
              )
            """
            like_params.extend([*account_ids, *account_ids])
            like_sql += " ORDER BY search_index.published_at DESC LIMIT ?"
            like_params.append(candidate_limit)
            _store(conn.execute(like_sql, like_params).fetchall())

    results = [_row_to_result(row) for row in rows_by_key.values()]
    results.sort(key=lambda item: item.published_at, reverse=True)
    results = [result for result in results if result_matches_query(result, query)]
    results = _dedupe_results(results)

    if platforms:
        results = [result for result in results if result.platform in platforms]

    filtered = filter_by_entity_types(results, query, entity_types)
    return filtered[:limit]
