"""FastAPI application entry point.

This app is deliberately small: connect one Gmail account, classify mail
with deterministic rules first and an AI second opinion only when the rules
can't settle it, and apply the result as real Gmail labels — including
recognizing labels the user already made by hand (see
:mod:`app.gmail.vendor_labels`), like an existing "Uber" folder catching
Uber receipts. There is no dashboard, no Sheets workbook, no digest email,
and no audit trail; a personal rules file (``config/rules.toml``) is the only
thing the user edits directly.

Routes:
- ``/health`` — process status.
- ``/`` — connect/disconnect Gmail, links to the read-only tools below.
- ``/oauth/*`` — Google OAuth (start / callback / status / disconnect).
- ``/gmail/preview`` — read-only: the last few messages' metadata.
- ``/classify/preview`` — read-only: what the rules engine would do to
  recent mail. Changes nothing.
- ``/gmail/apply`` — classify up to `limit` recent messages and, only with
  `confirm=true` and the write gate open (``DRY_RUN=false`` and
  ``GMAIL_PROCESSING_ENABLED=true``), actually apply it.
- ``/gmail/labels/sync-colors`` — cosmetic label color-coding.
- ``/realtime/*`` — runs one check-for-new-mail-and-classify-it cycle.
  Meant to be called on a timer by something outside this process (a cron
  job, a scheduled HTTP ping) rather than looped internally — see
  ``app/scheduling/service.py`` for why.
"""

from __future__ import annotations

import html
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import __version__
from app.config import get_settings
from app.gmail import client as gmail_client
from app.gmail import oauth as gmail_oauth
from app.gmail import tokens as gmail_tokens
from app.google_api import NotConnectedError
from app.logging_config import configure_logging, get_logger
from app.oauth_scopes import describe

