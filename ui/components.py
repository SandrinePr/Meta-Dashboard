"""Reusable RRO-styled Streamlit UI components."""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime
from typing import Iterable

import streamlit as st
import streamlit.components.v1 as components

from search.engine import (
    DEFAULT_FILTER_TYPES,
    SearchResult,
    detect_match_types,
    normalize_search_term,
    result_membership_types,
)
from ui.icons import (
    COMMENT_ICON,
    FACEBOOK_ICON,
    HEART_STAT_ICON,
    INSTAGRAM_ICON,
    MESSAGE_STAT_ICON,
    POST_ICON,
    SAVE_STAT_ICON,
    SHARE_STAT_ICON,
    STAT_COMMENT_ICON,
    VIEW_STAT_ICON,
)
from ui.media import (
    get_comment_parent_caption,
    get_comment_parent_post_id,
    get_content_type_for_result,
    get_engagement_stats,
    get_image_for_search_result,
    get_stats_for_result,
)
from ui.styles import RRO_CSS

logger = logging.getLogger(__name__)

_HASHTAG_INLINE_PATTERN = re.compile(r"#([A-Za-z0-9_]+)")
_INLINE_HIGHLIGHT_PATTERN = re.compile(r"(#[A-Za-z0-9_]+)|(@[A-Za-z0-9._]+)")


def highlight_hashtags_in_text(text: str | None) -> str:
    """Escape user content and wrap hashtags (#) and mentions (@) in spans."""
    if not text:
        return "Geen tekst"

    parts: list[str] = []
    last_end = 0
    for match in _INLINE_HIGHLIGHT_PATTERN.finditer(text):
        if match.start() > last_end:
            parts.append(html.escape(text[last_end : match.start()]))
        css_class = "hashtag" if match.group(1) else "mention"
        parts.append(f'<span class="{css_class}">{html.escape(match.group(0))}</span>')
        last_end = match.end()
    if last_end < len(text):
        parts.append(html.escape(text[last_end:]))
    return "".join(parts) if parts else html.escape(text)


_SNIPPET_THRESHOLD = 120
_SNIPPET_CONTEXT = 50


def _highlight_terms(query: str | None) -> list[str]:
    """Return lowercased search terms to highlight, longest first."""
    if not query:
        return []
    base = normalize_search_term(query).strip().lower()
    terms: set[str] = set()
    if base:
        terms.add(base)
        for token in re.split(r"\s+", base):
            token = token.strip("#@").strip()
            if len(token) >= 2:
                terms.add(token)
    return sorted(terms, key=len, reverse=True)


def _terms_regex(terms: list[str]) -> re.Pattern[str] | None:
    if not terms:
        return None
    return re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)


def _apply_term_highlight(plain_text: str, pattern: re.Pattern[str] | None) -> str:
    """Escape plain text and wrap query-term matches in a text-match span."""
    if not pattern:
        return html.escape(plain_text)
    parts: list[str] = []
    last_end = 0
    for match in pattern.finditer(plain_text):
        if match.start() > last_end:
            parts.append(html.escape(plain_text[last_end : match.start()]))
        parts.append(f'<span class="text-match">{html.escape(match.group(0))}</span>')
        last_end = match.end()
    if last_end < len(plain_text):
        parts.append(html.escape(plain_text[last_end:]))
    return "".join(parts)


def _build_highlighted_html(text: str, pattern: re.Pattern[str] | None) -> str:
    """Highlight query terms; hashtags/mentions are matched as a whole token.

    Hashtags/mentions detected first: when the term matches inside one, the whole
    token gets a subtle underline (no nested spans, no background). Remaining plain
    text uses the yellow background highlight.
    """
    parts: list[str] = []
    last_end = 0
    for match in _INLINE_HIGHLIGHT_PATTERN.finditer(text):
        if match.start() > last_end:
            parts.append(_apply_term_highlight(text[last_end : match.start()], pattern))
        token = match.group(0)
        base = "hashtag" if match.group(1) else "mention"
        matched = pattern is not None and pattern.search(token) is not None
        css_class = f"{base} {base}-match" if matched else base
        parts.append(f'<span class="{css_class}">{html.escape(token)}</span>')
        last_end = match.end()
    if last_end < len(text):
        parts.append(_apply_term_highlight(text[last_end:], pattern))
    return "".join(parts)


