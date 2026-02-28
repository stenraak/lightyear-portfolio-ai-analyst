"""
Email delivery for portfolio analysis reports.

Uses Gmail SMTP with an App Password — no extra package required (stdlib only).
The email body is an Outlook-safe summary built from structured analysis data.
The full HTML report is attached as report.html AND hosted at a public URL.

Email rendering across clients:
  Gmail / Apple Mail / Outlook web  — full summary renders correctly
  Outlook for Windows                — summary renders (table-based, inline styles)
  All clients                        — can open the attached report.html in a browser
                                       or follow the "View Full Report" link

Setup (one-time):
  1. Enable 2-Step Verification on your Google account
  2. myaccount.google.com → Security → App Passwords → generate for "Mail"
  3. Copy the 16-character code
  4. Add to .env:
       GMAIL_ADDRESS=your.address@gmail.com
       GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
       REPORT_EMAIL_TO=recipient@example.com
"""

import os
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

from src.analysis.analyst import PortfolioAnalysis


# ---------------------------------------------------------------------------
# Colour palette — light theme, works in all email clients
# ---------------------------------------------------------------------------

_C = {
    "bg":           "#f1f5f9",
    "card":         "#ffffff",
    "border":       "#e2e8f0",
    "header":       "#6366f1",   # indigo — matches the full report brand
    "header_sub":   "#c7d2fe",
    "text":         "#0f172a",
    "muted":        "#64748b",
    "separator":    "#e2e8f0",
    # Action colours (accessible on white background)
    "buy_bg":       "#dcfce7",
    "buy_fg":       "#166534",
    "hold_bg":      "#fef3c7",
    "hold_fg":      "#92400e",
    "sell_bg":      "#fee2e2",
    "sell_fg":      "#991b1b",
    # Trend
    "positive":     "#166534",
    "neutral":      "#92400e",
    "negative":     "#991b1b",
}


# ---------------------------------------------------------------------------
# Low-level HTML primitives
# ---------------------------------------------------------------------------

def _th(text: str, align: str = "left") -> str:
    style = (
        f"padding:8px 14px;font-size:11px;font-weight:600;text-transform:uppercase;"
        f"letter-spacing:0.05em;color:{_C['muted']};border-bottom:2px solid {_C['border']};"
        f"text-align:{align};"
    )
    return f"<th style='{style}'>{text}</th>"


def _td(text: str, extra: str = "") -> str:
    style = (
        f"padding:10px 14px;font-size:13px;color:{_C['text']};"
        f"border-bottom:1px solid {_C['border']};{extra}"
    )
    return f"<td style='{style}'>{text}</td>"


def _action_badge(action: str) -> str:
    bg, fg = {
        "buy":  (_C["buy_bg"],  _C["buy_fg"]),
        "sell": (_C["sell_bg"], _C["sell_fg"]),
    }.get(action.lower(), (_C["hold_bg"], _C["hold_fg"]))
    return (
        f"<span style='display:inline-block;padding:2px 8px;border-radius:4px;"
        f"background:{bg};color:{fg};font-weight:600;font-size:12px;'>"
        f"{action.upper()}</span>"
    )


def _card(title: str, body: str) -> str:
    """Wrap a body block in a titled card. Table-safe, Outlook-compatible."""
    return f"""
<table width="100%" cellpadding="0" cellspacing="0"
       style="background:{_C['card']};border:1px solid {_C['border']};
              border-collapse:collapse;margin-bottom:16px;">
  <tr>
    <td style="padding:10px 16px;background:#f8fafc;
               border-bottom:1px solid {_C['border']};">
      <span style="font-size:11px;font-weight:600;text-transform:uppercase;
                   letter-spacing:0.05em;color:{_C['muted']};">{title}</span>
    </td>
  </tr>
  <tr><td style="padding:16px;">{body}</td></tr>
</table>"""


# ---------------------------------------------------------------------------
# Section builders — one function per card
# ---------------------------------------------------------------------------

def _positions_card(analysis: PortfolioAnalysis) -> str:
    rows = ""
    for pos in analysis.positions:
        conviction_color = {
            "high":   _C["positive"],
            "medium": _C["neutral"],
            "low":    _C["muted"],
        }.get(pos.conviction, _C["muted"])

        rows += (
            "<tr>"
            + _td(f"<strong>{pos.symbol}</strong>")
            + _td(_action_badge(pos.recommendation))
            + _td(pos.conviction.capitalize(), f"color:{conviction_color};")
            + _td(pos.valuation_assessment.replace("_", " ").capitalize(),
                  f"color:{_C['muted']};")
            + "</tr>"
        )

    table = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
  <thead>
    <tr>{_th("Symbol")}{_th("Action")}{_th("Conviction")}{_th("Valuation")}</tr>
  </thead>
  <tbody>{rows}</tbody>
