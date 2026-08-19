"""Server-rendered HTML for Undo Last Run (Phase 12, CLAUDE.md §13, §14).

Same rules as the Command Center (`app/dashboard/views.py`): plain HTML, no
JavaScript, and every piece of email-sourced text (a subject line) is passed
through :func:`html.escape` here — this module's single choke point where
untrusted email text becomes HTML.

Two screens, the same "confirm → explain → require explicit confirmation →
act → log" shape CLAUDE.md §5 already requires for Trash, applied here to a
whole run instead of one message: :func:`render_preview` (a GET that changes
nothing — only its own form, posted to ``/dashboard/undo``, can actually
undo anything) and :func:`render_result` (what happened, message by message,
after the user confirmed).
"""

from __future__ import annotations

from html import escape

from app.undo.service import UndoPreview, UndoResult

_STYLE_LINK = '<link rel="stylesheet" href="/static/dashboard.css">'


def _page(title: str, body: str, account: str | None = None) -> str:
    header = '<h1><a href="/dashboard">Command Center</a></h1>'
    account_bar = (
        f'<div class="account">Signed in as <strong>{escape(account)}</strong></div>'
        if account
        else ""
    )
    return (
        "<!doctype html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{escape(title)}</title>{_STYLE_LINK}</head><body>"
        f"<header>{header}{account_bar}</header>"
        f"<main>{body}</main>"
        "<footer>A GET here never changes anything — only the confirm "
        "button on the preview page can undo a run.</footer>"
        "</body></html>"
    )


def render_no_undo(account: str) -> str:
    body = (
        '<p><a class="back" href="/dashboard">← Command Center</a></p>'
        "<h2>Nothing to undo</h2>"
        "<p>There's no recent real Gmail write available to reverse — either "
        "nothing has changed anything yet, or the last run has already been "
        "undone.</p>"
    )
    return _page("Undo Last Run · Command Center", body, account=account)


def _preview_row_html(msg) -> str:
    before = ", ".join(msg.labels_before) or "(none)"
    after = ", ".join(msg.labels_after) or "(none)"
    return (
        '<article class="row">'
        f'<div class="subject">{escape(msg.subject) or "(no subject)"}</div>'
        f'<div class="summary">{escape(msg.action_taken)}</div>'
        f'<div class="meta">was: {escape(before)} &nbsp;→&nbsp; undo restores: {escape(before)} '
        f'<span class="muted small">(currently: {escape(after)})</span></div>'
        "</article>"
    )


def render_preview(preview: UndoPreview, account: str) -> str:
    rows_html = "".join(_preview_row_html(m) for m in preview.messages) or (
        '<p class="empty">No individually reversible messages were found for this run.</p>'
    )
    body = (
        '<p><a class="back" href="/dashboard">← Command Center</a></p>'
        "<h2>Undo this run?</h2>"
        f'<p class="muted">Run <code>{escape(preview.run_id)}</code> · completed '
        f"{escape(preview.completed_at)} · {preview.message_count} message"
        f"{'s' if preview.message_count != 1 else ''} affected.</p>"
        '<p class="muted small">This restores each message\'s labels and Inbox state to '
        "exactly what they were before this run — it does not re-run the "
        "classifier, and it does not re-check whether the rules would decide "
        "something different today.</p>"
        f'<section class="rows">{rows_html}</section>'
        '<form method="post" action="/dashboard/undo">'
        f'<input type="hidden" name="run_id" value="{escape(preview.run_id)}">'
        '<button class="btn danger" type="submit">Yes, undo this run</button> '
        '<a class="btn secondary" href="/dashboard">Cancel</a>'
        "</form>"
    )
    return _page("Undo Last Run · Command Center", body, account=account)


_OUTCOME_LABELS: dict[str, str] = {
    "restored": "Restored",
    "already_ok": "Already in that state — nothing to do",
    "not_found": "No longer in Gmail — not recoverable",
}


def _outcome_row_html(outcome) -> str:
    label = _OUTCOME_LABELS.get(outcome.outcome, outcome.outcome)
    tone = "ok-note" if outcome.outcome != "not_found" else "danger"
    return (
        f'<li><span class="{tone}">{escape(label)}</span> — '
        f"{escape(outcome.message_id)}: {escape(outcome.detail)}</li>"
    )


def render_result(result: UndoResult, account: str) -> str:
    if result.status == "gate_closed":
        body = (
            '<p><a class="back" href="/dashboard">← Command Center</a></p>'
            "<h2>Couldn't undo</h2>"
            f'<p class="danger">{escape(result.message)}</p>'
        )
        return _page("Undo Last Run · Command Center", body, account=account)

    if result.status == "not_found":
        body = (
            '<p><a class="back" href="/dashboard">← Command Center</a></p>'
            "<h2>Nothing to undo</h2>"
            f'<p class="muted">{escape(result.message)}</p>'
        )
        return _page("Undo Last Run · Command Center", body, account=account)

    items = "".join(_outcome_row_html(o) for o in result.outcomes) or (
        "<li>Nothing needed restoring.</li>"
    )
    body = (
        '<p><a class="back" href="/dashboard">← Command Center</a></p>'
        "<h2>Undo complete</h2>"
        f'<p class="ok-note">{result.restored_count} of {len(result.outcomes)} '
        "message(s) restored to their previous state.</p>"
        f"<ul>{items}</ul>"
    )
    return _page("Undo Last Run · Command Center", body, account=account)


__all__ = ("render_no_undo", "render_preview", "render_result")
