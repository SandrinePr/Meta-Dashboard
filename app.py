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


def _run_dashboard_sync():
    """Run the same sync flow as `sync.py --all`."""
    load_dotenv(override=True)
    get_settings()
    return run_sync(platform="all")


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

        if st.button("Synchroniseer Meta", type="primary", use_container_width=True):
            with st.spinner("Meta-data synchroniseren..."):
                try:
                    stats = _run_dashboard_sync()
                    st.session_state.last_sync_stats = stats
                    logger.info(
                        "Dashboard sync done: ig_posts+%s/%s ig_comments+%s/%s "
                        "fb_posts+%s/%s fb_comments+%s/%s errors=%s",
                        stats.instagram_posts_added,
                        stats.instagram_posts_updated,
                        stats.instagram_comments_added,
                        stats.instagram_comments_updated,
                        stats.facebook_posts_added,
                        stats.facebook_posts_updated,
                        stats.facebook_comments_added,
                        stats.facebook_comments_updated,
                        len(stats.errors),
                    )
                except Exception as exc:
                    st.session_state.last_sync_stats = None
                    st.error(f"Synchronisatie mislukt: {exc}")
                    logger.exception("Dashboard sync failed")
                else:
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
    content_count = sum(1 for r in results if r.entity_type == "post")
    comment_count = sum(1 for r in results if r.entity_type == "comment")
    render_results_section(
        results,
        query=trimmed,
        content_count=content_count,
        comment_count=comment_count,
    )


if __name__ == "__main__":
    main()
