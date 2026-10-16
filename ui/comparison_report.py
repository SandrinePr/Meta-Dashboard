"""PDF report builder UI for Social vergelijking (no Meta requests)."""

from __future__ import annotations

import base64
from datetime import date

import streamlit as st
import streamlit.components.v1 as components

from analytics.comparison import growth_overview
from reports.comparison_pdf import ReportSelection, build_comparison_pdf

SECTION_OPTIONS = (
    ("performance", "Competitor Performance"),
    ("content", "Competitor type of content"),
    ("score_list", "Score List"),
    ("growth", "Growth"),
)
METRIC_OPTIONS = (
    ("followers", "Volgers"),
    ("posts", "Geplaatste posts"),
    ("likes", "Likes"),
    ("comments", "Reacties"),
    ("interaction", "Interactie en gemiddelden"),
    ("growth", "Groei (aantallen en procenten)"),
)


def _own_label(own_id: int | None, own_accounts: list) -> str:
    for account in own_accounts:
        if account.account_id == own_id:
            if account.username:
                return f"@{account.username}"
            return account.name or account.external_id
    return "Ons eigen account"


def _dismiss_report_dialog() -> None:
    st.session_state.comparison_report_open = False


def render_report_trigger() -> None:
    """Secondary top-bar control that opens the report dialog."""
    if st.button(
        "Rapport samenstellen",
        type="secondary",
        use_container_width=True,
        key="comparison_report_open_btn",
        help="Stel een PDF-rapport samen uit opgeslagen gegevens (geen Meta-aanvragen).",
    ):
        st.session_state.comparison_report_open = True


def maybe_open_report_dialog(
    *,
    own_id: int | None,
    competitors: list[str],
    period_start: date,
    period_end: date,
    growth_mode: str,
    growth_day1: date | None,
    growth_day2: date | None,
    growth_intraday_day: date | None,
    chart_metric: str = "growth_abs",
) -> None:
    """Open the report dialog when the top-bar button requested it."""
    if not st.session_state.get("comparison_report_open"):
        return
    _report_dialog(
        own_id=own_id,
        competitors=competitors,
        period_start=period_start,
        period_end=period_end,
        growth_mode=growth_mode,
        growth_day1=growth_day1,
        growth_day2=growth_day2,
        growth_intraday_day=growth_intraday_day,
        chart_metric=chart_metric,
    )


