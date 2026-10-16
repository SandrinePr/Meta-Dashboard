"""PDF report for Social vergelijking — SQLite data only, no Meta requests."""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Sequence
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from analytics.comparison import (
    account_comparison_rows,
    content_mix_rows,
    growth_overview,
    top_posts,
)
from analytics.daily_comparison import (
    NOT_MEASURED,
    build_daily_comparison,
    build_intraday_comparison,
    format_published_posts_cell,
    summarize_daily_account_deltas,
)
from analytics.growth_series import GrowthSeriesResult, SeriesMode, build_growth_series

AMSTERDAM = ZoneInfo("Europe/Amsterdam")

ORANGE = colors.Color(0.91, 0.47, 0.13)  # #E87722-ish
DARK = colors.Color(0.12, 0.14, 0.18)
MUTED = colors.Color(0.35, 0.38, 0.42)
LINE = colors.Color(0.85, 0.86, 0.88)
WHITE = colors.white


@dataclass(frozen=True, slots=True)
class ReportSelection:
    """User choices for a comparison PDF."""

    include_own: bool
    competitor_usernames: tuple[str, ...]
    sections: tuple[str, ...]  # performance, content, score_list, growth
    metrics: tuple[str, ...]  # followers, posts, likes, comments, interaction, growth
    period_start: date
    period_end: date
    growth_mode: str  # "Per dag" | "Binnen één dag"
    growth_day1: date | None
    growth_day2: date | None
    growth_intraday_day: date | None
    include_growth_chart: bool
    chart_metric: str = "growth_abs"  # fixed: follower growth in counts
    own_account_id: int | None = None
    tracking_since: date | None = None

    def fingerprint(self) -> str:
        payload = asdict(self)
        for key in ("period_start", "period_end", "growth_day1", "growth_day2", "growth_intraday_day", "tracking_since"):
            val = payload.get(key)
            if isinstance(val, date):
                payload[key] = val.isoformat()
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _fmt_day(value: date | None) -> str:
    if value is None:
        return "n.b."
    return value.strftime("%d-%m-%Y")


def _fmt_dt(value: str | None) -> str:
    if not value:
        return "n.b."
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(AMSTERDAM).strftime("%d-%m-%Y %H:%M")
    except ValueError:
        return str(value)[:16]


def _fmt_int(value: int | float | None) -> str:
    if value is None:
        return "n.b."
    return f"{int(round(value)):,}".replace(",", ".")


def _fmt_avg(value: float | None) -> str:
    if value is None:
        return "n.b."
    return f"{value:.1f}".replace(".", ",")


def _fmt_delta(value: int | None) -> str:
    if value is None:
        return "n.b."
    sign = "+" if value > 0 else ""
    return f"{sign}{value:,}".replace(",", ".")


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "n.b."
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%".replace(".", ",")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "RroTitle",
            parent=base["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=18,
            textColor=DARK,
            spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "RroH2",
            parent=base["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=13,
            textColor=DARK,
            spaceBefore=14,
            spaceAfter=4,
            borderPadding=2,
        ),
        "caption": ParagraphStyle(
            "RroCaption",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=MUTED,
            spaceAfter=8,
        ),
        "body": ParagraphStyle(
            "RroBody",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=9,
            textColor=DARK,
            leading=12,
        ),
        "note": ParagraphStyle(
            "RroNote",
            parent=base["Normal"],
            fontName="Helvetica-Oblique",
            fontSize=8,
            textColor=MUTED,
            spaceBefore=2,
            spaceAfter=6,
        ),
        "link": ParagraphStyle(
            "RroLink",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=ORANGE,
        ),
        "footer": ParagraphStyle(
            "RroFooter",
            parent=base["Normal"],
            fontName="Helvetica",
            fontSize=8,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
    }


def _table(data: list[list[Any]], col_widths: Sequence[float] | None = None) -> Table:
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.96, 0.96, 0.97)),
        ("TEXTCOLOR", (0, 0), (-1, 0), DARK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 1), (-1, -1), DARK),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "LEFT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, ORANGE),
    ]
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _selected_own_and_competitors(selection: ReportSelection) -> tuple[int | None, list[str]]:
    own_id = selection.own_account_id if selection.include_own else None
    return own_id, list(selection.competitor_usernames)