</table>"""
    return _card("Positions", table)


def _stats_card(analysis: PortfolioAnalysis) -> str:
    summary = analysis.portfolio_summary or {}
    trend = summary.get("fundamental_trend", "")
    trend_color = {
        "improving":    _C["positive"],
        "deteriorating": _C["negative"],
    }.get(trend.lower(), _C["neutral"])

    beta_str = (
        f"{analysis.portfolio_beta:.2f}"
        if analysis.portfolio_beta is not None else "N/A"
    )

    stats = [
        ("Total Value",     f"€{analysis.total_value_eur:,.2f}", _C["header"]),
        ("Portfolio Beta",  beta_str,                             _C["text"]),
        ("Trend",           trend.capitalize() or "N/A",          trend_color),
    ]

    cells = ""
    for i, (label, value, color) in enumerate(stats):
        border = "" if i == len(stats) - 1 else f"border-right:1px solid {_C['border']};"
        cells += f"""
    <td width="33%" style="padding:16px;text-align:center;{border}">
      <div style="font-size:11px;color:{_C['muted']};text-transform:uppercase;
                  letter-spacing:0.05em;margin-bottom:6px;">{label}</div>
      <div style="font-size:20px;font-weight:700;color:{color};">{value}</div>
    </td>"""

    body = (
        f"<table width='100%' cellpadding='0' cellspacing='0'"
        f" style='border-collapse:collapse;'><tr>{cells}</tr></table>"
    )
    return _card("Portfolio Stats", body)


def _opportunity_risk_card(analysis: PortfolioAnalysis) -> str:
    summary = analysis.portfolio_summary or {}
    opp  = summary.get("top_opportunity", {})
    risk = summary.get("top_risk", {})

    def _box(icon: str, label: str, bg: str, border: str, fg: str, data: dict) -> str:
        return f"""
<td width="50%" style="padding:0 8px 0 0;vertical-align:top;">
  <table width="100%" cellpadding="0" cellspacing="0"
         style="background:{bg};border:1px solid {border};border-collapse:collapse;">
    <tr><td style="padding:12px;">
      <div style="font-size:11px;font-weight:600;color:{fg};text-transform:uppercase;
                  letter-spacing:0.05em;margin-bottom:6px;">{icon} {label}</div>
      <div style="font-size:14px;font-weight:700;color:{_C['text']};
                  margin-bottom:4px;">{data.get("symbol", "—")}</div>
      <div style="font-size:12px;color:{_C['muted']};line-height:1.5;">
        {data.get("reason", "")}
      </div>
    </td></tr>
  </table>
</td>"""

    opp_box  = _box("▲", "Top Opportunity",
                    _C["buy_bg"],  "#bbf7d0", _C["buy_fg"],  opp)
    risk_box = _box("▼", "Top Risk",
                    _C["sell_bg"], "#fecdd3", _C["sell_fg"], risk)

    # Pair them in a two-column row; wrap right column so padding is symmetric
    right_box = risk_box.replace("padding:0 8px 0 0", "padding:0 0 0 8px")
    body = (
        f"<table width='100%' cellpadding='0' cellspacing='0'"
        f" style='border-collapse:collapse;'>"
        f"<tr>{opp_box}{right_box}</tr></table>"
    )
    return _card("Opportunities &amp; Risks", body)


def _action_card(analysis: PortfolioAnalysis) -> str:
    summary = analysis.portfolio_summary or {}
    portfolio_action   = summary.get("portfolio_action",   "")
    rebalance_suggestion = summary.get("rebalance_suggestion", "")

    body = f"""
<p style="font-size:13px;color:{_C['text']};line-height:1.6;margin:0 0 10px;">
  {portfolio_action}
</p>
<p style="font-size:12px;color:{_C['muted']};line-height:1.6;margin:0;
          border-left:3px solid {_C['header']};padding-left:10px;">
  {rebalance_suggestion}
</p>"""
    return _card("Portfolio Action", body)


def _cta_button(url: str) -> str:
    return f"""
<table width="100%" cellpadding="0" cellspacing="0">
  <tr>
    <td style="padding:8px 0 24px;text-align:center;">
      <a href="{url}"
         style="display:inline-block;padding:12px 32px;background:{_C['header']};
                color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;">
        View Full Report &rarr;
      </a>
      <div style="font-size:11px;color:{_C['muted']};margin-top:8px;">
        Opens in your browser
      </div>
    </td>
  </tr>
