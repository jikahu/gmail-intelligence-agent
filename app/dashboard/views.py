"""Server-rendered HTML for the Command Center (CLAUDE.md §3, §13).

Plain HTML + a little CSS. No React, no client framework — the "simple and
understandable" side of §21. Collapsible explainers use the browser's native
``<details>`` element, so the page needs no JavaScript to be fully usable.

**Security note (§16):** senders, subjects and summaries all originate in
untrusted email. Every one of those strings is passed through
:func:`html.escape` before it reaches the page, so an email whose subject is
``<script>…`` is shown as text, never run. This module is the single choke
point where email text becomes HTML, which is exactly why the escaping lives
here and nowhere else.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlencode

from app.dashboard.service import CommandCenter, Row

_STYLE_LINK = '<link rel="stylesheet" href="/static/dashboard.css">'

#: The Review-queue action buttons (§13). ``action`` is the
#: ``/dashboard/action/<action>`` path segment. All seven are now live
#: (Phase 11): five write only to the control workbook; Restore to Inbox and
#: Trash call real Gmail, gated behind ``check_write_gate`` — Trash also
#: routes through a separate confirmation page first (CLAUDE.md §5) rather
#: than posting directly, which is why it's handled specially in
#: :func:`_row_actions_html` below instead of appearing as a plain form.
_ROW_ACTIONS: tuple[tuple[str, str, str | None], ...] = (
    ("Keep", "Leave it where it is and note that you're fine with this.", "keep"),
    (
        "Restore to Inbox",
        "Move it back into your Inbox — a real Gmail change once live writes "
        "are turned on.",
        "restore",
    ),
    ("Review Correct", "Confirm the app was right to set this aside.", "review-correct"),
    (
        "Make Sender Rule",
        "Suggest always keeping this exact sender out of Review — "
        "you approve it in the control workbook.",
        "make-sender-rule",
    ),
    (
        "Make Domain Rule",
        "Suggest always keeping this whole domain out of Review — "
        "you approve it in the control workbook.",
        "make-domain-rule",
    ),
    ("Suggest VIP", "Propose this sender as a VIP (you approve it later).", "suggest-vip"),
    (
        "Trash",
        "Move to Gmail Trash — always asks you to confirm first, and is "
        "always recoverable from Trash for 30 days, never a permanent delete.",
        "trash",
    ),
)


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "—"
    text = iso.replace("T", " ")
    # Drop timezone offset / seconds for a calmer display.
    for cut in ("+", "."):
        if cut in text:
            text = text.split(cut, 1)[0]
    return text[:16]


def _fmt_conf(conf: float | None) -> str:
    return f"{conf * 100:.0f}%" if conf is not None else "—"


def _page(title: str, body: str, account: str | None = None) -> str:
    header = f"<h1><a href=\"/dashboard\">Command Center</a></h1>"
    account_bar = ""
    if account:
        account_bar = (
            f'<div class="account">Signed in as <strong>{escape(account)}</strong>'
            ' · <form method="post" action="/dashboard/logout" class="inline">'
            '<button class="linkbtn" type="submit">Sign out</button></form></div>'
        )
    return (
        "<!doctype html><html lang=\"en\"><head>"
        f"<meta charset=\"utf-8\"><meta name=\"viewport\" "
        "content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title>{_STYLE_LINK}</head><body>"
        f"<header>{header}{account_bar}</header>"
        f"<main>{body}</main>"
        "<footer>Every screen here is read-only except Restore to Inbox and "
        "Trash on the Review list, and only once live writes are turned on.</footer>"
        "</body></html>"
    )


def _banner(center: CommandCenter) -> str:
    if center.dry_run:
        return (
            '<div class="banner"><strong>DRY RUN — NO GMAIL CHANGES ARE BEING MADE.</strong> '
            "This screen only shows what the rules propose."
            "</div>"
        )
    return (
        '<div class="banner live"><strong>DRY_RUN is off.</strong> '
        "Restore to Inbox and Trash on the Review list can change real Gmail "
        "once GMAIL_PROCESSING_ENABLED is also on and the acceptance run has "
        "passed. Every other screen here stays read-only."
        "</div>"
    )


# --------------------------------------------------------------------
# Sign-in pages
# --------------------------------------------------------------------


def render_login(message: str | None = None) -> str:
    note = f'<p class="danger">{escape(message)}</p>' if message else ""
    body = (
        "<p>The Command Center shows your email, so it's locked to your Google "
        "account. Sign in to continue.</p>"
        f"{note}"
        '<p><a class="btn" href="/dashboard/auth/start">Sign in with Google</a></p>'
        '<p class="muted small">"Sign in with Google" is Google\'s own login. It '
        "tells the app which account you are — it does not give the app any new "
        "access to your mail.</p>"
    )
    return _page("Sign in · Command Center", body)


def render_unauthorized(email: str) -> str:
    body = (
        f'<p class="danger">The account <strong>{escape(email)}</strong> isn\'t '
        "authorized to view this Command Center.</p>"
        "<p>V1 allows the single Google account that connected Gmail. If this is "
        "your account, connect Gmail first; otherwise sign in with the right one.</p>"
        '<p><a class="btn" href="/dashboard/login">Back to sign in</a></p>'
    )
    return _page("Not authorized · Command Center", body)


def render_not_connected(account: str | None = None) -> str:
    body = (
        '<div class="banner">Gmail isn\'t connected yet, so there\'s nothing to '
        "show.</div>"
        "<p>The Command Center reads your recent mail to build these cards. "
        "Connect your Gmail account (read-only) to get started.</p>"
        '<p><a class="btn" href="/oauth/start">Connect Gmail</a> '
        '<a class="btn secondary" href="/">Home</a></p>'
    )
    return _page("Command Center", body, account=account)


# --------------------------------------------------------------------
# Command Center home
# --------------------------------------------------------------------


def _card_html(card) -> str:
    return (
        f'<a class="card tone-{card.tone}" href="/dashboard/list/{card.key}">'
        f'<span class="count">{card.count}</span>'
        f'<span class="card-title">{escape(card.title)}</span>'
        f'<span class="card-blurb">{escape(card.blurb)}</span>'
        "</a>"
    )


def render_command_center(center: CommandCenter, account: str) -> str:
    cards = "".join(_card_html(card) for card in center.cards)
    generated = center.generated_at.strftime("%Y-%m-%d %H:%M")
    body = (
        f"{_banner(center)}"
        '<p class="muted small">Built from your most recent messages · '
        f"generated {escape(generated)}.</p>"
        f'<section class="grid">{cards}</section>'
        '<p class="muted small">Click any card to see the messages behind it.</p>'
        '<p><a class="btn secondary" href="/dashboard/digest">Today\'s Digest</a> '
        "— the same P1/P2/Action/Overdue/Waiting/Due-Soon/Review sections, "
        "as one page you can skim.</p>"
        '<p><a class="btn secondary" href="/dashboard/undo">Undo Last Run</a> '
        '<span class="muted small">— reverses the most recent real Gmail '
        "write (a batch apply or a single Restore/Trash click), if one is "
        "still available to undo.</span></p>"
    )
    return _page("Command Center", body, account=account)


# --------------------------------------------------------------------
# List views
# --------------------------------------------------------------------


def _labels_html(labels: list[str]) -> str:
    if not labels:
        return ""
    chips = "".join(f'<span class="chip">{escape(label)}</span>' for label in labels)
    return f'<div class="labels">{chips}</div>'


def _row_actions_html(row: Row) -> str:
    hidden_fields = (
        ("message_id", row.message_id),
        ("thread_id", row.thread_id),
        ("sender_email", row.sender_email),
        ("sender_name", row.sender_name),
        ("subject", row.subject),
        ("classification", ", ".join(row.labels)),
        ("reason", row.reason),
    )
    hidden_html = "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(value or "")}">'
        for name, value in hidden_fields
    )

    buttons: list[str] = []
    for label, help_text, action in _ROW_ACTIONS:
        if action == "trash":
            # A GET to a confirmation page, not a direct POST — Trash always
            # asks first (CLAUDE.md §5). The confirm page's own form is what
            # actually posts to /dashboard/action/trash.
            confirm_url = "/dashboard/trash-confirm?" + urlencode(
                {name: value or "" for name, value in hidden_fields}
            )
            buttons.append(
                f'<a class="rowbtn active" href="{escape(confirm_url)}" '
                f'title="{escape(help_text)}">{escape(label)}</a>'
            )
        else:
            buttons.append(
                f'<form class="inline" method="post" '
                f'action="/dashboard/action/{action}">{hidden_html}'
                f'<button class="rowbtn active" type="submit" '
                f'title="{escape(help_text)}">{escape(label)}</button></form>'
            )

    legend = "".join(
        f"<li><strong>{escape(label)}</strong> — {escape(help_text)}</li>"
        for label, help_text, _action in _ROW_ACTIONS
    )
    return (
        f'<div class="actions">{"".join(buttons)}</div>'
        "<details class=\"explain\"><summary>What do these buttons do?</summary>"
        f"<ul>{legend}</ul>"
        '<p class="muted small">Keep, Review Correct, Make Sender Rule, Make Domain '
        "Rule and Suggest VIP record your decision in your control workbook right "
        "away. Rule and VIP suggestions still need your approval there before they "
        "change how anything is classified. Restore to Inbox and Trash change real "
        "Gmail — both refuse with a clear message until live writes are turned on "
        "(DRY_RUN=false, GMAIL_PROCESSING_ENABLED=true, and a passed acceptance "
        "run), and Trash always confirms on its own page first.</p></details>"
    )


def _sender_html(row: Row) -> str:
    """Name *and* address, visibly — not just a hover tooltip. A display
    name alone ("Google") isn't enough to tell a real notification from a
    spoofed one; the actual address is the detail that matters."""
    name = (row.sender_name or "").strip()
    address = (row.sender_email or "").strip()
    if name and address and name.lower() != address.lower():
        return f'{escape(name)} <span class="sender-addr">&lt;{escape(address)}&gt;</span>'
    return escape(name or address or "(unknown sender)")


def _row_html(row: Row, show_actions: bool) -> str:
    attach = ' <span class="attach" title="has attachments">📎</span>' if row.has_attachments else ""
    priority = f'<span class="pri pri-{escape(row.priority)}">{escape(row.priority)}</span>' if row.priority else ""
    note = f'<div class="note">{escape(row.note)}</div>' if row.note else ""
    reason = f'<div class="reason">Why flagged: {escape(row.reason)}</div>' if row.reason else ""
    snippet = f'<div class="summary">{escape(row.snippet)}</div>' if row.snippet else ""
    open_link = (
        f'<a class="open-gmail" href="{escape(row.gmail_url)}" target="_blank" '
        'rel="noopener">Open in Gmail →</a>'
        if row.gmail_url
        else ""
    )
    actions = _row_actions_html(row) if show_actions else ""
    return (
        '<article class="row">'
        '<div class="row-head">'
        f'<span class="sender">{_sender_html(row)}</span>'
        f'<span class="when">{escape(_fmt_dt(row.received))}</span>'
        f"{priority}</div>"
        f'<div class="subject">{escape(row.subject)}{attach}</div>'
        f"{snippet}"
        f"{reason}{note}"
        f'<div class="meta">confidence {escape(_fmt_conf(row.confidence))} {open_link}</div>'
        f"{_labels_html(row.labels)}"
        f"{actions}"
        "</article>"
    )


def _action_notice_html(notice: str | None, error: str | None) -> str:
    if error:
        return f'<p class="danger">{escape(error)}</p>'
    if notice:
        return f'<p class="ok-note">{escape(notice)}</p>'
    return ""


def render_list(
    center: CommandCenter,
    card_key: str,
    account: str,
    notice: str | None = None,
    error: str | None = None,
) -> str:
    card = center.card(card_key)
    if card is None:
        body = (
            '<p class="danger">Unknown list.</p>'
            '<p><a class="btn" href="/dashboard">Back to Command Center</a></p>'
        )
        return _page("Command Center", body, account=account)

    rows = center.rows(card_key)
    # The Review queue is the one with the full action toolbar (§13).
    show_actions = card_key == "review"

    if rows:
        rows_html = "".join(_row_html(row, show_actions) for row in rows)
    else:
        rows_html = '<p class="empty">Nothing here right now. That\'s a good thing.</p>'

    body = (
        f"{_banner(center)}"
        f"{_action_notice_html(notice, error)}"
        '<p><a class="back" href="/dashboard">← Command Center</a></p>'
        f"<h2>{escape(card.title)} <span class=\"count-inline\">{card.count}</span></h2>"
        f'<p class="muted">{escape(card.blurb)}</p>'
        f'<section class="rows">{rows_html}</section>'
    )
    return _page(f"{card.title} · Command Center", body, account=account)


# --------------------------------------------------------------------
# Trash confirmation (CLAUDE.md §5 — Trash always confirms first)
# --------------------------------------------------------------------


def render_trash_confirm(
    *,
    account: str,
    message_id: str,
    thread_id: str,
    sender_email: str,
    sender_name: str,
    subject: str,
    classification: str,
    reason: str,
) -> str:
    """The one required stop between clicking "Trash" and it happening.

    Names the exact message, explains Trash is recoverable (not a permanent
    delete), and requires a second, explicit click before anything happens
    to Gmail — a GET here changes nothing.
    """
    hidden_fields = (
        ("message_id", message_id),
        ("thread_id", thread_id),
        ("sender_email", sender_email),
        ("sender_name", sender_name),
        ("subject", subject),
        ("classification", classification),
        ("reason", reason),
    )
    hidden_html = "".join(
        f'<input type="hidden" name="{escape(name)}" value="{escape(value or "")}">'
        for name, value in hidden_fields
    )
    sender = escape(sender_name) or escape(sender_email) or "(unknown sender)"
    body = (
        '<p><a class="back" href="/dashboard/list/review">← Back, don\'t Trash it</a></p>'
        "<h2>Move this message to Gmail Trash?</h2>"
        '<article class="row confirm">'
        f'<div class="row-head"><span class="sender">{sender}</span></div>'
        f'<div class="subject">{escape(subject) or "(no subject)"}</div>'
        "</article>"
        '<p class="muted small">This moves the message to Gmail\'s own Trash — '
        "it stays recoverable there for 30 days. This app never permanently "
        "deletes anything.</p>"
        '<form method="post" action="/dashboard/action/trash">'
        f"{hidden_html}"
        '<button class="btn danger" type="submit">Yes, move to Trash</button> '
        '<a class="btn secondary" href="/dashboard/list/review">Cancel</a>'
        "</form>"
    )
    return _page("Confirm Trash · Command Center", body, account=account)


__all__ = (
    "render_command_center",
    "render_list",
    "render_login",
    "render_not_connected",
    "render_unauthorized",
)
