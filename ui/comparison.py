"""Social competitor comparison page with live stored Instagram metrics."""

from __future__ import annotations

import calendar
import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from analytics.comparison import (
    account_comparison_rows,
    content_mix_rows,
    growth_overview,
    top_posts,
)
from analytics.daily_comparison import (
    INSUFFICIENT_DAILY_HISTORY,
    NO_SECOND_MEASUREMENT,
    NOT_MEASURED,
    build_daily_comparison,
    build_intraday_comparison,
    default_day_pair,
    format_published_posts_cell,
    list_measurement_days,
    summarize_daily_account_deltas,
)
from db.comparison_repository import (
    DEFAULT_COMPETITORS,
    ROLE_OWN,
    ensure_default_comparison_accounts,
    find_active_fetch_run,
    finish_fetch_run,
    get_discovery_rate_limit,
    get_latest_success_measured_at,
    get_preferences,
    list_comparison_accounts,
    normalize_username,
    save_preferences,
)
from db.database import get_connection, initialize_database
from meta.client import RATE_LIMIT_USER_MESSAGE
from sync.comparison import run_comparison_sync
from ui.comparison_chart import render_growth_chart
from ui.comparison_report import maybe_open_report_dialog, render_report_trigger

PAGE_TITLE = "Social vergelijking"
EMPTY_DATA_MESSAGE = "Nog geen gegevens opgehaald"
NOT_FETCHED_MESSAGE = "Nog niet opgehaald"
INSUFFICIENT_GROWTH = "Nog onvoldoende meetgeschiedenis"
# Daily tab missing values (never show 0 for absent measurements).
DAILY_NOT_MEASURED = NOT_MEASURED


MAX_COMPETITORS = 10
MAX_COMPETITOR_LENGTH = 80

ERROR_EMPTY = "Vul een accountnaam of gebruikersnaam in."
ERROR_DUPLICATE = "Deze concurrent staat al in de lijst."
ERROR_LIMIT = "Je kunt maximaal 10 concurrenten toevoegen."
ERROR_TOO_LONG = "Gebruik maximaal 80 tekens."

PERIOD_PRESETS: tuple[tuple[str, str], ...] = (
    ("7d", "Afgelopen 7 dagen"),
    ("30d", "Afgelopen 30 dagen"),
    ("3m", "Afgelopen 3 maanden"),
    ("custom", "Aangepaste periode"),
)
PERIOD_LABELS: tuple[str, ...] = tuple(label for _, label in PERIOD_PRESETS)
PERIOD_KEYS: dict[str, str] = {label: key for key, label in PERIOD_PRESETS}

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def normalize_competitor_name(value: str) -> str:
    return normalize_username(value)


def add_competitor(competitors: list[str], name: str) -> tuple[list[str], str | None]:
    cleaned = normalize_competitor_name(name)
    if not cleaned:
        return list(competitors), ERROR_EMPTY
    if len(cleaned) > MAX_COMPETITOR_LENGTH:
        return list(competitors), ERROR_TOO_LONG
    if len(competitors) >= MAX_COMPETITORS:
        return list(competitors), ERROR_LIMIT
    if cleaned.casefold() in {item.casefold() for item in competitors}:
        return list(competitors), ERROR_DUPLICATE
    return [*competitors, cleaned], None


def remove_competitor(competitors: list[str], name: str) -> list[str]:
    target = normalize_competitor_name(name)
    return [item for item in competitors if item.casefold() != target.casefold()]


def subtract_months(value: date, months: int) -> date:
    month_index = value.month - 1 - months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(value.day, last_day))


def resolve_period(
    period_key: str,
    *,
    today: date,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> tuple[date, date] | None:
    if period_key == "7d":
        return today - timedelta(days=6), today
    if period_key == "30d":
        return today - timedelta(days=29), today
    if period_key == "3m":
        return subtract_months(today, 3), today
    if period_key == "custom":
        if custom_start is None or custom_end is None:
            return None
        if custom_start > custom_end:
            return None
        return custom_start, custom_end
    return None


def _fmt_int(value: int | None) -> str:
    if value is None:
        return "n.b."
    return f"{value:,}".replace(",", ".")


def _fmt_measured(value: int | None) -> str:
    """Format a stored measurement; missing stays ‘Niet gemeten’, never 0."""
    if value is None:
        return DAILY_NOT_MEASURED
    return f"{value:,}".replace(",", ".")


def _fmt_delta(value: int | None) -> str:
    if value is None:
        return DAILY_NOT_MEASURED
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,}".replace(",", ".")