</table>"""


# ---------------------------------------------------------------------------
# Top-level HTML assembler
# ---------------------------------------------------------------------------

def build_email_html(
    analysis: PortfolioAnalysis,
    report_date: str,
    report_url: Optional[str] = None,
) -> str:
    """
    Assemble the full Outlook-safe summary email.

    Pure function — takes structured data, returns an HTML string.
    Modify or reorder the section builders above to change the layout.
    """
    buy_count  = sum(1 for p in analysis.positions if p.recommendation == "buy")
    hold_count = sum(1 for p in analysis.positions if p.recommendation == "hold")
    sell_count = sum(1 for p in analysis.positions if p.recommendation == "sell")

    sections = (
        _positions_card(analysis)
        + _stats_card(analysis)
        + _opportunity_risk_card(analysis)
        + _action_card(analysis)
    )

    view_link = (
        f"&nbsp;&middot;&nbsp;"
        f"<a href='{report_url}' style='color:{_C['header']};text-decoration:none;'>"
        f"View in browser</a>"
        if report_url else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>Portfolio Analysis &mdash; {report_date}</title>
</head>
<body style="margin:0;padding:0;background:{_C['bg']};
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">

  <!-- Outer wrapper -->
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{_C['bg']};">
  <tr><td style="padding:24px 16px;">

    <!-- 600px centred container -->
    <table width="600" cellpadding="0" cellspacing="0" align="center"
           style="max-width:600px;width:100%;border-collapse:collapse;">

      <!-- Header -->
      <tr>
        <td style="background:{_C['header']};padding:24px 28px;">
          <div style="font-size:20px;font-weight:700;color:#ffffff;">
            Portfolio Analysis
          </div>
          <div style="font-size:13px;color:{_C['header_sub']};margin-top:4px;">
            {report_date}
            &nbsp;&middot;&nbsp;
            {buy_count} Buy &nbsp;&middot;&nbsp;
            {hold_count} Hold &nbsp;&middot;&nbsp;
            {sell_count} Sell
          </div>
        </td>
      </tr>

      <!-- Body -->
      <tr>
        <td style="background:{_C['bg']};padding:20px 0 4px;">
          {sections}
        </td>
      </tr>

      <!-- CTA (only if a URL is available) -->
      {"<tr><td>" + _cta_button(report_url) + "</td></tr>" if report_url else ""}

      <!-- Footer -->
      <tr>
        <td style="background:{_C['card']};border-top:1px solid {_C['border']};
                   padding:14px 28px;">
          <p style="font-size:11px;color:{_C['muted']};margin:0;text-align:center;">
            Lightyear Portfolio AI Analyst{view_link}
          </p>
        </td>
      </tr>

    </table>
  </td></tr>
  </table>

</body>
</html>"""


# ---------------------------------------------------------------------------
# SMTP delivery
# ---------------------------------------------------------------------------

def send_report_email(
    analysis: PortfolioAnalysis,
    report_date: str,
    report_url: Optional[str] = None,
    report_path: Optional[Path] = None,
) -> None:
    """
    Send the portfolio summary email via Gmail SMTP.

    Args:
        analysis:     Completed PortfolioAnalysis — drives the summary content.
        report_date:  Statement date string, used in the subject line.
        report_url:   Public URL to the hosted full report (optional).
        report_path:  Path to the local .html file — attached if provided (optional).
    """
    from_email   = os.environ["GMAIL_ADDRESS"]
    app_password = os.environ["GMAIL_APP_PASSWORD"]
    to_email     = os.environ["REPORT_EMAIL_TO"]

    email_html = build_email_html(analysis, report_date, report_url)

    plain_text = (
        f"Portfolio Analysis for {report_date}\n"
        f"Total value: €{analysis.total_value_eur:,.2f}\n"
        + (f"Full report: {report_url}\n" if report_url else "")
        + "\nPositions:\n"
        + "\n".join(
            f"  {p.symbol}: {p.recommendation.upper()} ({p.conviction} conviction)"
            for p in analysis.positions
        )
    )

    # Outer: mixed (allows both alternative body + attachment)
    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"Portfolio Analysis \u2014 {report_date}"
    msg["From"]    = from_email
    msg["To"]      = to_email

    # Inner: alternative (plain text fallback + HTML summary)
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText(plain_text, "plain"))
    alternative.attach(MIMEText(email_html, "html"))
    msg.attach(alternative)

    # Attach the full HTML report for offline access / Outlook users
    if report_path and report_path.exists():
        with open(report_path, "rb") as f:
            attachment = MIMEApplication(f.read(), _subtype="html")
            attachment.add_header(
                "Content-Disposition",
                "attachment",
                filename=report_path.name,
            )
            msg.attach(attachment)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_email, app_password)
        server.sendmail(from_email, to_email, msg.as_string())

    print(f"Report emailed to {to_email}")