_PLACEHOLDER_SESSION_SECRET = "change-me-to-a-long-random-string"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("app.main")

    if settings.is_production and settings.session_secret == _PLACEHOLDER_SESSION_SECRET:
        # SESSION_SECRET signs the OAuth CSRF state and derives the key that
        # encrypts the stored OAuth token — booting production with the
        # default placeholder would sign real state and encrypt a real
        # refresh token with a secret anyone can read in this file. Fail the
        # deploy loudly rather than run insecurely.
        raise RuntimeError(
            "Refusing to start: APP_ENV=production but SESSION_SECRET is "
            "still the placeholder value. Set a real one in Render's "
            "environment variables — generate it with: "
            "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )

    app = FastAPI(
        title="Gmail Intelligence Agent",
        version=__version__,
        description=(
            "Personal Gmail intelligence agent. Read-only Gmail access, a "
            "deterministic rules engine, an AI second opinion for what the "
            "rules can't settle, real Gmail label writes gated behind "
            "DRY_RUN and GMAIL_PROCESSING_ENABLED, and a POST /realtime/poll "
            "endpoint meant to be called on a timer by something outside "
            "this process (a cron job, a scheduled ping) to classify new "
            "mail as it arrives."
        ),
    )

    from app.scheduling.service import RealTimePoller

    app.state.realtime_poller = RealTimePoller()

    # ---------- System endpoints ----------

    @app.get("/health", tags=["system"])
    def health() -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "app_env": settings.app_env,
                "dry_run": settings.dry_run,
                "gmail_processing_enabled": settings.gmail_processing_enabled,
                "gmail_connected": gmail_tokens.token_exists(),
                "reconnect_required": bool(gmail_tokens.missing_scopes()),
            }
        )

    @app.get("/", response_class=HTMLResponse, tags=["system"])
    def index() -> HTMLResponse:
        stored = gmail_tokens.load_token()
        if stored is None:
            body = _render_disconnected()
        else:
            body = _render_connected(
                stored.account_email or "(unknown account)",
                missing_scopes=gmail_tokens.missing_scopes(),
            )
        return HTMLResponse(body)

    # ---------- OAuth ----------

    @app.get("/oauth/start", tags=["oauth"])
    def oauth_start() -> RedirectResponse:
        try:
            url, _state = gmail_oauth.build_authorization_url()
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return RedirectResponse(url, status_code=307)

    @app.get("/oauth/callback", tags=["oauth"])
    def oauth_callback(request: Request) -> HTMLResponse:
        params = request.query_params
        error = params.get("error")
        if error:
            log.warning("oauth_callback_error", extra={"error": error})
            return HTMLResponse(
                _render_error(f"Google reported an error: {error}"),
                status_code=400,
            )

        code = params.get("code")
        state = params.get("state")
        if not code or not state:
            return HTMLResponse(
                _render_error("Missing `code` or `state` in callback URL."),
                status_code=400,
            )

        try:
            stored = gmail_oauth.exchange_code_for_token(code=code, state=state)
        except PermissionError as exc:
            return HTMLResponse(_render_error(str(exc)), status_code=400)
        except RuntimeError as exc:
            return HTMLResponse(_render_error(str(exc)), status_code=400)

        # Show the refresh token once so it can be copied into Render's env
        # vars — but not if it already matches the configured seed, since
        # then there's nothing new to copy.
        reveal_refresh_token = None
        if stored.refresh_token and stored.refresh_token != settings.google_oauth_seed_refresh_token:
            reveal_refresh_token = stored.refresh_token

        return HTMLResponse(
            _render_connected(
                stored.account_email or "(unknown)",
                missing_scopes=gmail_tokens.missing_scopes(),
                reveal_refresh_token=reveal_refresh_token,
            )
        )

    @app.get("/oauth/status", tags=["oauth"])
    def oauth_status() -> JSONResponse:
        stored = gmail_tokens.load_token()
        if stored is None:
            return JSONResponse({"connected": False})
        return JSONResponse(
            {
                "connected": True,
                "account_email": stored.account_email,
                "scopes": stored.scopes,
                "expiry_iso": stored.expiry_iso,
            }
        )

    @app.post("/oauth/disconnect", tags=["oauth"])
    def oauth_disconnect() -> JSONResponse:
        removed = gmail_tokens.clear_token()
        log.info("oauth_disconnected", extra={"removed": removed})
        note = None
        if settings.google_oauth_seed_refresh_token:
            note = (
                "GOOGLE_OAUTH_SEED_REFRESH_TOKEN is still set in this "
                "environment — the next restart will reconnect this account "
                "automatically from it. Remove that variable in Render's "
                "dashboard too if you want the disconnect to stick."
            )
        return JSONResponse({"disconnected": removed, "note": note})

    # ---------- Read-only Gmail preview ----------

    @app.get("/gmail/preview", tags=["gmail"])
    def gmail_preview(limit: int = 10) -> JSONResponse:
        try:
            client = gmail_client.get_client()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        limit = max(1, min(limit, 25))
        summaries = client.list_recent_message_summaries(max_results=limit)
        profile = client.get_profile()
        return JSONResponse(
            {
                "account": profile.get("emailAddress"),
                "messages_total": profile.get("messagesTotal"),
                "threads_total": profile.get("threadsTotal"),
                "preview_count": len(summaries),
                "messages": gmail_client.format_summaries_for_display(summaries),
            }
        )

    def _require_full_grant() -> None:
        """Reject calls made with a token that predates the ``gmail.modify``
        write scope."""
        missing = gmail_tokens.missing_scopes()
        if missing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This Google account was connected before this permission "
                    "was added. Reconnect at /oauth/start to grant: "
                    + ", ".join(missing)
                ),
            )

    # ---------- Real Gmail writes ----------

    @app.post("/gmail/apply", tags=["gmail"])
    def gmail_apply_route(
        limit: int = 10,
        query: str | None = None,
        confirm: bool = False,
        use_ai: bool = False,
        contacts: bool = True,
        rules: bool = True,
    ) -> JSONResponse:
        """Classify up to `limit` recent messages and, only if `confirm=true`
        and the write gate allows it, actually apply the result to Gmail —
        real labels, real archive/restore, real Mark Important. Never Trash.
        `confirm=false` (the default) always previews, regardless of
        settings, the same "see it before you do it" shape as
        `/classify/preview`.
        """
        _require_full_grant()
        from app.gmail import write_service

        try:
            report = write_service.apply_recent(
                limit=limit,
                query=query,
                confirm=confirm,
                use_ai=use_ai,
                include_contacts=contacts,
                include_rules=rules,
            )
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "note": (
                    "Nothing was written to Gmail."
                    if not report.wrote_to_gmail
                    else "Applied for real — see results for exactly what changed."
                ),
                **report.as_dict(),
            }
        )

    @app.post("/gmail/labels/sync-colors", tags=["gmail"])
    def gmail_sync_label_colors_route() -> JSONResponse:
        """Color-code every already-existing taxonomy label in Gmail per
        :data:`app.gmail.write_client.LABEL_COLORS`. Purely cosmetic (label
        color, not content or placement) so this isn't behind the DRY_RUN/
        GMAIL_PROCESSING_ENABLED write gate -- it needs only the
        ``gmail.labels`` scope for label color-coding. Any label not yet
        created in Gmail is skipped, not created; that stays classification's
        job via :meth:`~app.gmail.write_client.GmailWriteClient.ensure_labels`.
        """
        from googleapiclient.errors import HttpError

        _require_full_grant()
        from app.gmail.write_client import get_write_client

        try:
            client = get_write_client()
            outcomes = client.sync_label_colors()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except HttpError as exc:
            if getattr(exc.resp, "status", None) == 403:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Google rejected this with 'insufficient authentication "
                        "scopes' -- the gmail.labels permission (added after this "
                        "account was last connected) hasn't actually been granted "
                        "yet, even if the stored token believes it has it. "
                        "Reconnect at /oauth/start to grant it for real, then "
                        "retry this call."
                    ),
                ) from exc
            raise

        return JSONResponse(
            {
                "colored": sum(1 for v in outcomes.values() if v == "colored"),
                "not_created_yet": sum(
                    1 for v in outcomes.values() if v == "not created yet"
                ),
                "labels": outcomes,
            }
        )

    # ---------- Classification preview (read-only) ----------

    @app.get("/classify/preview", tags=["classification"])
    def classify_preview(
        limit: int = 10,
        query: str | None = None,
        contacts: bool = True,
        rules: bool = True,
        ai: bool = True,
    ) -> JSONResponse:
        """Show what the rules engine *would* do. Changes nothing in Gmail."""
        from app.ai import build_provider, describe_provider
        from app.ai.costs import CostTracker
        from app.classification import pipeline

        provider = build_provider() if ai else None
        tracker = CostTracker()

        try:
            results = pipeline.preview_recent(
                limit=limit,
                query=query,
                include_contacts=contacts,
                include_rules=rules,
                use_ai=ai,
                provider=provider,
                tracker=tracker,
            )
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "dry_run": True,
                "gmail_modified": False,
                "note": (
                    "These are proposed decisions only. Nothing in your Gmail "
                    "has been changed. Use /gmail/apply to actually apply them."
                ),
                "ai": (
                    describe_provider(provider) if provider is not None else {"enabled": False}
                ),
                "cost": tracker.summary(),
                "summary": pipeline.summarize(results),
                "messages": [result.as_dict() for result in results],
            }
        )

    # ---------- Near-real-time processing ----------
    #
    # No background loop runs inside this process. Something outside it —
    # a cron job, a scheduled HTTP ping, Windows Task Scheduler — is meant
    # to call POST /realtime/poll on a timer instead. That request is also
    # what wakes a sleeping host (e.g. Render's free plan) back up, so
    # there's no "the loop died while the host was asleep" failure mode to
    # worry about — see app/scheduling/service.py.

    @app.get("/realtime/status", tags=["realtime"])
    def realtime_status() -> JSONResponse:
        return JSONResponse(app.state.realtime_poller.status.as_dict())

    @app.post("/realtime/poll", tags=["realtime"])
    async def realtime_poll(use_ai: bool = True) -> JSONResponse:
        """Run exactly one poll cycle right now: find mail that's new since
        the last call, classify it, and apply it if the write gate allows.
        Call this on a schedule from outside the app — see the module
        docstring above.
        """
        try:
            report = await app.state.realtime_poller.run_one_cycle(use_ai=use_ai)
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "note": (
                    "First poll for this account — recorded the current "
                    "Gmail history id as the starting point. Nothing is "
                    "processed on this call; new mail from here on will be."
                    if report.bootstrapped
                    else "Nothing new since the last poll."
                    if report.messages_seen == 0
                    else (
                        "Classified new mail and applied it to Gmail."
                        if report.gate_allowed
                        else "Classified new mail as a proposal only — "
                        "real-time writes are gated closed."
                    )
                ),
                **report.as_dict(),
            }
        )

    log.info(
        "app_started",
        extra={
            "app_env": settings.app_env,
            "dry_run": settings.dry_run,
            "gmail_processing_enabled": settings.gmail_processing_enabled,
            "gmail_connected": gmail_tokens.token_exists(),
        },
    )
    return app