def _fmt_avg(value: float | None) -> str:
    if value is None:
        return "n.b."
    return f"{value:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(value: float | None) -> str:
    """Format growth % with enough decimals so small non-zero changes stay visible."""
    if value is None:
        return "n.b."
    sign = "+" if value > 0 else ""
    abs_v = abs(value)
    if abs_v == 0:
        text = "0,0"
    elif abs_v >= 1:
        text = f"{value:.1f}".replace(".", ",")
    elif abs_v >= 0.1:
        text = f"{value:.2f}".replace(".", ",")
    elif abs_v >= 0.01:
        text = f"{value:.3f}".replace(".", ",")
    else:
        text = f"{value:.4f}".replace(".", ",")
    return f"{sign}{text}%"


def _fmt_day(value: date | None) -> str:
    if value is None:
        return "n.b."
    return value.strftime("%d-%m-%Y")


def _account_sync_status_label(account) -> str:
    status = getattr(account, "status", "") or ""
    err = getattr(account, "error_message", None)
    resumed = bool(getattr(account, "resumed", False))
    verb = "hervat" if resumed else "vernieuwd"
    if status == "success" and getattr(account, "is_complete", False):
        return verb
    if status == "success":
        return f"{verb}, onvolledig (periode nog niet compleet)"
    if status == "partial":
        reason = f" — {err}" if err else ""
        return f"{verb}, onvolledig{reason}"
    if status == "skipped":
        reason = err or "geen toelichting"
        return f"overgeslagen ({reason})"
    if status == "failed":
        reason = err or "onbekende fout"
        return f"mislukt ({reason})"
    if status:
        return status if not err else f"{status} ({err})"
    return "onbekend"


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "n.b."
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        local = dt.astimezone(AMSTERDAM)
        return local.strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return value


def _fmt_date(value: str | None) -> str:
    if not value:
        return "n.b."
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(AMSTERDAM).strftime("%d-%m-%Y")
    except ValueError:
        return value[:10]


def _persist_preferences(own_account_id: int | None, competitors: list[str]) -> None:
    with get_connection() as conn:
        save_preferences(conn, own_account_id=own_account_id, competitors=competitors)
        conn.commit()


def _load_state() -> None:
    initialize_database()
    with get_connection() as conn:
        ensure_default_comparison_accounts(conn)
        own_id, competitors = get_preferences(conn)
        own_accounts = [
            a for a in list_comparison_accounts(conn, roles=[ROLE_OWN]) if a.access_status == "ok"
        ]
        if not own_accounts:
            own_accounts = [
                a
                for a in list_comparison_accounts(conn)
                if a.is_discovery_host and a.access_status == "ok"
            ]
        conn.commit()

    if "comparison_competitors" not in st.session_state:
        st.session_state.comparison_competitors = competitors or list(DEFAULT_COMPETITORS)
    if "comparison_notice" not in st.session_state:
        st.session_state.comparison_notice = None
    if "comparison_period" not in st.session_state:
        st.session_state.comparison_period = "Afgelopen 3 maanden"
    if "comparison_custom_range" not in st.session_state:
        start, end = resolve_period("3m", today=datetime.now(tz=AMSTERDAM).date())
        st.session_state.comparison_custom_range = (start, end)
    if "comparison_own_account_id" not in st.session_state:
        st.session_state.comparison_own_account_id = own_id
    if "comparison_adding_competitor" not in st.session_state:
        st.session_state.comparison_adding_competitor = False
    st.session_state["_comparison_own_accounts"] = own_accounts


def _on_add_competitor() -> None:
    current = list(st.session_state.get("comparison_competitors", []))
    updated, error = add_competitor(
        current,
        str(st.session_state.get("comparison_competitor_input", "")),
    )
    st.session_state.comparison_notice = error
    if error is None:
        st.session_state.comparison_competitors = updated
        st.session_state.comparison_competitor_input = ""
        st.session_state.comparison_adding_competitor = False
        _persist_preferences(st.session_state.get("comparison_own_account_id"), updated)


def _on_cancel_add_competitor() -> None:
    st.session_state.comparison_adding_competitor = False
    st.session_state.comparison_competitor_input = ""
    st.session_state.comparison_notice = None


def _on_show_add_competitor() -> None:
    if len(st.session_state.get("comparison_competitors", [])) >= MAX_COMPETITORS:
        return
    st.session_state.comparison_adding_competitor = True
    st.session_state.comparison_notice = None


def _on_remove_competitor(name: str) -> None:
    current = list(st.session_state.get("comparison_competitors", []))
    updated = remove_competitor(current, name)
    st.session_state.comparison_competitors = updated
    st.session_state.comparison_notice = None
    _persist_preferences(st.session_state.get("comparison_own_account_id"), updated)


