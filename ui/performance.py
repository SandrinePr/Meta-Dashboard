"""Best performance per month tab."""

from __future__ import annotations

import html

import streamlit as st

from analytics.performance import (
    METRIC_KEYS,
    METRIC_LABELS,
    TopPost,
    format_month_label,
    get_available_months,
    get_monthly_top_posts,
)
from ui.components import _format_date, _platform_badge
from ui.icons import (
    HEART_STAT_ICON,
    MESSAGE_STAT_ICON,
    SAVE_STAT_ICON,
    SHARE_STAT_ICON,
    VIEW_STAT_ICON,
)

_METRIC_ICONS = {
    "views": VIEW_STAT_ICON,
    "likes": HEART_STAT_ICON,
    "comments": MESSAGE_STAT_ICON,
    "saves": SAVE_STAT_ICON,
    "shares": SHARE_STAT_ICON,
}

_METRIC_DESCRIPTIONS = {
    "views": "Posts met de hoogste weergaven in deze maand.",
    "likes": "Posts met de meeste likes in deze maand.",
    "comments": "Posts met de meeste reacties in deze maand.",
    "saves": "Instagram-posts met de meeste saves in deze maand.",
    "shares": "Posts met de meeste shares in deze maand.",
}


def _snippet(text: str | None, limit: int = 90) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return html.escape(cleaned) if cleaned else "<em>Geen caption</em>"
    return html.escape(cleaned[:limit].rstrip()) + "…"


def _platforms_summary(platforms: set[str]) -> str:
    labels: list[str] = []
    if "instagram" in platforms:
        labels.append("Instagram")
    if "facebook" in platforms:
        labels.append("Facebook")
    return " & ".join(labels) if labels else "—"


def _compact_row_html(post: TopPost, rank: int) -> str:
    metric_label = METRIC_LABELS[post.metric]
    icon = _METRIC_ICONS.get(post.metric, "")
    safe_date = html.escape(_format_date(post.published_at))
    link_html = ""
    if post.permalink:
        safe_link = html.escape(post.permalink, quote=True)
        link_html = (
            f'<a class="rro-perf-link" href="{safe_link}" target="_blank" '
            f'rel="noopener noreferrer">Bekijk post</a>'
        )

    return (
        f'<div class="rro-perf-row rro-perf-row--rank-{rank}">'
        f'<div class="rro-perf-rank" aria-label="Plaats {rank}">{rank}</div>'
        f'<div class="rro-perf-body">'
        f'<div class="rro-perf-meta">{_platform_badge(post.platform)}'
        f'<span class="rro-perf-date">{safe_date}</span></div>'
        f'<div class="rro-perf-caption">{_snippet(post.text)}</div>'
        f"</div>"
        f'<div class="rro-perf-score">'
        f"{icon}"
        f'<span class="rro-perf-score-label">{html.escape(metric_label)}</span>'
        f'<span class="rro-perf-score-value">{html.escape(str(post.metric_value))}</span>'
        f"</div>"
        f"{link_html}"
        f"</div>"
    )


def _metric_card_html(metric: str, posts: list[TopPost]) -> str:
    icon = _METRIC_ICONS.get(metric, "")
    label = METRIC_LABELS[metric]
    description = _METRIC_DESCRIPTIONS[metric]
    if posts:
        rows = "".join(_compact_row_html(post, rank) for rank, post in enumerate(posts, start=1))
    else:
        rows = (
            '<div class="rro-performance-empty">'
            "Geen posts met deze metric in de gekozen maand."
            "</div>"
        )
    return (
        f'<section class="rro-perf-metric-card">'
        f'<header class="rro-perf-metric-header">'
        f'<div class="rro-perf-metric-icon">{icon}</div>'
        f"<div>"
        f'<h4 class="rro-perf-metric-name">{html.escape(label)}</h4>'
        f'<p class="rro-perf-metric-desc">{html.escape(description)}</p>'
        f"</div>"
        f'<span class="rro-perf-metric-badge">Top 3</span>'
        f"</header>"
        f'<div class="rro-perf-leaderboard">{rows}</div>'
        f"</section>"
    )


def render_performance_metric_section(metric: str, posts: list[TopPost]) -> None:
    """Render one metric leaderboard card."""
    st.markdown(_metric_card_html(metric, posts), unsafe_allow_html=True)


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
        """
        <div class="rro-perf-hero">
            <p class="rro-perf-eyebrow">Performance-overzicht</p>
            <h2 class="rro-perf-title">Top posts per maand</h2>
            <p class="rro-perf-lead">
                Ontdek welke posts het best presteerden in een gekozen kalendermaand.
                Per metric tonen we de <strong>top 3</strong>: weergaven, likes,
                reacties, opgeslagen en gedeeld.
            </p>
            <ul class="rro-perf-steps">
                <li><strong>1.</strong> Kies een maand</li>
                <li><strong>2.</strong> Filter op platform</li>
                <li><strong>3.</strong> Bekijk de ranglijsten hieronder</li>
            </ul>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        st.markdown(
            '<div class="rro-perf-filter-title">Filters</div>',
            unsafe_allow_html=True,
        )
        filter_cols = st.columns([2, 2], gap="small")
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

    platform_summary = _platforms_summary(platforms)
    st.markdown(
        f'<div class="rro-perf-context">'
        f"Resultaten voor <strong>{html.escape(selected_label)}</strong>"
        f" · {html.escape(platform_summary)}"
        f" · top 3 per metric"
        f"</div>",
        unsafe_allow_html=True,
    )

    with st.spinner("Ranglijsten laden…"):
        ranked = get_monthly_top_posts(
            year,
            month,
            platforms=platforms,
            limit=3,
        )

    visible_metrics = [
        metric
        for metric in METRIC_KEYS
        if metric != "saves" or "instagram" in platforms
    ]

    left_metrics = visible_metrics[:3]
    right_metrics = visible_metrics[3:]
    grid_cols = st.columns(2, gap="large")
    with grid_cols[0]:
        for metric in left_metrics:
            render_performance_metric_section(metric, ranked[metric])
    with grid_cols[1]:
        for metric in right_metrics:
            render_performance_metric_section(metric, ranked[metric])

    st.markdown(
        '<p class="rro-perf-footnote">'
        "Cijfers komen uit de laatst gesynchroniseerde Meta-data. "
        "Posts zonder metric (n.b.) staan niet in de ranglijst."
        "</p>",
        unsafe_allow_html=True,
    )