# ---------- HTML helpers (tiny, no template engine) ----------

_BASE_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:720px;margin:3rem auto;"
    "padding:0 1rem;color:#222;line-height:1.5;}"
    "code{background:#eee;padding:2px 4px;border-radius:3px;}"
    ".btn{display:inline-block;padding:.6rem 1rem;background:#1a73e8;color:#fff;"
    "text-decoration:none;border-radius:4px;border:0;font:inherit;cursor:pointer;}"
    ".btn.secondary{background:#666;}"
    ".danger{color:#b00;}"
    "ul.scopes li{margin-bottom:.35rem;}"
    ".ok{color:#0a7f2e;}"
    ".warn{background:#fff4e5;border:1px solid #f0b429;padding:.75rem 1rem;"
    "border-radius:4px;margin:1rem 0;}"
    "pre.secret{white-space:pre-wrap;word-break:break-all;background:#fff;"
    "border:1px solid #ddd;padding:.5rem;border-radius:4px;user-select:all;}"
)


def _render_disconnected() -> str:
    scope_html = "".join(
        f"<li><code>{scope}</code> — {desc}</li>" for scope, desc in describe()
    )
    return (
        "<!doctype html><html><head><title>Gmail Intelligence Agent</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        "<h1>Gmail Intelligence Agent</h1>"
        "<p>Not connected yet.</p>"
        "<p>The app will ask Google for these permissions (and only these):</p>"
        f"<ul class=\"scopes\">{scope_html}</ul>"
        "<p><a class=\"btn\" href=\"/oauth/start\">Connect Gmail</a></p>"
        "<p style=\"margin-top:2rem;font-size:.9em;color:#555;\">"
        "You'll be sent to Google's consent screen. This app never asks for "
        "send permission, and never permanently deletes anything."
        "</p></body></html>"
    )