def _custom_bounds(picked) -> tuple[date | None, date | None]:
    if isinstance(picked, (tuple, list)) and len(picked) == 2:
        start, end = picked
        if isinstance(start, date) and isinstance(end, date):
            return start, end
    return None, None


def _should_show_rate_limit_banner(rate_state, last_sync) -> bool:
    """True for an active Retry-After, or when the latest sync itself hit #4."""
    last_was_rate_limited = bool(
        last_sync is not None and getattr(last_sync, "rate_limited", False)
    )
    active = bool(rate_state is not None and getattr(rate_state, "active", False))
    return active or last_was_rate_limited


def _render_rate_limit_banner(rate_state) -> None:
    """Show only an active wait, or a rate-limit from the latest sync run.

    A successful later run must not keep an old Meta #4 / unknown-reset row
    looking like a current block. Real Retry-After windows still show.
    """
    with get_connection() as conn:
        last_success = get_latest_success_measured_at(conn)
    last = st.session_state.get("comparison_last_sync")
    if not _should_show_rate_limit_banner(rate_state, last):
        return

    active = bool(rate_state is not None and rate_state.active)
    measured = None
    if active and rate_state is not None:
        measured = rate_state.last_success_measured_at or last_success
    elif last is not None:
        measured = getattr(last, "last_success_measured_at", None) or last_success
    else:
        measured = last_success

    if active and rate_state is not None and rate_state.blocked_until:
        msg = f"{RATE_LIMIT_USER_MESSAGE} — wacht tot {_fmt_dt(rate_state.blocked_until)} (UTC)."
    else:
        # Latest sync hit #4 without a usable Retry-After (informational only).
        msg = f"{RATE_LIMIT_USER_MESSAGE} — resetmoment onbekend."
    if measured:
        msg += f" Laatste meting: {_fmt_dt(measured)}."
    st.warning(msg)


def _apply_sync_result(result, status, progress_bar) -> None:
    st.session_state.comparison_last_sync = result
    accounts = list(result.accounts or [])
    partial = [
        a
        for a in accounts
        if a.status == "partial"
        or (a.status == "success" and not getattr(a, "is_complete", False))
    ]
    skipped = [a for a in accounts if a.status == "skipped"]
    failed = [a for a in accounts if a.status == "failed"]
    all_ok = (
        bool(accounts)
        and not partial
        and not skipped
        and not failed
        and not result.rate_limited
        and result.status == "completed"
    )

    if result.rate_limited or result.status == "skipped":
        status.update(label="Rate limit / overgeslagen", state="error")
        msg = result.skipped_reason or result.error_message or RATE_LIMIT_USER_MESSAGE
        if result.rate_limited and not result.rate_limit_blocked_until:
            msg += " Resetmoment onbekend."
        if result.last_success_measured_at:
            msg += f" Laatste geslaagde meting: {_fmt_dt(result.last_success_measured_at)}."
        st.warning(msg)
    elif result.status == "failed":
        status.update(label="Mislukt", state="error")
        st.error(result.error_message or "Ophalen mislukt.")
    elif all_ok:
        status.update(label="Alle gekozen accounts vernieuwd", state="complete")
    else:
        status.update(label="Deels bijgewerkt", state="complete")
        st.warning("Niet alle accounts zijn volledig vernieuwd — zie status per account.")
    progress_bar.progress(1.0, text="Klaar")

    if accounts:
        st.markdown("**Status per account**")
        for account in accounts:
            label = _account_sync_status_label(account)
            posts = getattr(account, "posts_fetched", 0) or 0
            line = f"@{account.username}: {label}"
            if posts:
                line += f" · {posts} posts"
            status.write(line)
    elif result.status not in {"skipped"}:
        st.caption("Geen accountresultaten in deze run.")


def _sync_block_state() -> tuple[bool, object | None, int | None, str]:
    """Return (ui_busy, rate_state, active_run_id, block_reason).

    Resume/refresh are blocked only while a sync is truly running or a
    Meta Retry-After window is still active. A rate-limit row without end
    time is informational and must not permanently disable the buttons.
    """
    with get_connection() as conn:
        rate_state = get_discovery_rate_limit(conn)
        # Expires stale "running" rows when no worker is left.
        active_run_id = find_active_fetch_run(conn)
        if active_run_id is None:
            conn.commit()

    session_running = bool(st.session_state.get("comparison_sync_running"))
    if session_running and active_run_id is None:
        # Page reload / interrupted run left a sticky session flag.
        st.session_state.comparison_sync_running = False
        session_running = False

    rate_blocked = bool(rate_state is not None and rate_state.active)
    busy = session_running or active_run_id is not None

    if busy:
        reason = (
            f"Er loopt al een ophaalrun (id={active_run_id})."
            if active_run_id is not None
            else "Er loopt al een ophaalrun in dit scherm."
        )
    elif rate_blocked and rate_state is not None and rate_state.blocked_until:
        reason = (
            f"Meta-wachttijd actief tot {_fmt_dt(rate_state.blocked_until)} (UTC)."
        )
    else:
        reason = ""

    return busy, rate_state, active_run_id, reason


