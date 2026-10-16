"""Growth line chart for Social vergelijking (stored measurements only)."""

from __future__ import annotations

from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from analytics.growth_series import SeriesMode, build_growth_series


def render_growth_chart(
    *,
    own_account_id: int | None,
    competitor_usernames: list[str],
    mode: SeriesMode,
    day1: date | None = None,
    day2: date | None = None,
    intraday_day: date | None = None,
) -> None:
    """Render standard follower-growth chart in counts (no banners, no toggles)."""
    series = build_growth_series(
        own_account_id=own_account_id,
        competitor_usernames=competitor_usernames,
        mode=mode,
        day1=day1,
        day2=day2,
        intraday_day=intraday_day,
    )

    rows: list[dict] = []
    color_domain: list[str] = []
    color_range: list[str] = []
    for account in series.series:
        if not account.chartable or len(account.points) < 2:
            continue
        color_domain.append(account.display_name)
        color_range.append(account.color)
        for point in account.points:
            rows.append(
                {
                    "Account": account.display_name,
                    "Moment": point.label,
                    "measured_at": point.measured_at,
                    "Volgers": point.followers,
                    "Groei aantal": point.growth_abs,
                    "Groei %": point.growth_pct,
                }
            )
    if not rows:
        return

    frame = pd.DataFrame(rows).sort_values("measured_at")
    moment_order = list(dict.fromkeys(frame.sort_values("measured_at")["Moment"].tolist()))

    chart = (
        alt.Chart(frame)
        .mark_line(point=True)
        .encode(
            x=alt.X("Moment:N", sort=moment_order, title="Meetdatum"),
            y=alt.Y("Groei aantal:Q", title="Volgersgroei"),
            color=alt.Color(
                "Account:N",
                title="Account",
                scale=alt.Scale(domain=color_domain, range=color_range),
            ),
            tooltip=[
                alt.Tooltip("Account:N"),
                alt.Tooltip("Moment:N", title="Meetmoment"),
                alt.Tooltip("Volgers:Q", title="Aantal volgers", format=",.0f"),
                alt.Tooltip("Groei aantal:Q", title="Groei (aantal)", format="+.0f"),
                alt.Tooltip("Groei %:Q", title="Groei (%)", format="+.1f"),
            ],
        )
        .properties(height=320)
    )
    st.altair_chart(chart, width="stretch")