def _snippet_around_match(
    text: str,
    pattern: re.Pattern[str] | None,
) -> tuple[str, bool, bool]:
    """Return (snippet, cut_start, cut_end) with ~50 chars of context per side."""
    if len(text) <= _SNIPPET_THRESHOLD or pattern is None:
        return text, False, False

    match = pattern.search(text)
    if match is None:
        return text, False, False

    start = max(0, match.start() - _SNIPPET_CONTEXT)
    end = min(len(text), match.end() + _SNIPPET_CONTEXT)

    if start > 0:
        space = text.find(" ", start, match.start())
        if space != -1:
            start = space + 1
    if end < len(text):
        space = text.rfind(" ", match.end(), end)
        if space != -1:
            end = space

    return text[start:end], start > 0, end < len(text)


def highlight_and_snippet(text: str | None, query: str = "") -> str:
    """Render caption/comment text: context snippet + term/hashtag/mention styling."""
    if not text:
        return "Geen tekst"

    terms = _highlight_terms(query)
    pattern = _terms_regex(terms)
    snippet, cut_start, cut_end = _snippet_around_match(text, pattern)
    body = _build_highlighted_html(snippet, pattern)
    prefix = "... " if cut_start else ""
    suffix = " ..." if cut_end else ""
    return f"{prefix}{body}{suffix}"


def matches_hashtag_query(result: SearchResult, query: str) -> bool:
    """Return True when the search term matches one of the post hashtags."""
    term = normalize_search_term(query).lower()
    if not term:
        return False
    return any(tag.lower() == term for tag in result.hashtags)


def inject_styles() -> None:
    """Inject global RRO theme CSS and mobile sidebar click-outside close."""
    st.markdown(RRO_CSS, unsafe_allow_html=True)
    components.html(
        """
        <script>
        (function () {
          const MQ = window.matchMedia("(max-width: 800px)");
          const doc = window.parent.document;

          function sidebarOpen() {
            const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
            if (!sidebar) return false;
            const style = window.parent.getComputedStyle(sidebar);
            const transform = style.transform || "";
            if (transform.includes("matrix") && transform !== "none") {
              const match = transform.match(/matrix\\(([^)]+)\\)/);
              if (match) {
                const parts = match[1].split(",").map(function (p) { return parseFloat(p.trim()); });
                if (parts.length >= 5 && parts[4] < -50) return false;
              }
            }
            return style.visibility !== "hidden" && style.display !== "none";
          }

          function collapseSidebar() {
            const btn = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
            if (btn && sidebarOpen()) btn.click();
          }

          if (doc.__rroSidebarOutsideBound) {
            doc.removeEventListener("click", doc.__rroSidebarOutsideBound, true);
          }
          doc.__rroSidebarOutsideBound = function (event) {
            if (!MQ.matches) return;
            const sidebar = doc.querySelector('section[data-testid="stSidebar"]');
            const collapseBtn = doc.querySelector('[data-testid="stSidebarCollapseButton"]');
            const openBtn = doc.querySelector('[data-testid="collapsedControl"]');
            if (!sidebar || !collapseBtn) return;
            if (!sidebarOpen()) return;
            const target = event.target;
            if (sidebar.contains(target)) return;
            if (collapseBtn.contains(target)) return;
            if (openBtn && openBtn.contains(target)) return;
            collapseSidebar();
          };
          doc.addEventListener("click", doc.__rroSidebarOutsideBound, true);
        })();
        </script>
        """,
        height=0,
    )