def _render_refresh() -> None:
    busy, rate_state, _active_run_id, block_reason = _sync_block_state()
    rate_blocked = bool(rate_state is not None and rate_state.active)
    buttons_disabled = bool(busy or rate_blocked)

    _render_rate_limit_banner(rate_state)

    col_refresh, col_report = st.columns(2)
    with col_refresh:
        clicked = st.button(
            "Gegevens vernieuwen",
            type="primary",
            use_container_width=True,
            disabled=buttons_disabled,
            key="comparison_refresh",
            help="Haalt metingen op voor ons account en alle concurrenten.",
        )
    with col_report:
        render_report_trigger()

    last = st.session_state.get("comparison_last_sync")
    if buttons_disabled:
        st.caption(block_reason or "Ophalen tijdelijk geblokkeerd.")
    elif last is not None:
        st.caption(f"Laatste run: {last.status} · {_fmt_dt(last.measured_at)}")

    if not clicked:
        return

    st.session_state.comparison_sync_running = True
    label = "Vergelijkingsgegevens ophalen (eigen account + concurrenten)…"
    progress_bar = st.progress(0.0, text=label)
    status = st.status(label, expanded=True)

    def on_progress(message: str, fraction: float | None = None) -> None:
        status.write(message)
        if fraction is not None:
            progress_bar.progress(min(max(fraction, 0.0), 1.0), text=message)

    try:
        result = run_comparison_sync(trigger_source="manual", progress=on_progress)
        _apply_sync_result(result, status, progress_bar)
    except Exception as exc:  # noqa: BLE001
        status.update(label="Mislukt", state="error")
        st.error(f"Vernieuwen mislukt: {exc}")
        # Belt-and-suspenders: never leave a DB run stuck on "running".
        try:
            with get_connection() as conn:
                active_id = find_active_fetch_run(conn)
                if active_id is not None:
                    finish_fetch_run(
                        conn,
                        active_id,
                        status="failed",
                        error_message=str(exc) or "Vernieuwen mislukt.",
                    )
                    conn.commit()
        except Exception:  # noqa: BLE001
            pass
    finally:
        st.session_state.comparison_sync_running = False


def _render_own_account_select() -> int | None:
    accounts = st.session_state.get("_comparison_own_accounts") or []
    if not accounts:
        st.markdown(
            '<div class="rro-comparison-label">Eigen account</div>',
            unsafe_allow_html=True,
        )
        st.caption("Nog geen eigen account — klik Gegevens vernieuwen.")
        return None

    labels = []
    label_to_id: dict[str, int] = {}
    for account in accounts:
        label = f"@{account.username}" if account.username else account.external_id
        if account.is_discovery_host:
            label = f"{label} (discovery-host)"
        labels.append(label)
        label_to_id[label] = account.account_id

    current_id = st.session_state.get("comparison_own_account_id")
    default_index = 0
    for index, label in enumerate(labels):
        if label_to_id[label] == current_id:
            default_index = index
            break

    selected = st.selectbox(
        "Eigen account",
        labels,
        index=default_index,
        key="comparison_own_account_label",
    )
    own_id = label_to_id[str(selected)]
    if own_id != current_id:
        st.session_state.comparison_own_account_id = own_id
        _persist_preferences(own_id, list(st.session_state.get("comparison_competitors", [])))
    return own_id


def _render_period_select() -> str:
    period_label = st.selectbox("Periode", PERIOD_LABELS, key="comparison_period")
    return PERIOD_KEYS[str(period_label)]


def _render_period_details(period_key: str) -> tuple[date, date] | None:
    custom_start = None
    custom_end = None
    if period_key == "custom":
        picked = st.date_input(
            "Aangepaste periode",
            format="YYYY/MM/DD",
            key="comparison_custom_range",
        )
        custom_start, custom_end = _custom_bounds(picked)
    today = datetime.now(tz=AMSTERDAM).date()
    bounds = resolve_period(
        period_key,
        today=today,
        custom_start=custom_start,
        custom_end=custom_end,
    )
    if bounds is None:
        st.caption("Kies een begin- en einddatum.")
        return None
    start, end = bounds
    st.caption(f"Periode: {start:%d-%m-%Y} t/m {end:%d-%m-%Y}.")
    return bounds


