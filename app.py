"""Streamlit entrypoint for the local search dashboard."""

from __future__ import annotations

import logging

import streamlit as st
from dotenv import load_dotenv

from config import get_settings
from db.database import initialize_database
from search.engine import search
from sync import get_sync_summary, run_sync
from ui.components import (
    MIN_QUERY_LENGTH,
    inject_styles,
    render_results_section,
    render_search_form,
    render_sidebar_stats,
    render_sync_result,
)

logger = logging.getLogger(__name__)


def _run_dashboard_sync(progress=None):
    """Fast Meta sync: all posts/likes, recent comments + Insights only."""
    load_dotenv(override=True)
    get_settings()
    # Default is already fast mode (full=False); omit kwarg so a stale Streamlit
    # process that hasn't reloaded orchestrator yet doesn't TypeError on `full`.
    return run_sync(platform="all", progress=progress)


def main() -> None:
    st.set_page_config(
        page_title="Social Media Search Dashboard",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_styles()
    logging.basicConfig(level=logging.INFO)

    st.title("Social Media Search Dashboard")
    st.caption("Zoek lokaal in gesynchroniseerde Instagram- en Facebook-content.")

    initialize_database()
    totals = get_sync_summary()

    if "last_sync_stats" not in st.session_state:
        st.session_state.last_sync_stats = None

    with st.sidebar:
        render_sidebar_stats(totals)
        render_sync_result(st.session_state.last_sync_stats)

        sync_clicked = st.button(
            "Synchroniseer Meta",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.get("sync_running", False),
        )
        if sync_clicked:
            st.session_state.sync_running = True
            progress_bar = st.progress(0.0, text="Sync starten…")
            status = st.status("Meta synchroniseren…", expanded=True)

            def on_progress(message: str, fraction: float | None = None) -> None:
                status.write(message)
                if fraction is not None:
                    progress_bar.progress(
                        min(max(fraction, 0.0), 1.0),
                        text=message,
                    )

            try:
                stats = _run_dashboard_sync(progress=on_progress)
                st.session_state.last_sync_stats = stats
                status.update(label="Synchronisatie voltooid", state="complete")
                progress_bar.progress(1.0, text="Klaar")
                logger.info(
                    "Dashboard sync done: ig_posts+%s/%s ig_comments+%s/%s "
                    "fb_posts+%s/%s fb_comments+%s/%s insights_ok=%s insights_failed=%s "
                    "errors=%s",
                    stats.instagram_posts_added,
                    stats.instagram_posts_updated,
                    stats.instagram_comments_added,
                    stats.instagram_comments_updated,
                    stats.facebook_posts_added,
                    stats.facebook_posts_updated,
                    stats.facebook_comments_added,
                    stats.facebook_comments_updated,
                    stats.insights_ok,
                    stats.insights_failed,
                    len(stats.errors),
                )
            except Exception as exc:
                st.session_state.last_sync_stats = None
                status.update(label="Synchronisatie mislukt", state="error")
                st.error(f"Synchronisatie mislukt: {exc}")
                logger.exception("Dashboard sync failed")
            finally:
                st.session_state.sync_running = False
            st.rerun()

    query, platforms, entity_types, date_range = render_search_form()
    trimmed = query.strip()

    if not trimmed:
        return

    if len(trimmed) < MIN_QUERY_LENGTH:
        st.info(f"Typ minstens {MIN_QUERY_LENGTH} tekens om te zoeken.")
        return

    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range
    elif isinstance(date_range, tuple) and len(date_range) == 1:
        start_date = end_date = date_range[0]
    else:
        start_date = None
        end_date = None

    results = search(
        trimmed,
        platforms=platforms,
        entity_types=entity_types,
        start_date=start_date,
        end_date=end_date,
        limit=50,
    )
    render_results_section(
        results,
        query=trimmed,
    )


if __name__ == "__main__":
    main()