def _format_date(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return value



def resolve_checkbox_filters(
    *,
    instagram: bool,
    facebook: bool,
    comments: bool,
    tags: bool,
    hashtags: bool = False,
    captions: bool = False,
) -> tuple[set[str], set[str]]:
    """Map checkbox state to platform/type sets. All off means no filter."""
    platforms: set[str] = set()
    if instagram:
        platforms.add("instagram")
    if facebook:
        platforms.add("facebook")
    if not platforms:
        platforms = {"instagram", "facebook"}

    entity_types: set[str] = set()
    if comments:
        entity_types.add("comment")
    if tags:
        entity_types.add("tag")
    if hashtags:
        entity_types.add("hashtag")
    if captions:
        entity_types.add("caption")
    if not entity_types:
        entity_types = set(DEFAULT_FILTER_TYPES)

    return platforms, entity_types


def filter_results(
    results: Iterable[SearchResult],
    platforms: set[str],
    entity_types: set[str],
    query: str,
) -> list[SearchResult]:
    """Apply platform + match-type filters client-side after search."""
    types = set(entity_types) or set(DEFAULT_FILTER_TYPES)
    filtered: list[SearchResult] = []
    for result in results:
        if result.platform not in platforms:
            continue
        if result_membership_types(result, query) & types:
            filtered.append(result)
    return filtered


def render_sidebar_stats(totals: dict) -> None:
    """Render sidebar sync statistics with icons."""
    st.markdown('<div class="rro-sidebar-title">Synchronisatie</div>', unsafe_allow_html=True)

    stats = [
        (INSTAGRAM_ICON, "Instagram posts", totals.get("instagram_posts", 0)),
        (STAT_COMMENT_ICON, "Instagram comments", totals.get("instagram_comments", 0)),
        (FACEBOOK_ICON, "Facebook posts", totals.get("facebook_posts", 0)),
        (STAT_COMMENT_ICON, "Facebook comments", totals.get("facebook_comments", 0)),
    ]
    rows = []
    for icon, label, value in stats:
        rows.append(
            '<div class="rro-stat-row">'
            f'<span class="rro-stat-icon">{icon}</span>'
            f'<span class="rro-stat-label">{html.escape(label)}</span>'
            f'<span class="rro-stat-value">{html.escape(str(value))}</span>'
            "</div>"
        )
    st.markdown("".join(rows), unsafe_allow_html=True)


def render_sync_result(stats) -> None:
    """Render last sync run counters and errors in the sidebar."""
    if stats is None:
        return

    st.markdown('<div class="rro-sidebar-title">Laatste sync</div>', unsafe_allow_html=True)
    lines = [
        f"Instagram posts toegevoegd: {stats.instagram_posts_added}",
        f"Instagram posts bijgewerkt: {stats.instagram_posts_updated}",
        f"Instagram comments toegevoegd: {stats.instagram_comments_added}",
        f"Instagram comments bijgewerkt: {stats.instagram_comments_updated}",
        f"Facebook posts toegevoegd: {stats.facebook_posts_added}",
        f"Facebook posts bijgewerkt: {stats.facebook_posts_updated}",
        f"Facebook comments toegevoegd: {stats.facebook_comments_added}",
        f"Facebook comments bijgewerkt: {stats.facebook_comments_updated}",
    ]
    for line in lines:
        st.caption(line)

    if stats.errors:
        st.warning("Sync voltooid met waarschuwingen.")
        for error in stats.errors:
            st.error(error)
    else:
        st.success("Synchronisatie voltooid.")


MIN_QUERY_LENGTH = 3


def render_search_form() -> tuple[str, set[str], set[str], tuple[date, ...]]:
    """Render live search controls (no mandatory submit; searches while typing)."""
    if "date_range_filter" not in st.session_state:
        st.session_state["date_range_filter"] = ()
    st.session_state.pop("date_filter", None)
    st.session_state.pop("selected_date", None)

    st.markdown('<div class="rro-search-form-header"><h3>Zoeken</h3></div>', unsafe_allow_html=True)
    query = st.text_input(
        "Zoekterm",
        placeholder="Typ minstens 3 tekens...",
        label_visibility="collapsed",
        key="search_query",
    )

    filter_cols = st.columns(3)
    with filter_cols[0]:
        platform_labels = st.multiselect(
            "Platform",
            ["Instagram", "Facebook"],
            default=["Instagram", "Facebook"],
        )
    with filter_cols[1]:
        type_labels = st.multiselect(
            "Type",
            ["Comments", "Tags", "Hashtags", "Captions"],
            default=["Comments", "Tags", "Hashtags", "Captions"],
        )
    with filter_cols[2]:
        date_range = st.date_input(
            "Datums",
            format="YYYY/MM/DD",
            key="date_range_filter",
        )

    # Fallback button; searching is automatic, so this is optional.
    st.button("Zoeken", use_container_width=False)

    platforms, entity_types = resolve_checkbox_filters(
        instagram="Instagram" in platform_labels,
        facebook="Facebook" in platform_labels,
        comments="Comments" in type_labels,
        tags="Tags" in type_labels,
        hashtags="Hashtags" in type_labels,
        captions="Captions" in type_labels,
    )
    return query, platforms, entity_types, date_range


def _platform_badge(platform: str) -> str:
    if platform == "instagram":
        return f'<span class="badge badge-instagram badge-platform">{INSTAGRAM_ICON}<span>Instagram</span></span>'
    return f'<span class="badge badge-facebook badge-platform">{FACEBOOK_ICON}<span>Facebook</span></span>'


def _content_type_badge(label: str) -> str:
    icon = COMMENT_ICON if label == "Comment" else POST_ICON
    return f'<span class="badge badge-content badge-type">{icon}<span>{label}</span></span>'


_MATCH_BADGE_ORDER = (
    ("caption", "Caption", "badge-caption"),
    ("hashtag", "Hashtag", "badge-hashtag"),
    ("tag", "Tag", "badge-tag"),
)


def _match_badges(result: SearchResult, query: str) -> str:
    """Render Caption/Hashtag/Tag badges explaining why a result matched."""
    if not query or not query.strip():
        return ""
    matches = detect_match_types(result, query)
    badges = [
        f'<span class="badge {css} badge-match">{label}</span>'
        for key, label, css in _MATCH_BADGE_ORDER
        if key in matches
    ]
    return "".join(badges)


_STAT_DISPLAY_ORDER = (
    ("likes", HEART_STAT_ICON),
    ("comments", MESSAGE_STAT_ICON),
    ("shares", SHARE_STAT_ICON),
    ("views", VIEW_STAT_ICON),
    ("saves", SAVE_STAT_ICON),
)


def _stat_items_html(stats: dict[str, int]) -> list[str]:
    """Build stat spans for available metrics with value > 0 (real icons)."""
    return [
        f'<span class="rro-stat">{icon}<span class="rro-stat-value-inline">{stats[key]}</span></span>'
        for key, icon in _STAT_DISPLAY_ORDER
        if stats.get(key, 0) > 0
    ]


def _stats_html(result: SearchResult) -> str:
    """Render a compact engagement stats line; hides missing metrics."""
    stats = get_stats_for_result(result)
    if not stats:
        return ""
    items = _stat_items_html(stats)
    if not items:
        return ""
    return f'<div class="rro-card-stats">{"".join(items)}</div>'


def render_result_card(result: SearchResult, query: str = "") -> None:
    """Render one search result card in RRO style."""
    platform_label = "Instagram" if result.platform == "instagram" else "Facebook"
    highlighted_text = highlight_and_snippet(result.text, query)
    safe_date = html.escape(_format_date(result.published_at))

    image_url, image_source = get_image_for_search_result(result)
    logger.debug(
        "Result image entity=%s/%s url=%s source=%s",
        result.entity_type,
        result.entity_id,
        image_url,
        image_source,
    )

    if image_url:
        safe_thumb = html.escape(image_url, quote=True)
        thumb_html = (
            f'<img class="rro-thumb" src="{safe_thumb}" '
            f'alt="{html.escape(platform_label)} thumbnail" '
            f'onerror="this.outerHTML=\'<div class=&quot;rro-thumb-placeholder&quot;>Afbeelding niet beschikbaar</div>\';" />'
        )
    else:
        thumb_html = '<div class="rro-thumb-placeholder">Geen thumbnail</div>'

    if result.permalink:
        safe_link = html.escape(result.permalink, quote=True)
        action_html = (
            f'<a class="rro-btn-link" href="{safe_link}" target="_blank" '
            f'rel="noopener noreferrer">Bekijk origineel</a>'
        )
    else:
        action_html = '<div class="rro-btn-disabled">Geen link</div>'

    content_type_label = get_content_type_for_result(result)
    badges_html = (
        f"{_platform_badge(result.platform)}"
        f"{_content_type_badge(content_type_label)}"
        f"{_match_badges(result, query)}"
    )
    stats_html = _stats_html(result)
    card_html = (
        '<div class="rro-result-card">'
        f"<div>{thumb_html}</div>"
        "<div>"
        f'<div class="rro-card-badges">{badges_html}</div>'
        f'<div class="rro-card-date">{safe_date}</div>'
        f'<div class="rro-card-text">{highlighted_text}</div>'
        f"{stats_html}"
        "</div>"
        f"<div>{action_html}</div>"
        "</div>"
    )
    st.markdown(card_html, unsafe_allow_html=True)


def _comment_row_html(comment: SearchResult, query: str) -> str:
    """Render a single comment row used inside a grouped post card."""
    safe_date = html.escape(_format_date(comment.published_at))
    highlighted = highlight_and_snippet(comment.text, query)
    match_html = _match_badges(comment, query)
    comment_badge = '<span class="badge badge-comment badge-match">Comment</span>'
    return (
        '<div class="rro-comment-item">'
        f'<div class="rro-comment-item-head">{comment_badge}{match_html}'
        f'<span class="rro-comment-date">{safe_date}</span></div>'
        f'<div class="rro-comment-text">{highlighted}</div>'
        "</div>"
    )


def render_comment_group(comments: list[SearchResult], query: str) -> None:
    """Render comments nested under their parent post card."""
    if not comments:
        return
    rows = "".join(_comment_row_html(comment, query) for comment in comments)
    group_html = (
        '<div class="rro-comment-group">'
        f'<div class="rro-comment-group-title">Reacties ({len(comments)})</div>'
        f"{rows}"
        "</div>"
    )
    st.markdown(group_html, unsafe_allow_html=True)


def render_comment_card(comment: SearchResult, query: str = "") -> None:
    """Render a standalone comment as a distinct, smaller card."""
    platform_label = "Instagram" if comment.platform == "instagram" else "Facebook"
    safe_date = html.escape(_format_date(comment.published_at))
    highlighted = highlight_and_snippet(comment.text, query)
    match_html = _match_badges(comment, query)

    parent_caption = get_comment_parent_caption(comment.entity_id)
    parent_html = ""
    if parent_caption.strip():
        preview = parent_caption.strip()
        if len(preview) > 140:
            preview = preview[:140].rstrip() + "..."
        parent_html = (
            f'<div class="rro-comment-parent">Op post: {html.escape(preview)}</div>'
        )

    if comment.permalink:
        safe_link = html.escape(comment.permalink, quote=True)
        action_html = (
            f'<a class="rro-comment-link" href="{safe_link}" target="_blank" '
            f'rel="noopener noreferrer">Bekijk post</a>'
        )
    else:
        action_html = ""

    comment_badge = '<span class="badge badge-comment badge-match">Comment</span>'
    card_html = (
        '<div class="rro-comment-card">'
        f'<div class="rro-comment-label">{COMMENT_ICON}'
        f"<span>Reactie op {platform_label}-post</span></div>"
        f'<div class="rro-comment-badges">{comment_badge}{match_html}'
        f'<span class="rro-comment-date">{safe_date}</span></div>'
        f'<div class="rro-comment-text">{highlighted}</div>'
        f"{parent_html}"
        f"{action_html}"
        "</div>"
    )
    st.markdown(card_html, unsafe_allow_html=True)


def render_results_section(
    results: list[SearchResult],
    *,
    query: str = "",
    content_count: int,
    comment_count: int,
) -> None:
    """Render results inside a clear visual section (header + cards/empty state)."""
    with st.container(border=True):
        st.markdown(
            '<div class="rro-results-section">',
            unsafe_allow_html=True,
        )
        render_results_header(content_count, comment_count, results)
        if not results:
            st.info("Geen resultaten gevonden in de lokale database.")
        else:
            render_results(results, query=query)
        st.markdown("</div>", unsafe_allow_html=True)


def render_results(results: list[SearchResult], query: str | None = None) -> None:
    """Render results, grouping comments under their parent post when present."""
    query = query or ""
    post_by_id = {
        result.entity_id: result
        for result in results
        if result.entity_type == "post"
    }

    grouped: dict[int, list[SearchResult]] = {}
    orphans: set[int] = set()
    for result in results:
        if result.entity_type != "comment":
            continue
        parent_id = get_comment_parent_post_id(result.entity_id)
        if parent_id is not None and parent_id in post_by_id:
            grouped.setdefault(parent_id, []).append(result)
        else:
            orphans.add(result.entity_id)

    for result in results:
        if result.entity_type == "post":
            render_result_card(result, query)
            render_comment_group(grouped.get(result.entity_id, []), query)
        elif result.entity_id in orphans:
            render_comment_card(result, query)


def _aggregate_engagement(results: list[SearchResult]) -> dict[str, int]:
    """Sum available engagement stats across the visible results."""
    totals: dict[str, int] = {}
    for result in results:
        for key, value in get_engagement_stats(result).items():
            if value:
                totals[key] = totals.get(key, 0) + value
    return totals


def _totals_html(results: list[SearchResult]) -> str:
    """Render a compact engagement totals line; hides zero/missing metrics."""
    totals = _aggregate_engagement(results)
    items = _stat_items_html(totals)
    if not items:
        return ""
    joined = ' <span class="rro-totals-sep">&middot;</span> '.join(items)
    return f'<div class="rro-results-totals">Totaal: {joined}</div>'


def render_results_header(
    content_count: int,
    comment_count: int,
    results: list[SearchResult] | None = None,
) -> None:
    """Render results header with content/comment breakdown and engagement totals."""
    total = content_count + comment_count
    totals_html = _totals_html(results or [])
    st.markdown(
        '<div class="rro-results-header">'
        f"<h2>Resultaten ({total})</h2>"
        "</div>"
        '<div class="rro-results-subcount">'
        f"waarvan: {content_count} content-items &middot; {comment_count} comments"
        "</div>"
        f"{totals_html}",
        unsafe_allow_html=True,
    )