def _render_competitor_editor() -> None:
    competitors = list(st.session_state.comparison_competitors)
    at_limit = len(competitors) >= MAX_COMPETITORS
    if at_limit and st.session_state.get("comparison_adding_competitor"):
        st.session_state.comparison_adding_competitor = False
    adding = bool(st.session_state.get("comparison_adding_competitor")) and not at_limit
    st.markdown(
        '<div class="rro-comparison-label">Concurrenten'
        f"<span>{len(competitors)} / {MAX_COMPETITORS}</span></div>",
        unsafe_allow_html=True,
    )

    if not competitors:
        st.caption("Nog geen concurrenten toegevoegd.")
    else:
        for index, name in enumerate(competitors):
            name_col, remove_col = st.columns([5, 1.4], gap="small")
            with name_col:
                st.markdown(
                    f'<div class="rro-competitor-name">@{html.escape(name)}</div>',
                    unsafe_allow_html=True,
                )
            with remove_col:
                st.button(
                    "Verwijder",
                    use_container_width=True,
                    key=f"comparison_remove_{index}",
                    on_click=_on_remove_competitor,
                    args=(name,),
                )

    notice = st.session_state.get("comparison_notice")
    if notice:
        st.markdown(
            f'<div class="rro-comparison-error">{html.escape(str(notice))}</div>',
            unsafe_allow_html=True,
        )

    if adding and not at_limit:
        st.text_input(
            "Gebruikersnaam",
            placeholder="@account",
            key="comparison_competitor_input",
        )
        add_col, cancel_col, _ = st.columns([1.2, 1.2, 3], gap="small")
        with add_col:
            st.button(
                "Toevoegen",
                use_container_width=True,
                key="comparison_add_competitor",
                on_click=_on_add_competitor,
                type="primary",
            )
        with cancel_col:
            st.button(
                "Annuleren",
                use_container_width=True,
                key="comparison_cancel_add_competitor",
                on_click=_on_cancel_add_competitor,
            )
    else:
        _, plus_col = st.columns([5, 0.7], gap="small")
        with plus_col:
            st.button(
                "+",
                key="comparison_show_add_competitor",
                disabled=at_limit,
                on_click=_on_show_add_competitor,
                help="Concurrent toevoegen",
                use_container_width=True,
            )
        if at_limit:
            st.caption("Maximum van 10 concurrenten bereikt.")


