"""
Phase 7B: Project Health PDF report generation.

Builds a downloadable PDF summarizing one project's ALREADY-PERSISTED
analysis (metrics, health score, risk, and — when available — scan
history) using ReportLab. This module performs ZERO computation of its
own:

  - No source code is rescanned.
  - No metric, health score, or risk score is recalculated — every
    number printed here comes straight from a `Project`/`Module` row,
    the same values the JSON APIs in app/api/projects.py already expose.
  - The AI provider is never called during PDF generation (see the "AI
    Recommendations" section below for why that section is always
    "unavailable" in the current architecture, by design).

The one piece of *display* logic reused (not invented) here is
`health_status_for_score()` from app.analyzer.scoring, applied to each
individual category score so the report can show a Status label per
category — the exact same 5-band thresholds used for the overall Health
Score, not a second scoring system.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.analyzer.scoring import health_status_for_score

CATEGORY_LABELS = {
    "maintainability": "Maintainability",
    "complexity": "Complexity",
    "code_quality": "Code Quality",
    "testing": "Testing",
    "coupling_cohesion": "Coupling / Cohesion",
    "duplication": "Duplication",
    "documentation": "Documentation",
    "code_churn": "Code Churn",
}
CATEGORY_ORDER = list(CATEGORY_LABELS.keys())

_STATUS_COLORS = {
    "CRITICAL": colors.HexColor("#dc2626"),
    "POOR": colors.HexColor("#ea580c"),
    "NEEDS_ATTENTION": colors.HexColor("#d97706"),
    "HEALTHY": colors.HexColor("#16a34a"),
    "EXCELLENT": colors.HexColor("#059669"),
}
_RISK_COLORS = {
    "LOW": colors.HexColor("#16a34a"),
    "MEDIUM": colors.HexColor("#d97706"),
    "HIGH": colors.HexColor("#ea580c"),
    "CRITICAL": colors.HexColor("#dc2626"),
}
_ACCENT = colors.HexColor("#4f46e5")
_TEXT = colors.HexColor("#111827")
_MUTED = colors.HexColor("#6b7280")
_BORDER = colors.HexColor("#e5e7eb")


def _na(value, suffix: str = "") -> str:
    """The one formatting rule this whole module exists to get right:
    None -> "N/A", any real value (including a genuine 0) -> printed as-is."""
    if value is None:
        return "N/A"
    return f"{value}{suffix}"


def _status_label(status: str | None) -> str:
    if not status:
        return "N/A"
    return status.replace("_", " ").title()


def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="ReportTitle", parent=styles["Title"], fontSize=20,
        textColor=_TEXT, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="ReportSubtitle", parent=styles["Normal"], fontSize=11,
        textColor=_ACCENT, spaceAfter=14,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", parent=styles["Heading2"], fontSize=14,
        textColor=_TEXT, spaceBefore=18, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="MetaLine", parent=styles["Normal"], fontSize=9.5,
        textColor=_MUTED, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="BigScore", parent=styles["Normal"], fontSize=32,
        textColor=_TEXT, spaceAfter=0,
    ))
    styles.add(ParagraphStyle(
        name="BodyText9", parent=styles["Normal"], fontSize=9, textColor=_TEXT,
    ))
    return styles


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(_MUTED)
    canvas.drawString(0.75 * inch, 0.5 * inch, "AI Software Metrics Advisor — Software Project Health Report")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _table_style(header_bg=_ACCENT) -> TableStyle:
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, _BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
    ])


def generate_project_report(
    project_info: dict,
    modules_info: list[dict],
    history_rows: list[dict],
    recommendations_info: dict | None,
) -> bytes:
    """
    Build the PDF and return its raw bytes. All inputs are plain
    dicts/lists already assembled from persisted data by the caller
    (app/api/upload.py's report endpoint) — this function only lays
    them out, it does not fetch, compute, or validate anything.

      project_info: {id, name, upload_date, health_score, health_status,
                      category_scores, summary: {files, classes,
                      functions, total_loc}}
      modules_info: list of merged raw-metric + risk dicts, one per
                    module (same shape app/api/projects.py's
                    `_module_metrics_dict`/`_module_risk_dict` produce)
      history_rows: list of {scan_id, scanned_at, health_score,
                    health_status, category_scores}, chronological,
                    or [] if the project has no history
      recommendations_info: {available, recommendations, message} in the
                    same shape as RecommendationsResponse. Recommendations
                    may come from the configured AI provider or the
                    deterministic persisted-metric fallback.
    """
    styles = _build_styles()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )
    story = []

    # --- A. Header ----------------------------------------------------
    story.append(Paragraph("Software Project Health Report", styles["ReportTitle"]))
    story.append(Paragraph(project_info.get("name") or "Untitled Project", styles["ReportSubtitle"]))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    upload_date = project_info.get("upload_date")
    upload_date_str = upload_date.strftime("%Y-%m-%d %H:%M UTC") if hasattr(upload_date, "strftime") else _na(upload_date)

    story.append(Paragraph(f"Project ID: {project_info.get('id')}", styles["MetaLine"]))
    story.append(Paragraph(f"Scan date: {upload_date_str}", styles["MetaLine"]))
    story.append(Paragraph(f"Report generated: {generated_at}", styles["MetaLine"]))
    story.append(Spacer(1, 10))

    # --- B. Executive Summary ------------------------------------------
    story.append(Paragraph("Executive Summary", styles["SectionHeading"]))
    summary = project_info.get("summary") or {}
    summary_table = Table(
        [
            ["Health Score", "Health Status", "Files", "Classes", "Functions", "Total LOC"],
            [
                _na(project_info.get("health_score")),
                _status_label(project_info.get("health_status")),
                _na(summary.get("files")),
                _na(summary.get("classes")),
                _na(summary.get("functions")),
                _na(summary.get("total_loc")),
            ],
        ],
        colWidths=[1.0 * inch] * 6,
    )
    summary_table.setStyle(_table_style())
    story.append(summary_table)
    story.append(Spacer(1, 4))

    # --- C. Health Score -------------------------------------------------
    story.append(Paragraph("Health Score", styles["SectionHeading"]))
    score = project_info.get("health_score")
    status = project_info.get("health_status")
    score_color = _STATUS_COLORS.get(status, _TEXT)
    score_style = ParagraphStyle(
        name="ScoreValue", parent=styles["BigScore"], textColor=score_color,
    )
    story.append(Paragraph(f"{_na(score)} / 100 — {_status_label(status)}", score_style))
    story.append(Spacer(1, 10))

    # --- 4. Category Score Table ------------------------------------------
    story.append(Paragraph("Category Scores", styles["SectionHeading"]))
    category_scores = project_info.get("category_scores") or {}
    cat_rows = [["Category", "Score", "Status"]]
    for key in CATEGORY_ORDER:
        value = category_scores.get(key)
        cat_rows.append([
            CATEGORY_LABELS[key],
            _na(value),
            health_status_for_score(value).replace("_", " ").title() if value is not None else "N/A",
        ])
    cat_table = Table(cat_rows, colWidths=[2.2 * inch, 1.2 * inch, 2.2 * inch])
    cat_table.setStyle(_table_style())
    story.append(cat_table)
    story.append(Spacer(1, 4))

    # --- 6. Risk Summary ---------------------------------------------------
    story.append(Paragraph("Risk Summary", styles["SectionHeading"]))
    risk_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    scored_modules = [m for m in modules_info if m.get("risk_score") is not None]
    for m in scored_modules:
        level = m.get("risk_level")
        if level in risk_counts:
            risk_counts[level] += 1

    risk_summary_table = Table(
        [
            ["Total Scored", "Low", "Medium", "High", "Critical"],
            [
                str(len(scored_modules)),
                str(risk_counts["LOW"]), str(risk_counts["MEDIUM"]),
                str(risk_counts["HIGH"]), str(risk_counts["CRITICAL"]),
            ],
        ],
        colWidths=[1.4 * inch] * 5,
    )
    risk_summary_table.setStyle(_table_style())
    story.append(risk_summary_table)
    story.append(Spacer(1, 8))

    ranked = sorted(scored_modules, key=lambda m: m["risk_score"], reverse=True)
    if ranked:
        risk_rows = [["Module", "Risk Score", "Risk Level", "Risk Factors"]]
        for m in ranked:
            factors = ", ".join(m.get("risk_factors") or []) or "None identified"
            risk_rows.append([
                Paragraph(m.get("file_name", ""), styles["BodyText9"]),
                _na(m.get("risk_score")),
                m.get("risk_level") or "N/A",
                Paragraph(factors, styles["BodyText9"]),
            ])
        risk_table = Table(risk_rows, colWidths=[1.6 * inch, 0.9 * inch, 0.9 * inch, 2.6 * inch], repeatRows=1)
        risk_table.setStyle(_table_style())
        story.append(risk_table)
    else:
        story.append(Paragraph("No risk-scored modules for this project.", styles["Normal"]))
    story.append(Spacer(1, 4))

    # --- 7. Module Details ---------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Module Details", styles["SectionHeading"]))
    if modules_info:
        module_rows = [[
            "Module", "LOC", "Classes", "Fn", "Complexity", "Maint.",
            "Coupling", "Cohesion", "DIT", "Dup %", "Doc %", "Risk",
        ]]
        for m in modules_info:
            loc = m.get("loc") or {}
            complexity = m.get("complexity") or {}
            module_rows.append([
                Paragraph(m.get("file_name", ""), styles["BodyText9"]),
                _na(loc.get("total")),
                _na(m.get("classes")),
                _na(m.get("functions")),
                _na(complexity.get("total")),
                _na(m.get("maintainability_index")),
                _na(m.get("coupling")),
                _na(m.get("cohesion")),
                _na(m.get("depth_of_inheritance")),
                _na(m.get("duplication_percentage")),
                _na(m.get("documentation_percentage")),
                m.get("risk_level") or "N/A",
            ])
        module_table = Table(
            module_rows,
            colWidths=[1.15 * inch, 0.42 * inch, 0.42 * inch, 0.32 * inch, 0.55 * inch,
                       0.45 * inch, 0.5 * inch, 0.48 * inch, 0.32 * inch, 0.4 * inch,
                       0.4 * inch, 0.5 * inch],
            repeatRows=1,
        )
        module_table.setStyle(_table_style())
        story.append(module_table)
    else:
        story.append(Paragraph("No module information available for this project.", styles["Normal"]))

    # --- 8. AI Recommendations -----------------------------------------
    story.append(Spacer(1, 10))
    story.append(Paragraph("AI Recommendations", styles["SectionHeading"]))
    recommendations = (recommendations_info or {}).get("recommendations") or []
    if recommendations:
        for rec in recommendations:
            evidence = rec.get("evidence") or {}
            evidence_parts = []
            evidence_labels = {
                "complexity": "Complexity",
                "maintainability_index": "Maintainability",
                "coupling": "Coupling",
                "cohesion": "Cohesion",
                "duplication_percentage": "Duplication",
                "documentation_percentage": "Documentation",
                "risk_score": "Risk score",
            }
            for key, label in evidence_labels.items():
                if key in evidence and evidence[key] is not None:
                    value = evidence[key]
                    suffix = "%" if key.endswith("percentage") else ""
                    evidence_parts.append(f"{label}: {value}{suffix}")

            block = [
                Paragraph(
                    f"<b>{rec.get('severity', 'N/A')}</b> — {rec.get('problem', '')}"
                    f" (Priority {rec.get('priority', 'N/A')})",
                    styles["Normal"],
                ),
                Paragraph(f"<b>File:</b> {rec.get('file_path') or 'N/A'}", styles["BodyText9"]),
                Paragraph(
                    f"<b>Official risk:</b> {rec.get('official_risk') or 'N/A'} "
                    f"&nbsp;&nbsp; <b>AI concern:</b> {rec.get('severity') or 'N/A'}",
                    styles["BodyText9"],
                ),
            ]
            if evidence_parts:
                block.append(Paragraph(
                    "<b>Measured evidence:</b> " + "; ".join(evidence_parts),
                    styles["BodyText9"],
                ))
            if rec.get("disagreement"):
                block.append(Paragraph(
                    f"<b>Risk disagreement:</b> {rec.get('disagreement')}",
                    styles["BodyText9"],
                ))
            block.extend([
                Paragraph(f"<b>Explanation:</b> {rec.get('explanation', '')}", styles["BodyText9"]),
                Paragraph(f"<b>Recommendation:</b> {rec.get('recommendation', '')}", styles["BodyText9"]),
                Paragraph(f"<b>Provenance:</b> {rec.get('provenance') or 'AI advisory'}", styles["BodyText9"]),
                Spacer(1, 6),
            ])
            story.append(KeepTogether(block))
    else:
        story.append(Paragraph(
            (recommendations_info or {}).get("message")
            or "No evidence-backed recommendations were generated for this scan.",
            styles["Normal"],
        ))
    story.append(Spacer(1, 4))

    # --- 9. Historical Trend Summary --------------------------------------
    story.append(Paragraph("Historical Trend Summary", styles["SectionHeading"]))
    if not history_rows or len(history_rows) < 2:
        story.append(Paragraph(
            "Historical trend unavailable — only one scan is available.",
            styles["Normal"],
        ))
    else:
        previous_score = history_rows[-2]["health_score"]
        current_score = history_rows[-1]["health_score"]
        if previous_score is not None and current_score is not None:
            change = round(current_score - previous_score, 2)
            change_str = f"{'+' if change >= 0 else ''}{change}"
        else:
            change_str = "N/A"

        history_table = Table(
            [
                ["Scans in History", "Previous Health Score", "Current Health Score", "Change"],
                [
                    str(len(history_rows)),
                    _na(previous_score),
                    _na(current_score),
                    change_str,
                ],
            ],
            colWidths=[1.5 * inch] * 4,
        )
        history_table.setStyle(_table_style())
        story.append(history_table)

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