def _render_connected(
    account_email: str,
    missing_scopes: list[str] | None = None,
    reveal_refresh_token: str | None = None,
) -> str:
    banner = ""
    reveal_section = ""
    if reveal_refresh_token:
        reveal_section = (
            "<div class=\"warn\"><strong>Deploying to Render? Copy this now — "
            "it won't be shown again.</strong>"
            "<p>Render wipes local files on every redeploy, but never wipes "
            "its own environment variable settings. Paste the value below "
            "into Render's dashboard as <code>GOOGLE_OAUTH_SEED_REFRESH_TOKEN</code> "
            "so this connection survives a redeploy without you reconnecting "
            "Gmail by hand every time. Running this locally instead? You can "
            "ignore this box.</p>"
            f"<pre class=\"secret\">{html.escape(reveal_refresh_token)}</pre>"
            "<p>Optional: also set <code>GOOGLE_OAUTH_SEED_ACCOUNT_EMAIL</code> "
            f"to <code>{html.escape(account_email)}</code> so the connected "
            "account shows correctly after a reseed.</p>"
            "<p>Lost it? No harm done — just reconnect Gmail at "
            "<a href=\"/oauth/start\">/oauth/start</a> to get a new one.</p></div>"
        )
    if missing_scopes:
        items = "".join(f"<li><code>{scope}</code></li>" for scope in missing_scopes)
        banner = (
            "<div class=\"warn\"><strong>Reconnect required.</strong> "
            "This account was connected before these permissions were added:"
            f"<ul>{items}</ul>"
            f"<a href=\"/oauth/start\">Reconnect now</a>.</div>"
        )

    return (
        "<!doctype html><html><head><title>Gmail Intelligence Agent</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        "<h1>Gmail Intelligence Agent</h1>"
        f"<p class=\"ok\">Connected as <strong>{account_email}</strong>.</p>"
        f"{banner}"
        f"{reveal_section}"
        "<p>Gmail access includes labels, archive, restore, and Mark "
        "Important — never sending email, and never a permanent delete. "
        "Live writes stay off until you deliberately set DRY_RUN=false and "
        "GMAIL_PROCESSING_ENABLED=true.</p>"
        "<p><a class=\"btn\" href=\"/gmail/preview\">Preview last 10 messages (metadata only)</a></p>"
        "<h2>See what the rules engine would do (raw JSON)</h2>"
        "<p>Runs the classification rules over your recent mail and shows the "
        "proposed decisions. <strong>Nothing in Gmail is changed.</strong></p>"
        "<p><a class=\"btn secondary\" href=\"/classify/preview?limit=25\">"
        "Preview classification of last 25 messages</a></p>"
        "<form method=\"post\" action=\"/oauth/disconnect\" style=\"margin-top:1rem;\">"
        "<button class=\"btn secondary\" type=\"submit\">Disconnect &amp; delete stored token</button>"
        "</form>"
        "<p style=\"margin-top:2rem;font-size:.9em;color:#555;\">"
        "Health: <a href=\"/health\"><code>/health</code></a>&nbsp;·&nbsp;"
        "OAuth status: <a href=\"/oauth/status\"><code>/oauth/status</code></a>&nbsp;·&nbsp;"
        "Real-time status: <a href=\"/realtime/status\"><code>/realtime/status</code></a>"
        "</p></body></html>"
    )


def _render_error(message: str) -> str:
    return (
        "<!doctype html><html><head><title>Error</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        "<h1>Something went wrong</h1>"
        f"<p class=\"danger\">{message}</p>"
        "<p><a href=\"/\">Back home</a></p>"
        "</body></html>"
    )


app = create_app()
