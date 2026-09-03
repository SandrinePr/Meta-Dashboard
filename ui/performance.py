"""Best performance per month tab."""

from __future__ import annotations

import html

import streamlit as st

from analytics.performance import (
    METRIC_KEYS,
    METRIC_LABELS,
    TopPost,
    count_posts_for_month,
    format_month_label,
    get_available_months,
    get_latest_post_month,
    get_month_post_counts,
    get_monthly_top_posts,
)
from config import get_settings
from search.engine import SearchResult
from sync.engagement_refresh import refresh_month_insights
from ui.components import render_result_card


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def build_month_select_options(
    months: list[tuple[int, int]],
    post_counts: dict[tuple[int, int], int],
) -> tuple[list[str], dict[str, tuple[int, int]], int]:
    """Build selectbox labels, lookup map, and default index (latest month with posts)."""
    month_labels: list[str] = []
    label_to_month: dict[str, tuple[int, int]] = {}
    for year, month in months:
        base = format_month_label(year, month)
        count = post_counts.get((year, month), 0)
        label = base if count else f"{base} (nog geen posts)"
        month_labels.append(label)
        label_to_month[label] = (year, month)

    default_index = next(
        (index for index, ym in enumerate(months) if post_counts.get(ym, 0) > 0),
        0,
    )
    return month_labels, label_to_month, default_index


def _top_post_as_result(post: TopPost) -> SearchResult:
    return SearchResult(
        platform=post.platform,
        entity_type="post",
        entity_id=post.post_id,
        published_at=post.published_at,
        text=post.text or "",
        hashtags=[],
        permalink=post.permalink,
        thumbnail_url=post.thumbnail_url,
    )


def _rank_banner_html(post: TopPost, rank: int) -> str:
    metric_label = METRIC_LABELS[post.metric]
    return (
        f'<div class="rro-perf-rank-banner">'
        f'<span class="rro-perf-rank-line">'
        f'<span class="rro-perf-rank-badge">#{rank}</span>'
        f" · {html.escape(metric_label)}: "
        f'<strong class="rro-perf-rank-value">'
        f"{html.escape(_format_count(post.metric_value))}"
        f"</strong>"
        f"</span>"
        f"</div>"
    )


def render_performance_metric_section(metric: str, posts: list[TopPost]) -> None:
    """Render one metric block with up to three full result cards."""
    label = METRIC_LABELS[metric]
    st.markdown(
        f'<h3 class="rro-perf-section-header">'
        f"{html.escape(label)} · top 3 · tot nu"
        f"</h3>",
        unsafe_allow_html=True,
    )

    if not posts:
        st.markdown(
            '<div class="rro-performance-empty">'
            "Geen posts met deze metric in de gekozen maand."
            "</div>",
            unsafe_allow_html=True,
        )
        return

    for rank, post in enumerate(posts, start=1):
        render_result_card(
            _top_post_as_result(post),
            full_text=True,
            rank_banner_html=_rank_banner_html(post, rank),
        )


def render_performance_tab() -> None:
    """Render the monthly best-performance dashboard tab."""
    months = get_available_months()
    if not months:
        st.info("Nog geen posts beschikbaar om prestaties per maand te tonen.")
        return

    post_counts = get_month_post_counts()
    month_labels, label_to_month, default_index = build_month_select_options(
        months,
        post_counts,
    )

    latest_month = get_latest_post_month()
    latest_label = format_month_label(*latest_month) if latest_month else None

    st.markdown(
        '<div class="rro-filter-block-anchor" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )
    filter_cols = st.columns([2, 2, 1], gap="small")
    with filter_cols[0]:
        if "performance_month" not in st.session_state:
            st.session_state.performance_month = month_labels[default_index]
        selected_label = st.selectbox(
            "Maand",
            month_labels,
            key="performance_month",
            help=(
                "Posts uit deze maand, gerangschikt op hun huidige totalen tot nu "
                "(bijv. 47.924), niet op een oud maandcijfer."
            ),
        )
    with filter_cols[1]:
        platform_labels = st.multiselect(
            "Platforms",
            ["Instagram", "Facebook"],
            default=["Instagram", "Facebook"],
            key="performance_platforms",
        )
    with filter_cols[2]:
        st.markdown('<div class="rro-perf-refresh-spacer"></div>', unsafe_allow_html=True)
        refresh_clicked = st.button(
            "Ververs cijfers",
            use_container_width=True,
            key="performance_refresh_insights",
            help="Haalt opnieuw actuele totalen tot nu op bij Meta.",
            disabled=not get_settings().meta_page_access_token,
        )

    year, month = label_to_month[selected_label]
    platforms: set[str] = set()
    if "Instagram" in platform_labels:
        platforms.add("instagram")
    if "Facebook" in platform_labels:
        platforms.add("facebook")
    if not platforms:
        st.warning("Selecteer minstens één platform.")
        return

    post_count = count_posts_for_month(year, month, platforms=platforms)
    if post_count == 0:
        latest_hint = (
            f" Laatste beschikbare maand: **{latest_label}**."
            if latest_month
            else ""
        )
        st.info(
            f"Geen posts in **{format_month_label(year, month)}**.{latest_hint} "
            "Klik **Synchroniseer Meta** in de zijbalk om recente posts op te halen."
        )

    insights_cache_key = f"{year:04d}-{month:02d}:{','.join(sorted(platforms))}"
    refreshed = st.session_state.setdefault("performance_refreshed_months", set())
    settings = get_settings()
    # First open of a month (or manual refresh): pull live lifetime totals BEFORE ranking,
    # so users never see a stale month snapshot like 19.222 instead of 47.924.
    needs_live_refresh = (
        post_count > 0
        and bool(settings.meta_page_access_token)
        and (refresh_clicked or insights_cache_key not in refreshed)
    )
    if needs_live_refresh:
        with st.spinner(
            f"Actuele totalen tot nu ophalen voor {format_month_label(year, month)}…"
        ):
            stats = refresh_month_insights(
                year,
                month,
                platforms=platforms,
                insights_only=True,
            )
        if stats.token_expired:
            st.error("Meta-token verlopen — stel tokens opnieuw in en probeer opnieuw.")
        elif stats.failed and not stats.updated and not stats.skipped:
            st.warning(
                "Cijfers bijwerken mislukt. Probeer later opnieuw of sync via de zijbalk."
            )
        else:
            refreshed.add(insights_cache_key)
            st.session_state.performance_refreshed_months = refreshed

    ranked = get_monthly_top_posts(
        year,
        month,
        platforms=platforms,
        limit=3,
    )

    st.caption(
        f"Top posts **geplaatst in {format_month_label(year, month)}**, "
        "gerangschikt op hun **huidige totalen tot nu** "
        "(niet alleen wat die maand behaald was)."
    )

    st.markdown(
        '<div class="rro-results-section" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )

    visible_metrics = [
        metric
        for metric in METRIC_KEYS
        if metric != "saves" or "instagram" in platforms
    ]

    for index, metric in enumerate(visible_metrics):
        if index > 0:
            st.markdown('<hr class="rro-perf-section-divider">', unsafe_allow_html=True)
        render_performance_metric_section(metric, ranked[metric])