def _render_account_block(
    rows,
    measured_at: str | None,
    incomplete: bool,
    *,
    period_start: date,
    period_end: date,
) -> None:
    st.markdown(
        '<h3 class="rro-comparison-section">Competitor Performance</h3>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"Opgehaalde cijfers voor posts van "
        f"{period_start:%d-%m-%Y} t/m {period_end:%d-%m-%Y}."
    )
    _ = measured_at, incomplete  # statuses remain in table cells
    if not rows:
        st.markdown(
            f'<div class="rro-empty-state">{html.escape(EMPTY_DATA_MESSAGE)}</div>',
            unsafe_allow_html=True,
        )
        return

    body_rows: list[str] = []
    for row in rows:
        metric_line = ""
        if row.post_metrics_measured_from and row.post_metrics_measured_to:
            if row.post_metrics_measured_from == row.post_metrics_measured_to:
                metric_line = (
                    f"<br><small>Postcijfers gemeten {_fmt_dt(row.post_metrics_measured_from)}</small>"
                )
            else:
                metric_line = (
                    "<br><small>Postcijfers gemeten "
                    f"{_fmt_dt(row.post_metrics_measured_from)} – "
                    f"{_fmt_dt(row.post_metrics_measured_to)}</small>"
                )
        if not row.has_fetched_data:
            body_rows.append(
                "<tr>"
                f"<td class='rro-account-cell'><strong>{html.escape(row.display_name)}</strong>"
                f"{metric_line}</td>"
                f"<td colspan='5' class='rro-muted'>{html.escape(NOT_FETCHED_MESSAGE)}</td>"
                "</tr>"
            )
            continue
        flags: list[str] = []
        if row.period_fetch_complete is False:
            flags.append("periode onvolledig")
        if row.metrics_incomplete:
            flags.append("cijfers onvolledig")
        flag_mark = f" · {' · '.join(flags)}" if flags else ""
        usable = (
            f"{row.posts_with_interaction}/{row.posts_in_period} posts met bruikbare cijfers"
        )
        followers_line = _fmt_int(row.followers)
        if row.followers_measured_at:
            followers_line += f"<br><small>gemeten {_fmt_date(row.followers_measured_at)}</small>"
        posts_cell = _fmt_int(row.posts_in_period)
        if row.period_fetch_complete is False:
            posts_cell = f"{posts_cell}<br><small>onvolledig</small>"
        body_rows.append(
            "<tr>"
            f"<td class='rro-account-cell'><strong>{html.escape(row.display_name)}</strong>"
            f"<br><small>{html.escape(usable)}{html.escape(flag_mark)}</small>"
            f"{metric_line}</td>"
            f"<td class='rro-num'>{followers_line}</td>"
            f"<td class='rro-num'>{posts_cell}</td>"
            f"<td class='rro-num'>{_fmt_int(row.likes_total)} / {_fmt_avg(row.likes_avg)}</td>"
            f"<td class='rro-num'>{_fmt_int(row.comments_total)} / {_fmt_avg(row.comments_avg)}</td>"
            f"<td class='rro-num'>{_fmt_avg(row.interaction_avg)}</td>"
            "</tr>"
        )
    st.markdown(
        "<div class='rro-comparison-table-wrap'>"
        "<table class='rro-comparison-table'>"
        "<thead><tr>"
        "<th>Account</th>"
        "<th class='rro-num'>Volgers</th>"
        "<th class='rro-num'>Posts</th>"
        "<th class='rro-num'>Likes tot/gem.</th>"
        "<th class='rro-num'>Reacties tot/gem.</th>"
        "<th class='rro-num'>Interactie / post</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )


def _render_content_block(
    rows,
    *,
    period_start: date,
    period_end: date,
) -> None:
    st.markdown(
        '<h3 class="rro-comparison-section">Competitor type of content</h3>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"Verdeling van geplaatste content van "
        f"{period_start:%d-%m-%Y} t/m {period_end:%d-%m-%Y}."
    )
    if not rows:
        st.markdown(
            f'<div class="rro-empty-state">{html.escape(EMPTY_DATA_MESSAGE)}</div>',
            unsafe_allow_html=True,
        )
        return
    body_rows: list[str] = []
    for row in rows:
        period_note = ""
        if row.period_fetch_complete is False:
            period_note = "<br><small>periode onvolledig</small>"
        if not row.has_fetched_data:
            body_rows.append(
                "<tr>"
                f"<td class='rro-account-cell'><strong>{html.escape(row.display_name)}</strong>"
                f"{period_note}</td>"
                f"<td colspan='5' class='rro-muted'>{html.escape(NOT_FETCHED_MESSAGE)}</td>"
                "</tr>"
            )
            continue
        body_rows.append(
            "<tr>"
            f"<td class='rro-account-cell'><strong>{html.escape(row.display_name)}</strong>"
            f"{period_note}</td>"
            f"<td class='rro-num'>{row.reels}</td>"
            f"<td class='rro-num'>{row.images}</td>"
            f"<td class='rro-num'>{row.carousels}</td>"
            f"<td class='rro-num'>{row.other_videos}</td>"
            f"<td class='rro-num'>{row.unknown}</td>"
            "</tr>"
        )
    st.markdown(
        "<div class='rro-comparison-table-wrap'>"
        "<table class='rro-comparison-table'>"
        "<thead><tr>"
        "<th>Account</th>"
        "<th class='rro-num'>Reels</th>"
        "<th class='rro-num'>Afbeeldingen</th>"
        "<th class='rro-num'>Carrousels</th>"
        "<th class='rro-num'>Overige video’s</th>"
        "<th class='rro-num'>Onbekend</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )


def _render_top_block(
    rows,
    incomplete: bool,
    measured_at: str | None,
    missing_accounts: list[str] | None = None,
    *,
    period_start: date,
    period_end: date,
) -> None:
    st.markdown(
        '<h3 class="rro-comparison-section">Score List</h3>',
        unsafe_allow_html=True,
    )
    st.caption(
        f"De 20 posts met de meeste likes en reacties van "
        f"{period_start:%d-%m-%Y} t/m {period_end:%d-%m-%Y}."
    )
    _ = measured_at, incomplete, missing_accounts  # statuses remain on post rows
    if not rows:
        st.markdown(
            f'<div class="rro-empty-state">{html.escape(EMPTY_DATA_MESSAGE)}</div>',
            unsafe_allow_html=True,
        )
        return
    for index, row in enumerate(rows, start=1):
        link = ""
        if row.permalink:
            link = (
                f"<a class='rro-btn-link' href='{html.escape(row.permalink)}' "
                "target='_blank' rel='noopener noreferrer'>Open post</a>"
            )
        measured_line = (
            f"<br><small>gemeten {_fmt_dt(row.measured_at)}</small>"
            if row.measured_at
            else "<br><small>gemeten n.b.</small>"
        )
        st.markdown(
            "<div class='rro-comparison-top-row'>"
            f"<div class='rro-comparison-top-rank'>#{index}</div>"
            "<div>"
            f"<strong>{html.escape(row.account_name)}</strong> · "
            f"{html.escape(_fmt_date(row.published_at))} · "
            f"{html.escape(row.content_type)}"
            f"<br>Likes {_fmt_int(row.likes)} · Reacties {_fmt_int(row.comments)} · "
            f"Interactie {_fmt_int(row.interaction)}"
            f"{measured_line}"
            f"<br>{link}"
            "</div></div>",
            unsafe_allow_html=True,
        )


def _render_account_delta_table(rows, *, show_exact_times: bool = True) -> None:
    """Growth table: geplaatste posts + follower/engagement deltas."""
    _ = show_exact_times  # timestamps always shown on the account cell
    if not rows:
        st.markdown(
            f'<div class="rro-empty-state">{html.escape(EMPTY_DATA_MESSAGE)}</div>',
            unsafe_allow_html=True,
        )
        return

    body_rows: list[str] = []
    for row in rows:
        published_cell = html.escape(
            format_published_posts_cell(
                getattr(row, "published_posts_count", None),
                getattr(row, "published_posts_coverage", "unknown"),
            )
        )
        if row.insufficient:
            followers_cell = html.escape(row.missing_note or NO_SECOND_MEASUREMENT)
            likes_cell = "—"
            comments_cell = "—"
            coverage = html.escape(NO_SECOND_MEASUREMENT)
        else:
            followers_cell = html.escape(_fmt_delta(row.followers_delta))
            if row.comparable_posts == 0:
                likes_cell = "—"
                comments_cell = "—"
            else:
                likes_cell = html.escape(_fmt_delta(row.likes_delta))
                comments_cell = html.escape(_fmt_delta(row.comments_delta))
            coverage = f"{row.comparable_posts} posts vergelijkbaar"
            if row.posts_only_start or row.posts_only_end:
                coverage += (
                    f" · deels "
                    f"({row.posts_only_start}/{row.posts_only_end} éénzijdig)"
                )
            if row.engagement_incomplete:
                coverage += " · sommige cijfers ontbreken"
            coverage = html.escape(coverage)

        moments = (
            f"{_fmt_dt(row.start_measured_at)} → {_fmt_dt(row.end_measured_at)}"
        )

        body_rows.append(
            "<tr>"
            f"<td class='rro-account-cell'><strong>{html.escape(row.display_name)}</strong>"
            f"<br><small>{html.escape(moments)}</small>"
            f"<br><small>{coverage}</small></td>"
            f"<td class='rro-num'>{published_cell}</td>"
            f"<td class='rro-num'>{followers_cell}</td>"
            f"<td class='rro-num'>{likes_cell}</td>"
            f"<td class='rro-num'>{comments_cell}</td>"
            "</tr>"
        )

    st.markdown(
        "<div class='rro-comparison-table-wrap'>"
        "<table class='rro-comparison-table'>"
        "<thead><tr>"
        "<th>Account</th>"
        "<th class='rro-num'>Geplaatste posts</th>"
        "<th class='rro-num'>Δ Volgers</th>"
        "<th class='rro-num'>Δ Likes</th>"
        "<th class='rro-num'>Δ Reacties</th>"
        "</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>",
        unsafe_allow_html=True,
    )


def _format_day_label(day: date) -> str:
    return day.strftime("%d-%m-%Y")


def _render_growth_block(
    own_id: int | None, competitors: list[str], overview
) -> dict:
    """Render Growth table + chart. Returns selection context for the PDF form."""
    st.markdown(
        '<h3 class="rro-comparison-section">Growth</h3>',
        unsafe_allow_html=True,
    )
    tracking = overview.tracking_since
    since_label = _fmt_day(tracking) if tracking is not None else "n.b."
    st.caption(f"Verschil tussen opgeslagen meetmomenten (sinds {since_label}).")

    mode = st.radio(
        "Vergelijkingsmodus",
        options=["Per dag", "Binnen één dag"],
        horizontal=True,
        key="comparison_growth_mode",
        help="Per dag of eerste vs laatste meting binnen één dag.",
    )

    context: dict = {
        "growth_mode": mode,
        "growth_day1": None,
        "growth_day2": None,
        "growth_intraday_day": None,
        "chart_metric": "growth_abs",
    }
    days = list_measurement_days()
    if not days:
        st.markdown(
            f'<div class="rro-empty-state">{html.escape(EMPTY_DATA_MESSAGE)}</div>',
            unsafe_allow_html=True,
        )
        return context

    if mode == "Per dag":
        if len(days) < 2:
            st.markdown(
                f'<div class="rro-empty-state">{html.escape(INSUFFICIENT_DAILY_HISTORY)}</div>',
                unsafe_allow_html=True,
            )
            return context
        default_prev, default_latest = default_day_pair(days)
        assert default_prev is not None and default_latest is not None
        labels = [_format_day_label(day) for day in days]
        by_label = {label: day for label, day in zip(labels, days)}
        col_a, col_b = st.columns(2, gap="small")
        with col_a:
            day1_label = st.selectbox(
                "Eerdere meetdag",
                options=labels,
                index=labels.index(_format_day_label(default_prev)),
                key="comparison_growth_day1",
            )
        with col_b:
            day2_label = st.selectbox(
                "Latere meetdag",
                options=labels,
                index=labels.index(_format_day_label(default_latest)),
                key="comparison_growth_day2",
            )
        day1 = by_label[str(day1_label)]
        day2 = by_label[str(day2_label)]
        context["growth_day1"] = day1
        context["growth_day2"] = day2
        if day1 == day2:
            st.warning("Kies twee verschillende meetdagen.")
            return context
        result = build_daily_comparison(
            own_account_id=own_id,
            competitor_usernames=competitors,
            day1=day1,
            day2=day2,
        )
        _render_account_delta_table(
            summarize_daily_account_deltas(result),
            show_exact_times=True,
        )
        render_growth_chart(
            own_account_id=own_id,
            competitor_usernames=competitors,
            mode="daily",
            day1=day1,
            day2=day2,
        )
        return context

    # Binnen één dag
    labels = [_format_day_label(day) for day in days]
    by_label = {label: day for label, day in zip(labels, days)}
    day_label = st.selectbox(
        "Meetdag",
        options=labels,
        index=len(labels) - 1,
        key="comparison_growth_intraday_day",
    )
    day = by_label[str(day_label)]
    context["growth_intraday_day"] = day
    intraday = build_intraday_comparison(
        own_account_id=own_id,
        competitor_usernames=competitors,
        day=day,
    )
    _render_account_delta_table(intraday.account_rows, show_exact_times=True)
    render_growth_chart(
        own_account_id=own_id,
        competitor_usernames=competitors,
        mode="intraday",
        intraday_day=day,
    )
    return context


def _render_comparison_body(own_id: int | None, competitors: list[str]) -> None:
    """Single-page period blocks + growth (read-only from SQLite)."""
    period_key = _render_period_select()
    bounds = _render_period_details(period_key)
    if bounds is None:
        return
    start, end = bounds

    account_rows, measured_at, account_incomplete = account_comparison_rows(
        own_account_id=own_id,
        competitor_usernames=competitors,
        period_start=start,
        period_end=end,
    )
    mix_rows = content_mix_rows(
        own_account_id=own_id,
        competitor_usernames=competitors,
        period_start=start,
        period_end=end,
    )
    top_rows, top_incomplete, top_missing = top_posts(
        own_account_id=own_id,
        competitor_usernames=competitors,
        period_start=start,
        period_end=end,
    )
    grow = growth_overview(own_account_id=own_id, competitor_usernames=competitors)

    _render_account_block(
        account_rows,
        measured_at,
        account_incomplete,
        period_start=start,
        period_end=end,
    )
    _render_content_block(mix_rows, period_start=start, period_end=end)
    _render_top_block(
        top_rows,
        top_incomplete,
        measured_at,
        top_missing,
        period_start=start,
        period_end=end,
    )
    growth_ctx = _render_growth_block(own_id, competitors, grow)
    maybe_open_report_dialog(
        own_id=own_id,
        competitors=competitors,
        period_start=start,
        period_end=end,
        growth_mode=str(growth_ctx.get("growth_mode") or "Per dag"),
        growth_day1=growth_ctx.get("growth_day1"),
        growth_day2=growth_ctx.get("growth_day2"),
        growth_intraday_day=growth_ctx.get("growth_intraday_day"),
        chart_metric="growth_abs",
    )


def render_comparison_page() -> None:
    """Render comparison workspace with stored Instagram metrics."""
    _load_state()
    _render_refresh()

    own_id = _render_own_account_select()
    _render_competitor_editor()
    competitors = list(st.session_state.get("comparison_competitors", []))
    _render_comparison_body(own_id, competitors)
