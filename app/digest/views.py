"""Server-rendered HTML for the daily digest (CLAUDE.md §3, §13, §14).

Same plain-HTML, no-JS approach as the rest of the Command Center (Phase 8) —
see ``app/dashboard/views.py``'s own docstring for why. This module is a
second, independent escaping choke point for the same reason that one is:
every sender/subject/summary shown here originates in untrusted email, so
every such string passes through :func:`html.escape` before it reaches the
page.

The digest is read-only — there are no action buttons here. Acting on a
message (Keep, Restore, Trash, ...) still happens on the Command Center's own
Review list; this page only summarizes.
"""

from __future__ import annotations

from html import escape

from app.digest.models import DigestReport, DigestSection

_STYLE_LINK = '<link rel="stylesheet" href="/static/dashboard.css">'


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    text = iso.replace("T", " ")
    for cut in ("+", "."):
        if cut in text:
            text = text.split(cut, 1)[0]
    return text[:16]


def _fmt_conf(conf: float | None) -> str:
    return f"{conf * 100:.0f}%" if conf is not None else "—"


def _page(title: str, body: str, account: str | None = None) -> str:
    header = '<h1><a href="/dashboard">Command Center</a></h1>'
    account_bar = (
        f'<div class="account">Signed in as <strong>{escape(account)}</strong></div>'
        if account
        else ""
    )
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title>{_STYLE_LINK}</head><body>"
        f"<header>{header}{account_bar}</header>"
        f"<main>{body}</main>"
        "<footer>The digest is read-only — open the Command Center to act "
        "on anything in it.</footer>"
        "</body></html>"
    )


def _sender_html(row) -> str:
    name = (row.sender_name or "").strip()
    address = (row.sender_email or "").strip()
    if name and address and name.lower() != address.lower():
        return f'{escape(name)} <span class="sender-addr">&lt;{escape(address)}&gt;</span>'
    return escape(name or address or "(unknown sender)")


def _row_html(row) -> str:
    attach = (
        ' <span class="attach" title="has attachments">\U0001f4ce</span>'
        if row.has_attachments
        else ""
    )
    priority = (
        f'<span class="pri pri-{escape(row.priority)}">{escape(row.priority)}</span>'
        if row.priority
        else ""
    )
    note = f'<div class="note">{escape(row.note)}</div>' if row.note else ""
    reason = (
        f'<div class="reason">Why flagged: {escape(row.reason)}</div>' if row.reason else ""
    )
    snippet = f'<div class="summary">{escape(row.snippet)}</div>' if row.snippet else ""
    open_link = (
        f'<a class="open-gmail" href="{escape(row.gmail_url)}" target="_blank" '
        'rel="noopener">Open in Gmail →</a>'
        if row.gmail_url
        else ""
    )
    return (
        '<article class="row">'
        '<div class="row-head">'
        f'<span class="sender">{_sender_html(row)}</span>'
        f'<span class="when">{escape(_fmt_dt(row.received))}</span>'
        f"{priority}</div>"
        f'<div class="subject">{escape(row.subject)}{attach}</div>'
        f"{snippet}{reason}{note}"
        f'<div class="meta">confidence {escape(_fmt_conf(row.confidence))} {open_link}</div>'
        "</article>"
    )


def _section_html(section: DigestSection) -> str:
    if section.rows:
        rows_html = "".join(_row_html(row) for row in section.rows)
    else:
        rows_html = '<p class="empty">Nothing here today. That\'s a good thing.</p>'
    return (
        '<section class="digest-section">'
        f'<h2>{escape(section.title)} <span class="count-inline">{section.count}</span></h2>'
        f'<p class="muted">{escape(section.blurb)}</p>'
        f'<div class="rows">{rows_html}</div>'
        "</section>"
    )


def render_digest(report: DigestReport, account: str) -> str:
    date_str = report.digest_date.strftime("%A, %B %d, %Y")
    generated_str = report.generated_at.strftime("%Y-%m-%d %H:%M")
    banner = (
        '<div class="banner"><strong>DRY RUN — NO GMAIL CHANGES ARE BEING '
        "MADE.</strong> This digest only shows what the rules propose.</div>"
        if report.dry_run
        else ""
    )
    sections_html = "".join(_section_html(section) for section in report.sections)
    item_word = "item" if report.total == 1 else "items"
    body = (
        f"{banner}"
        f"<h1 class=\"digest-date\">Daily Digest — {escape(date_str)}</h1>"
        f'<p class="muted small">Timezone {escape(report.timezone)} · '
        f'generated {escape(generated_str)} · {report.total} {item_word} across '
        f'{len(report.sections)} sections.</p>'
        f"{sections_html}"
    )
    return _page(f"Daily Digest · {date_str}", body, account=account)


def render_not_connected(account: str | None = None) -> str:
    body = (
        '<div class="banner">Gmail isn\'t connected yet, so there\'s no digest to '
        "build.</div>"
        '<p><a class="btn" href="/oauth/start">Connect Gmail</a> '
        '<a class="btn secondary" href="/dashboard">Command Center</a></p>'
    )
    return _page("Daily Digest", body, account=account)


__all__ = ("render_digest", "render_not_connected")
