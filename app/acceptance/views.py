"""Server-rendered HTML for the acceptance-run report (CLAUDE.md §14, §15).

Same rules as the Command Center (`app/dashboard/views.py`): plain HTML, no
JavaScript, and every piece of email-sourced text is passed through
:func:`html.escape` here — this is this report's single choke point where
untrusted email text becomes HTML.
"""

from __future__ import annotations

from html import escape

from app.acceptance.models import AcceptanceReport
from app.acceptance.strata import STRATA

_STYLE_LINK = '<link rel="stylesheet" href="/static/dashboard.css">'
_STRATUM_TARGETS: dict[str, int] = {s.name: s.target for s in STRATA}


def _page(title: str, body: str, account: str | None = None) -> str:
    header = '<h1><a href="/dashboard">Command Center</a></h1>'
    account_bar = f'<div class="account">Signed in as <strong>{escape(account)}</strong></div>' if account else ""
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title>{_STYLE_LINK}</head><body>"
        f"<header>{header}{account_bar}</header>"
        f"<main>{body}</main>"
        "<footer>Read-only acceptance run · no Gmail changes are ever made by this report.</footer>"
        "</body></html>"
    )


def _gate_banner(report: AcceptanceReport) -> str:
    if report.passed:
        return (
            '<div class="ok-note">PASSED — 0 protected or important emails were '
            "routed to Review in this sample. Still: read the Review list below "
            "yourself before trusting this number (CLAUDE.md §15) — the count only "
            "catches what the app already recognized as protected.</div>"
        )
    count = len(report.false_reviews)
    return (
        f'<div class="banner"><strong>FAILED — {count} protected email'
        f'{"s" if count != 1 else ""} routed to Review.</strong> Do not enable live '
        "Gmail writes until this is investigated and fixed, and the run passes "
        "cleanly (CLAUDE.md §15).</div>"
    )


def _strata_table_html(strata: dict[str, int]) -> str:
    rows = "".join(
        f"<tr><td>{escape(name)}</td><td>{count}</td>"
        f"<td>{_STRATUM_TARGETS.get(name, '—')}</td></tr>"
        for name, count in strata.items()
    )
    return (
        "<table class=\"strata\"><thead><tr><th>Category</th><th>Pulled</th>"
        "<th>Target</th></tr></thead><tbody>"
        f"{rows}</tbody></table>"
    )


def _summary_html(summary: dict) -> str:
    by_priority = summary.get("by_priority", {})
    items = (
        ("Total classified", summary.get("total", 0)),
        ("P1 Urgent", by_priority.get("P1", 0)),
        ("P2 Important", by_priority.get("P2", 0)),
        ("P3 Normal", by_priority.get("P3", 0)),
        ("Protected", summary.get("protected", 0)),
        ("Routed to Review", summary.get("would_review", 0)),
        ("Action required", summary.get("action_required", 0)),
        ("AI consulted", summary.get("ai_consulted", 0)),
    )
    cells = "".join(f'<div class="stat"><span class="n">{v}</span><span class="l">{escape(k)}</span></div>' for k, v in items)
    return f'<div class="stats">{cells}</div>'


def _false_reviews_html(report: AcceptanceReport) -> str:
    if not report.false_reviews:
        return ""
    rows = "".join(
        "<li>"
        f'<strong>{escape(c.sender_email)}</strong> — {escape(c.subject_safe_ref)}<br>'
        f'protected because: {escape("; ".join(c.protection_reasons) or "unknown")}<br>'
        f'routed to Review because: {escape(c.review_reason or "unknown")}'
        "</li>"
        for c in report.false_reviews
    )
    return f'<h2>Gate failures</h2><ul class="rows">{rows}</ul>'


def _review_row_html(row) -> str:
    attach = ' <span class="attach" title="has attachments">\U0001F4CE</span>' if row.has_attachments else ""
    priority = f'<span class="pri pri-{escape(row.priority)}">{escape(row.priority)}</span>' if row.priority else ""
    name = (row.sender_name or "").strip()
    address = (row.sender_email or "").strip()
    if name and address and name.lower() != address.lower():
        sender = f'{escape(name)} <span class="sender-addr">&lt;{escape(address)}&gt;</span>'
    else:
        sender = escape(name or address or "(unknown sender)")
    reason = f'<div class="reason">Why flagged: {escape(row.reason)}</div>' if row.reason else ""
    snippet = f'<div class="summary">{escape(row.snippet)}</div>' if row.snippet else ""
    conf = f"{row.confidence * 100:.0f}%" if row.confidence is not None else "—"
    open_link = (
        f'<a class="open-gmail" href="{escape(row.gmail_url)}" target="_blank" '
        'rel="noopener">Open in Gmail →</a>'
        if row.gmail_url
        else ""
    )
    return (
        '<article class="row">'
        '<div class="row-head">'
        f'<span class="sender">{sender}</span>'
        f"{priority}</div>"
        f'<div class="subject">{escape(row.subject)}{attach}</div>'
        f"{snippet}"
        f"{reason}"
        f'<div class="meta">confidence {conf} {open_link}</div>'
        "</article>"
    )


def render_report(report: AcceptanceReport, account: str) -> str:
    body = (
        f'<p><a class="back" href="/dashboard">← Command Center</a></p>'
        f"<h2>Acceptance run · {escape(report.run_id)}</h2>"
        f'<p class="muted small">Generated {escape(report.generated_at)} · '
        f"{report.sample_size} of {report.target_size} messages targeted · "
        "read-only, zero Gmail changes.</p>"
        f"{_gate_banner(report)}"
        "<h3>Sample composition</h3>"
        f"{_strata_table_html(report.strata)}"
        "<h3>Summary</h3>"
        f"{_summary_html(report.summary)}"
        f"{_false_reviews_html(report)}"
        f"<h2>Review list ({len(report.review_rows)})</h2>"
        '<p class="muted small">Everything this run set aside for Review. Read '
        "through it yourself — this is the human check CLAUDE.md §15 asks for, "
        "on top of the automated count above.</p>"
        + (
            "".join(_review_row_html(r) for r in report.review_rows)
            if report.review_rows
            else '<p class="empty">Nothing was routed to Review in this sample.</p>'
        )
    )
    return _page(f"Acceptance run {report.run_id} · Command Center", body, account=account)


def render_no_runs(account: str) -> str:
    body = (
        '<p><a class="back" href="/dashboard">← Command Center</a></p>'
        "<h2>No acceptance run yet</h2>"
        "<p>Nothing has been run in this server process. Start one with:</p>"
        "<pre>POST /acceptance/run?target=250</pre>"
        '<p class="muted small">This report only exists in memory for the process '
        "that ran it — restarting the server clears it. The permanent record is "
        "your control workbook's System_Runs and Audit_Log tabs.</p>"
    )
    return _page("Acceptance run · Command Center", body, account=account)


def render_run_not_found(run_id: str, account: str) -> str:
    body = (
        '<p><a class="back" href="/dashboard">← Command Center</a></p>'
        f'<p class="danger">No cached report for run <code>{escape(run_id)}</code>.</p>'
        "<p>Either it was never run in this process, or an older run pushed it out "
        "of the small in-memory cache (the last 5 runs are kept).</p>"
    )
    return _page("Run not found · Command Center", body, account=account)


__all__ = ("render_no_runs", "render_report", "render_run_not_found")