def _render_chart_png(series_result: GrowthSeriesResult) -> bytes | None:
    """Render standard % follower-growth chart for PDF embedding."""
    usable = [s for s in series_result.series if s.chartable and len(s.points) >= 2]
    if not usable:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    fig, ax = plt.subplots(figsize=(9.5, 4.2), dpi=120)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    axis_keys: list[str] = []
    axis_labels: dict[str, str] = {}
    for account in usable:
        for point in account.points:
            if point.measured_at not in axis_labels:
                axis_keys.append(point.measured_at)
                axis_labels[point.measured_at] = point.label
    axis_keys.sort()
    x_index = {key: i for i, key in enumerate(axis_keys)}
    for account in usable:
        xs = [x_index[p.measured_at] for p in account.points]
        ys = [p.growth_abs for p in account.points]
        ax.plot(
            xs,
            ys,
            marker="o",
            color=account.color,
            label=account.display_name,
            linewidth=2,
        )
    ax.set_xticks(list(range(len(axis_keys))))
    ax.set_xticklabels(
        [axis_labels[k] for k in axis_keys],
        rotation=30,
        ha="right",
        fontsize=7,
    )
    ax.tick_params(colors="#2A2E35")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(fontsize=7, frameon=False)
    ax.grid(True, axis="y", color="#E6E8EB", linewidth=0.6)
    ax.set_ylabel("Volgersgroei", color="#2A2E35", fontsize=8)
    ax.set_xlabel("Meetdatum", color="#2A2E35", fontsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def build_comparison_pdf(selection: ReportSelection) -> bytes:
    """Build a PDF bytes object from stored SQLite metrics only."""
    styles = _styles()
    own_id, competitors = _selected_own_and_competitors(selection)
    buffer = io.BytesIO()

    def _footer(canvas, doc) -> None:
        canvas.saveState()
        canvas.setStrokeColor(ORANGE)
        canvas.setLineWidth(1)
        canvas.line(18 * mm, 12 * mm, A4[0] - 18 * mm, 12 * mm)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawCentredString(A4[0] / 2, 7 * mm, f"Pagina {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title="Social vergelijking — rapport",
    )
    story: list[Any] = []
    story.append(Paragraph("Social vergelijking", styles["title"]))
    story.append(
        Paragraph(
            f"Publicatieperiode: {_fmt_day(selection.period_start)} t/m {_fmt_day(selection.period_end)}.",
            styles["caption"],
        )
    )
    if selection.tracking_since is not None:
        story.append(
            Paragraph(
                f"Meetgeschiedenis Growth sinds {_fmt_day(selection.tracking_since)}.",
                styles["caption"],
            )
        )

    metrics = set(selection.metrics)
    sections = set(selection.sections)

    if "performance" in sections:
        rows, measured_at, incomplete = account_comparison_rows(
            own_account_id=own_id,
            competitor_usernames=competitors,
            period_start=selection.period_start,
            period_end=selection.period_end,
        )
        story.append(Paragraph("Competitor Performance", styles["h2"]))
        story.append(
            Paragraph(
                f"Opgehaalde cijfers voor posts van {_fmt_day(selection.period_start)} "
                f"t/m {_fmt_day(selection.period_end)}.",
                styles["caption"],
            )
        )
        if measured_at:
            story.append(Paragraph(f"Meetmoment (ophaal): {_fmt_dt(measured_at)}.", styles["note"]))
        notes: list[str] = []
        if incomplete:
            notes.append("Sommige totalen zijn onvolledig (n.b. i.p.v. 0).")
        if any(r.period_fetch_complete is False for r in rows):
            notes.append("Een of meer accounts hebben een onvolledige periode-ophaling.")
        for note in notes:
            story.append(Paragraph(note, styles["note"]))

        header = ["Account"]
        if "followers" in metrics:
            header.append("Volgers")
        if "posts" in metrics:
            header.append("Posts")
        if "likes" in metrics:
            header.append("Likes tot/gem.")
        if "comments" in metrics:
            header.append("Reacties tot/gem.")
        if "interaction" in metrics:
            header.append("Interactie / post")
        data: list[list[Any]] = [header]
        for row in rows:
            if not row.has_fetched_data:
                cells: list[Any] = [row.display_name]
                cells.extend(["n.b."] * (len(header) - 1))
                data.append(cells)
                continue
            cells = [Paragraph(row.display_name, styles["body"])]
            if "followers" in metrics:
                cell = _fmt_int(row.followers)
                if row.followers_measured_at:
                    cell += f"<br/><font size='7' color='#5A5F66'>gemeten {_fmt_dt(row.followers_measured_at)}</font>"
                cells.append(Paragraph(cell, styles["body"]))
            if "posts" in metrics:
                cells.append(_fmt_int(row.posts_in_period))
            if "likes" in metrics:
                cells.append(f"{_fmt_int(row.likes_total)} / {_fmt_avg(row.likes_avg)}")
            if "comments" in metrics:
                cells.append(f"{_fmt_int(row.comments_total)} / {_fmt_avg(row.comments_avg)}")
            if "interaction" in metrics:
                cells.append(_fmt_avg(row.interaction_avg))
            data.append(cells)
        story.append(_table(data))

    if "content" in sections:
        mix = content_mix_rows(
            own_account_id=own_id,
            competitor_usernames=competitors,
            period_start=selection.period_start,
            period_end=selection.period_end,
        )
        story.append(Paragraph("Competitor type of content", styles["h2"]))
        story.append(
            Paragraph(
                f"Verdeling van geplaatste content van {_fmt_day(selection.period_start)} "
                f"t/m {_fmt_day(selection.period_end)}.",
                styles["caption"],
            )
        )
        if any(r.period_fetch_complete is False for r in mix):
            story.append(Paragraph("Verdeling gedeeltelijk bij onvolledig opgehaalde accounts.", styles["note"]))
        header = ["Account", "Reels", "Afbeeldingen", "Carrousels", "Overige video’s", "Onbekend"]
        data = [header]
        for row in mix:
            if not row.has_fetched_data:
                data.append([row.display_name, "n.b.", "n.b.", "n.b.", "n.b.", "n.b."])
                continue
            data.append(
                [
                    row.display_name,
                    str(row.reels),
                    str(row.images),
                    str(row.carousels),
                    str(row.other_videos),
                    str(row.unknown),
                ]
            )
        story.append(_table(data))

    if "score_list" in sections:
        # Filter accounts/period first, then top 20 (analytics already ranks within selection).
        ranked, incomplete, missing = top_posts(
            own_account_id=own_id,
            competitor_usernames=competitors,
            period_start=selection.period_start,
            period_end=selection.period_end,
        )
        story.append(Paragraph("Score List", styles["h2"]))
        story.append(
            Paragraph(
                f"De 20 posts met de meeste likes en reacties van "
                f"{_fmt_day(selection.period_start)} t/m {_fmt_day(selection.period_end)}.",
                styles["caption"],
            )
        )
        if incomplete:
            story.append(
                Paragraph(
                    "Ranglijst onvolledig (posts zonder likes én reacties weggelaten).",
                    styles["note"],
                )
            )
        if missing:
            story.append(
                Paragraph("Nog niet in de ranglijst: " + ", ".join(missing) + ".", styles["note"])
            )
        header = ["#", "Account / post"]
        if "likes" in metrics:
            header.append("Likes")
        if "comments" in metrics:
            header.append("Reacties")
        if "interaction" in metrics:
            header.append("Interactie")
        data = [header]
        for index, row in enumerate(ranked, start=1):
            link = ""
            if row.permalink:
                safe = row.permalink.replace("&", "&amp;")
                link = f'<br/><a href="{safe}" color="#E87722"><u>Open post</u></a>'
            account_cell = Paragraph(
                f"<b>{row.account_name}</b><br/>"
                f"{_fmt_dt(row.published_at)[:10]} · {row.content_type}"
                f"<br/><font size='7' color='#5A5F66'>gemeten {_fmt_dt(row.measured_at)}</font>"
                f"{link}",
                styles["body"],
            )
            cells: list[Any] = [str(index), account_cell]
            if "likes" in metrics:
                cells.append(_fmt_int(row.likes))
            if "comments" in metrics:
                cells.append(_fmt_int(row.comments))
            if "interaction" in metrics:
                cells.append(_fmt_int(row.interaction))
            data.append(cells)
        if len(data) == 1:
            story.append(Paragraph("Geen posts in deze selectie.", styles["note"]))
        else:
            story.append(_table(data, col_widths=[12 * mm, 95 * mm] + [None] * (len(header) - 2)))

    if "growth" in sections:
        story.append(Paragraph("Growth", styles["h2"]))
        story.append(
            Paragraph(
                f"Verschil tussen opgeslagen meetmomenten "
                f"(sinds {_fmt_day(selection.tracking_since)}).",
                styles["caption"],
            )
        )
        grow_rows = []
        if selection.growth_mode == "Per dag" and selection.growth_day1 and selection.growth_day2:
            result = build_daily_comparison(
                own_account_id=own_id,
                competitor_usernames=competitors,
                day1=selection.growth_day1,
                day2=selection.growth_day2,
            )
            if result.incomplete:
                story.append(Paragraph("Ontbrekende metingen tonen “Niet gemeten”.", styles["note"]))
            grow_rows = summarize_daily_account_deltas(result)
            story.append(
                Paragraph(
                    f"Meetdagen: {_fmt_day(result.day1)} → {_fmt_day(result.day2)}.",
                    styles["note"],
                )
            )
        elif selection.growth_mode == "Binnen één dag" and selection.growth_intraday_day:
            intraday = build_intraday_comparison(
                own_account_id=own_id,
                competitor_usernames=competitors,
                day=selection.growth_intraday_day,
            )
            grow_rows = intraday.account_rows
            story.append(
                Paragraph(
                    f"Meetdag: {_fmt_day(selection.growth_intraday_day)} (eerste vs laatste meting).",
                    styles["note"],
                )
            )

        header = ["Account"]
        if "posts" in metrics:
            header.append("Geplaatste posts")
        if "followers" in metrics or "growth" in metrics:
            header.append("Δ Volgers")
        if "growth" in metrics:
            header.append("Δ Volgers %")
        if "likes" in metrics:
            header.append("Δ Likes")
        if "comments" in metrics:
            header.append("Δ Reacties")
        data = [header]
        for row in grow_rows:
            cells = [
                Paragraph(
                    f"<b>{row.display_name}</b><br/>"
                    f"<font size='7' color='#5A5F66'>"
                    f"{_fmt_dt(row.start_measured_at)} → {_fmt_dt(row.end_measured_at)}"
                    f"</font>",
                    styles["body"],
                )
            ]
            if "posts" in metrics:
                cells.append(
                    format_published_posts_cell(
                        getattr(row, "published_posts_count", None),
                        getattr(row, "published_posts_coverage", "unknown"),
                    )
                )
            if "followers" in metrics or "growth" in metrics:
                if row.insufficient:
                    cells.append(row.missing_note or NOT_MEASURED)
                else:
                    cells.append(_fmt_delta(row.followers_delta))
            if "growth" in metrics:
                if row.insufficient or row.followers_start in (None, 0) or row.followers_delta is None:
                    cells.append("n.b.")
                else:
                    pct = (row.followers_delta / row.followers_start) * 100.0
                    cells.append(_fmt_pct(pct))
            if "likes" in metrics:
                cells.append("—" if row.comparable_posts == 0 else _fmt_delta(row.likes_delta))
            if "comments" in metrics:
                cells.append("—" if row.comparable_posts == 0 else _fmt_delta(row.comments_delta))
            data.append(cells)
        if len(data) > 1:
            story.append(_table(data))
        else:
            story.append(Paragraph("Geen Growth-gegevens voor deze selectie.", styles["note"]))

        if selection.include_growth_chart:
            series_mode: SeriesMode = (
                "daily" if selection.growth_mode == "Per dag" else "intraday"
            )
            series = build_growth_series(
                own_account_id=own_id,
                competitor_usernames=competitors,
                mode=series_mode,
                day1=selection.growth_day1,
                day2=selection.growth_day2,
                intraday_day=selection.growth_intraday_day,
            )
            png = _render_chart_png(series)
            if png:
                story.append(Spacer(1, 6))
                story.append(Image(io.BytesIO(png), width=170 * mm, height=75 * mm))

    if len(story) <= 3:
        story.append(Paragraph("Geen onderdelen geselecteerd.", styles["note"]))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