@st.dialog("Rapport samenstellen", width="large", on_dismiss=_dismiss_report_dialog)
def _report_dialog(
    *,
    own_id: int | None,
    competitors: list[str],
    period_start: date,
    period_end: date,
    growth_mode: str,
    growth_day1: date | None,
    growth_day2: date | None,
    growth_intraday_day: date | None,
    chart_metric: str = "growth_abs",
) -> None:
    """Report choices in a dialog; one-click PDF download from stored data only."""
    own_accounts = list(st.session_state.get("_comparison_own_accounts") or [])
    own_name = _own_label(own_id, own_accounts)
    generating = bool(st.session_state.get("comparison_report_generating"))

    st.markdown("**Periode**")
    period_range = st.date_input(
        "Publicatieperiode",
        value=(period_start, period_end),
        format="YYYY/MM/DD",
        key="comparison_report_period",
        disabled=generating,
    )
    report_start, report_end = period_start, period_end
    if isinstance(period_range, (tuple, list)) and len(period_range) == 2:
        if isinstance(period_range[0], date) and isinstance(period_range[1], date):
            report_start, report_end = period_range[0], period_range[1]

    # Growth follows the dashboard comparison settings (no separate controls here).
    report_growth_mode = growth_mode
    report_day1, report_day2, report_intra = growth_day1, growth_day2, growth_intraday_day

    col_accounts, col_sections, col_metrics = st.columns(3)

    with col_accounts:
        st.markdown("**Accounts**")
        include_own = st.checkbox(
            own_name,
            value=True,
            key="comparison_report_own",
            disabled=generating,
        )
        selected_competitors: list[str] = []
        for name in competitors:
            checked = st.checkbox(
                f"@{name}",
                value=True,
                key=f"comparison_report_comp_{name}",
                disabled=generating,
            )
            if checked:
                selected_competitors.append(name)

    with col_sections:
        st.markdown("**Onderdelen**")
        selected_sections: list[str] = []
        for key, label in SECTION_OPTIONS:
            if st.checkbox(
                label,
                value=True,
                key=f"comparison_report_section_{key}",
                disabled=generating,
            ):
                selected_sections.append(key)
        include_chart = st.checkbox(
            "Growth-grafiek opnemen",
            value=True,
            key="comparison_report_include_chart",
            disabled=generating,
        )

    with col_metrics:
        st.markdown("**Cijfers**")
        selected_metrics: list[str] = []
        for key, label in METRIC_OPTIONS:
            if st.checkbox(
                label,
                value=True,
                key=f"comparison_report_metric_{key}",
                disabled=generating,
            ):
                selected_metrics.append(key)

    grow = growth_overview(own_account_id=own_id, competitor_usernames=competitors)
    selection = ReportSelection(
        include_own=bool(include_own),
        competitor_usernames=tuple(selected_competitors),
        sections=tuple(selected_sections),
        metrics=tuple(selected_metrics),
        period_start=report_start,
        period_end=report_end,
        growth_mode=report_growth_mode,
        growth_day1=report_day1,
        growth_day2=report_day2,
        growth_intraday_day=report_intra,
        include_growth_chart=bool(include_chart and "growth" in selected_sections),
        chart_metric=chart_metric,
        own_account_id=own_id,
        tracking_since=grow.tracking_since,
    )
    fingerprint = selection.fingerprint()

    # Invalidate any previously generated PDF when the selection changes.
    prev = st.session_state.get("comparison_report_fingerprint")
    if prev != fingerprint:
        st.session_state.comparison_report_fingerprint = fingerprint
        st.session_state.comparison_report_ready_pdf = None
        st.session_state.comparison_report_ready_fp = None
        st.session_state.comparison_report_do_download = False

    has_account = include_own or bool(selected_competitors)
    has_section = bool(selected_sections)
    invalid = not has_account or not has_section
    if invalid:
        st.warning("Selecteer minimaal één account en één onderdeel.")

    error = st.session_state.get("comparison_report_error")
    if error:
        st.error(error)

    if generating:
        st.info("PDF wordt gemaakt…")

    clicked = st.button(
        "PDF downloaden",
        type="primary",
        use_container_width=True,
        disabled=invalid or generating,
        key="comparison_report_download_btn",
    )
    if clicked:
        st.session_state.comparison_report_generating = True
        st.session_state.comparison_report_error = None
        st.session_state.comparison_report_pending_selection = selection
        st.session_state.comparison_report_pending_fp = fingerprint
        st.rerun()

    if st.session_state.get("comparison_report_generating"):
        pending = st.session_state.get("comparison_report_pending_selection")
        pending_fp = st.session_state.get("comparison_report_pending_fp")
        with st.spinner("PDF wordt gemaakt…"):
            try:
                if pending is None or pending_fp != fingerprint:
                    raise RuntimeError("Selectie gewijzigd — probeer opnieuw.")
                pdf_bytes = build_comparison_pdf(pending)
                if not pdf_bytes:
                    raise RuntimeError("PDF is leeg.")
                st.session_state.comparison_report_ready_pdf = pdf_bytes
                st.session_state.comparison_report_ready_fp = pending_fp
                st.session_state.comparison_report_ready_name = (
                    f"social-vergelijking-{pending_fp}.pdf"
                )
                st.session_state.comparison_report_do_download = True
                st.session_state.comparison_report_error = None
            except Exception as exc:  # noqa: BLE001
                st.session_state.comparison_report_ready_pdf = None
                st.session_state.comparison_report_ready_fp = None
                st.session_state.comparison_report_do_download = False
                st.session_state.comparison_report_error = f"PDF maken mislukt: {exc}"
            finally:
                st.session_state.comparison_report_generating = False
                st.session_state.comparison_report_pending_selection = None
        st.rerun()

    if (
        st.session_state.get("comparison_report_do_download")
        and st.session_state.get("comparison_report_ready_fp") == fingerprint
        and st.session_state.get("comparison_report_ready_pdf")
    ):
        pdf_bytes = st.session_state.comparison_report_ready_pdf
        file_name = st.session_state.get(
            "comparison_report_ready_name",
            f"social-vergelijking-{fingerprint}.pdf",
        )
        # One-shot: clear before embedding so reruns do not re-download.
        st.session_state.comparison_report_do_download = False
        b64 = base64.b64encode(pdf_bytes).decode("ascii")
        safe_name = str(file_name).replace('"', "")
        components.html(
            f"""
            <a id="rro-comparison-pdf-dl"
               href="data:application/pdf;base64,{b64}"
               download="{safe_name}"></a>
            <script>
              const link = document.getElementById("rro-comparison-pdf-dl");
              if (link) {{ link.click(); }}
            </script>
            """,
            height=0,
        )
