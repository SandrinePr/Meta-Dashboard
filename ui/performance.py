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
    get_monthly_top_posts,
)
from search.engine import SearchResult
from ui.components import render_result_card


def _format_count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


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
        f"{html.escape(label)} · top 3"
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

    month_labels = [format_month_label(year, month) for year, month in months]
    label_to_month = {
        label: (year, month)
        for label, (year, month) in zip(month_labels, months, strict=True)
    }

    st.markdown(
        '<div class="rro-search-form-header"><h3>Top posts per maand</h3></div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Bekijk per kalendermaand welke posts het best presteerden op weergaven, "
        "likes, reacties, opgeslagen en gedeeld."
    )

    st.markdown(
        '<div class="rro-filter-block-anchor" aria-hidden="true"></div>',
        unsafe_allow_html=True,
    )
    filter_cols = st.columns(2, gap="small")
    with filter_cols[0]:
        selected_label = st.selectbox(
            "Maand",
            month_labels,
            key="performance_month",
            help="Kalendermaand waarop de post is gepubliceerd.",
        )
    with filter_cols[1]:
        platform_labels = st.multiselect(
            "Platforms",
            ["Instagram", "Facebook"],
            default=["Instagram", "Facebook"],
            key="performance_platforms",
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

    st.markdown(
        f'<div class="rro-perf-context">'
        f"Ranglijsten voor <strong>{html.escape(selected_label)}</strong>"
        f"</div>",
        unsafe_allow_html=True,
    )

    post_count = count_posts_for_month(year, month, platforms=platforms)
    if post_count == 0:
        st.info(
            f"Nog geen posts in {selected_label}. "
            "Gebruik **Synchroniseer Meta** in de zijbalk om recente content op te halen."
        )

    with st.spinner("Ranglijsten laden…"):
        ranked = get_monthly_top_posts(
            year,
            month,
            platforms=platforms,
            limit=3,
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

    st.caption(
        "Cijfers komen uit de laatst gesynchroniseerde Meta-data. "
        "Posts zonder metric staan niet in de ranglijst."
    )
