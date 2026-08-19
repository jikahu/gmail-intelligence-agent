"""FastAPI application entry point.

Phases wired so far:
- Phase 0: health endpoint, landing page.
- Phase 1: Google OAuth (start / callback / status / disconnect) and a
  read-only Gmail preview page proving we can read but not modify.
- Phase 2: the Google Sheets control workbook (status / init).
- Phase 3: the deterministic rules engine, exposed read-only at
  /classify/preview. It proposes decisions; it never applies them.
- Phase 4: the AI provider layer, consulted only for what the rules couldn't
  settle. AI suggests; the rules engine decides.
- Phase 5: attachment text extraction. Attachments are read as information,
  never executed.
- Phase 6-7: intelligence extraction and stateful follow-up timers.
- Phase 8: the Command Center dashboard — the first real screen. Google
  Sign-In, cards you can click, a Review queue. Still no Gmail changes.
- Phase 9: audit trail, Review-queue feedback, and learning. Five of the
  Review queue's seven buttons now record real decisions in the control
  workbook. Rule and VIP suggestions still require separate approval there —
  nothing is silently promoted. Still no Gmail changes.
- Phase 10: the 250-email stratified acceptance run — CLAUDE.md §15's launch
  quality gate. Pulls a deliberately mixed sample of real mail, classifies it
  read-only, and reports the one number that matters most: how many
  protected/important emails were wrongly routed to Review (must be zero).
  Golden-dataset support runs the same check against a small hand-labeled
  fixture set on every test run. Still no Gmail changes.
- Phase 11: the first phase that can change real Gmail. Add/remove `AI/*`
  labels, archive, restore to Inbox, mark Important (add-only — never
  auto-removed), and a user-confirmed Trash (recoverable for 30 days, never
  a permanent delete). Every write path — the manual /gmail/apply endpoint
  and the two live dashboard buttons — checks the same gate first: DRY_RUN
  must be false, GMAIL_PROCESSING_ENABLED must be true, and the last
  250-email acceptance run must have passed. All three default to the safe
  state, so nothing here writes to Gmail until the user deliberately turns
  it on.
- Phase 12: Undo Last Run. Reverses the most recent real Gmail write — a
  confirmed batch /gmail/apply, or a single Restore/Trash click, each of
  which now gets its own run_id and System_Runs row specifically so this
  phase has something to find. Restoration, not replay: every message goes
  back to the exact labels/Inbox state recorded before the original write,
  never through the classifier again. The same confirm-first discipline as
  Trash, and the same write gate as every other write path — no exceptions
  for "the original write already happened."
- Phase 13: near-real-time processing. A background loop (off unless
  REALTIME_ENABLED=true) polls Gmail's history feed every
  REALTIME_POLL_INTERVAL_SECONDS for genuinely new mail, classifies each
  message with full thread context, and — only when the same write gate as
  every other write path allows it — applies the result. Idempotent by
  construction (an already-correct message produces no Gmail call), retries
  transient failures, and never lets one bad message or thread stop the
  cycle. A manual POST /realtime/poll runs one cycle by hand at any time.
- Phase 14: the daily digest. Reuses the Command Center's own data
  (Phase 8), reordered into CLAUDE.md §13's digest section order (P1, P2,
  Action Required, Overdue, Waiting for Reply, Due Soon, AI Review) and
  viewable at /dashboard/digest. A background scheduler (on by default —
  DIGEST_SCHEDULER_ENABLED=true) checks the clock and builds + records one
  digest per calendar day, no earlier than DIGEST_HOUR in DIGEST_TIMEZONE. A
  manual POST /digest/scan builds one immediately. Read-only against Gmail;
  persisting a digest writes only a summary row to the control workbook, not
  a Gmail change. Real email delivery (Gmail send) is an explicit, separate
  follow-up — nothing in this app has ever sent mail.
- Phase 15: the 12-month historical cleanup. A deliberate, manually
  triggered sweep of up to the last 12 months of mail (POST
  /historical/start), run separately from the Phase 13 real-time loop and
  the Phase 14 digest — neither ever starts one automatically. Runs as a
  background task (a large mailbox can take a while), paging through Gmail
  a batch at a time with the same write gate and confirm-first shape as
  Phase 11's manual apply — confirm=false (the default) always previews,
  so the first run of this is a dry run for free. GET /historical/status
  reports live progress; POST /historical/cancel stops it between pages. A
  safety-invariant violation (a protected email nearly routed to Review)
  aborts the whole run rather than being silently skipped — the same
  "a crash beats a hidden email" philosophy the 250-email acceptance run
  established in Phase 10. Every real change lands in Audit_Log under one
  run_id with a System_Runs row, so Undo Last Run (Phase 12) works on a
  historical run exactly like any other.
- Phase 16: Render deployment. render.yaml targets Render's free plan (no
  new cost for a single-account project) with buildCommand/startCommand/
  healthCheckPath wired to this app. No classification, safety, or Gmail
  behavior changes — this phase is purely about running the existing app
  somewhere other than a developer's own machine. The one real problem it
  solves: Render's filesystem is wiped on every redeploy, so the local
  encrypted OAuth token file (app/gmail/tokens.py) wouldn't survive one. A
  refresh token barely changes once issued, so instead of a paid persistent
  disk, GOOGLE_OAUTH_SEED_REFRESH_TOKEN — set once in Render's own
  environment-variable store, which does survive a redeploy — reseeds the
  local file automatically on boot when it's missing. create_app() also now
  refuses to start in production with a still-default SESSION_SECRET,
  since that value both signs dashboard sessions and encrypts the stored
  token.
"""

from __future__ import annotations

import html
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import CURRENT_PHASE, __version__
from app.config import get_settings
from app.dashboard import actions as dashboard_actions
from app.dashboard import auth as dashboard_auth
from app.dashboard import service as dashboard_service
from app.dashboard import views as dashboard_views
from app.gmail import client as gmail_client
from app.gmail import oauth as gmail_oauth
from app.gmail import tokens as gmail_tokens
from app.google_api import NotConnectedError
from app.logging_config import configure_logging, get_logger
from app.oauth_scopes import describe
from app.sheets import client as sheets_client
from app.sheets import workbook as sheets_workbook

#: Project-root static directory (app/main.py → parent.parent).
_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


_PLACEHOLDER_SESSION_SECRET = "change-me-to-a-long-random-string"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("app.main")

    if settings.is_production and settings.session_secret == _PLACEHOLDER_SESSION_SECRET:
        # SESSION_SECRET signs dashboard sessions and derives the key that
        # encrypts the stored OAuth token — booting production with the
        # default placeholder would sign real sessions and encrypt a real
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
            "Personal Gmail intelligence agent. Currently at Phase 16 — "
            "read-only Gmail access, the Google Sheets control workbook, the "
            "deterministic rules engine, an AI second opinion, attachment text "
            "extraction, intelligence + follow-up timers, the Command Center "
            "dashboard, an audit trail plus Review-queue feedback and learning, "
            "the 250-email acceptance run (the launch quality gate), real Gmail "
            "writes — labels, archive, restore, Mark Important, and a "
            "user-confirmed Trash — gated behind DRY_RUN, "
            "GMAIL_PROCESSING_ENABLED, and a passed acceptance run, Undo Last "
            "Run to reverse the most recent one, near-real-time processing of "
            "new mail via a background poll loop (off by default; "
            "REALTIME_ENABLED=true turns it on), a daily digest — "
            "P1/P2/Action Required/Overdue/Waiting for Reply/Due Soon/Review, "
            "viewable any time at /dashboard/digest and built automatically "
            "once a day by a background scheduler (on by default), a "
            "manually-triggered 12-month historical cleanup sweep "
            "(POST /historical/start) that runs as its own background task "
            "with the same write gate and confirm-first preview as every "
            "other write path, and now deployable to Render's free plan with "
            "a durable Gmail connection that survives a redeploy without a "
            "paid disk."
        ),
    )

    from app.digest.scheduler import DigestScheduler
    from app.historical.runner import HistoricalRunner
    from app.scheduling.service import RealTimePoller

    app.state.realtime_poller = RealTimePoller(settings.realtime_poll_interval_seconds)
    app.state.digest_scheduler = DigestScheduler()
    app.state.historical_runner = HistoricalRunner()

    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    def _set_dashboard_session(response, email: str) -> None:
        """Attach a signed dashboard-session cookie to a response."""
        response.set_cookie(
            dashboard_auth.SESSION_COOKIE,
            dashboard_auth.issue_session(email),
            max_age=settings.dashboard_session_max_age_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=settings.is_production,
        )

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
                "digest_timezone": settings.digest_timezone,
                "phase": CURRENT_PHASE,
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
        # vars (Phase 16) — but not if it already matches the configured
        # seed, since then there's nothing new to copy.
        reveal_refresh_token = None
        if stored.refresh_token and stored.refresh_token != settings.google_oauth_seed_refresh_token:
            reveal_refresh_token = stored.refresh_token

        response = HTMLResponse(
            _render_connected(
                stored.account_email or "(unknown)",
                missing_scopes=gmail_tokens.missing_scopes(),
                reveal_refresh_token=reveal_refresh_token,
            )
        )
        # Connecting Gmail proves you own the account, so it also signs you into
        # the Command Center — no second Google round-trip needed.
        if stored.account_email and dashboard_auth.is_authorized(stored.account_email):
            _set_dashboard_session(response, stored.account_email)
        return response

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
        """Reject calls made with a token that predates the scope it needs
        (Sheets access from Phase 2, or Gmail write access from Phase 11)."""
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

    # ---------- Real Gmail writes (Phase 11) ----------

    @app.post("/gmail/apply", tags=["gmail"])
    def gmail_apply_route(
        limit: int = 10,
        query: str | None = None,
        confirm: bool = False,
        use_ai: bool = False,
        contacts: bool = True,
        rules: bool = True,
        attachments: bool = True,
    ) -> JSONResponse:
        """Classify up to `limit` recent messages and, only if `confirm=true`
        and the write gate allows it, actually apply the result to Gmail —
        real labels, real archive/restore, real Mark Important. Never Trash;
        that stays a separate, always-confirmed dashboard action.
        `confirm=false` (the default) always previews, regardless of settings,
        the same "see it before you do it" shape as /acceptance/run.
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
                include_workbook=rules,
                read_attachments=attachments,
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
        """Color-code every already-existing ``AI/*`` label in Gmail per
        :data:`app.gmail.write_client.LABEL_COLORS`. Purely cosmetic (label
        color, not content or placement) so this isn't behind the DRY_RUN/
        GMAIL_PROCESSING_ENABLED write gate -- it needs only the same
        ``gmail.modify`` scope label creation already uses. Any label not yet
        created in Gmail is skipped, not created; that stays classification's
        job via :meth:`~app.gmail.write_client.GmailWriteClient.ensure_labels`.
        """
        _require_full_grant()
        from app.gmail.write_client import get_write_client

        try:
            client = get_write_client()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        outcomes = client.sync_label_colors()
        return JSONResponse(
            {
                "colored": sum(1 for v in outcomes.values() if v == "colored"),
                "not_created_yet": sum(
                    1 for v in outcomes.values() if v == "not created yet"
                ),
                "labels": outcomes,
            }
        )

    # ---------- Sheets control workbook ----------

    @app.get("/sheets/status", tags=["sheets"])
    def sheets_status() -> JSONResponse:
        if not gmail_tokens.token_exists():
            return JSONResponse(
                {"connected": False, "initialized": False, "workbook_id": None}
            )

        missing = gmail_tokens.missing_scopes()
        if missing:
            return JSONResponse(
                {
                    "connected": True,
                    "reconnect_required": True,
                    "missing_scopes": missing,
                    "initialized": False,
                    "workbook_id": None,
                }
            )

        try:
            workbook_id = sheets_workbook.find_workbook_id(
                sheets_client.get_drive_service()
            )
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "connected": True,
                "reconnect_required": False,
                "initialized": workbook_id is not None,
                "workbook_id": workbook_id,
                "workbook_url": (
                    sheets_workbook.workbook_url(workbook_id) if workbook_id else None
                ),
            }
        )

    @app.post("/sheets/init", tags=["sheets"])
    def sheets_init() -> JSONResponse:
        """Create the control workbook if needed and bring it up to schema.

        Idempotent — running it twice is harmless.
        """
        _require_full_grant()
        try:
            info = sheets_workbook.ensure_workbook()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "workbook_id": info.spreadsheet_id,
                "workbook_url": info.url,
                "created": info.created,
                "tabs_created": info.tabs_created,
                "columns_added": info.columns_added,
                "settings_seeded": info.settings_seeded,
                "changed": info.changed,
            }
        )

    @app.get("/sheets/settings", tags=["sheets"])
    def sheets_settings() -> JSONResponse:
        """Read back the Settings tab — the app's editable control panel."""
        _require_full_grant()
        from app.sheets.repository import ControlWorkbook

        try:
            workbook = ControlWorkbook.connect()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "workbook_id": workbook.spreadsheet_id,
                "workbook_url": sheets_workbook.workbook_url(workbook.spreadsheet_id),
                "settings": workbook.settings.all(),
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
        attachments: bool = True,
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
                include_workbook=rules,
                use_ai=ai,
                read_attachments=attachments,
                provider=provider,
                tracker=tracker,
            )
            report = pipeline.build_intelligence(results)
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "dry_run": True,
                "gmail_modified": False,
                "note": (
                    "These are proposed decisions only. Nothing in your Gmail "
                    "has been changed. Applying decisions arrives in Phase 11."
                ),
                "ai": (
                    describe_provider(provider) if provider is not None else {"enabled": False}
                ),
                "cost": tracker.summary(),
                "summary": pipeline.summarize(results),
                "intelligence": report.as_dict(),
                "messages": [result.as_dict() for result in results],
            }
        )

    @app.post("/intelligence/scan", tags=["intelligence"])
    def intelligence_scan(
        limit: int = 25,
        query: str | None = None,
        persist: bool = True,
    ) -> JSONResponse:
        """Extract deadlines, money, subscriptions, trips & orders from recent
        mail, and (by default) record them in the control workbook.

        Reads Gmail only — it never modifies a message. Writing to the workbook
        is not a Gmail change; the mailbox is untouched. Pass ``persist=false``
        to see what would be written without writing it.
        """
        from app.classification import pipeline
        from app.intelligence import persistence
        from app.sheets.repository import ControlWorkbook

        try:
            results = pipeline.preview_recent(
                limit=limit,
                query=query,
                use_ai=False,
                read_attachments=False,
            )
            report = pipeline.build_intelligence(results)
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        written: dict[str, object] | None = None
        if persist:
            try:
                workbook = ControlWorkbook.connect()
                written = persistence.persist(workbook, report)
            except NotConnectedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "gmail_modified": False,
                "persisted": persist,
                "note": (
                    "Intelligence was extracted from your mail. Your Gmail was "
                    "not changed. Deadlines, subscriptions and trips are recorded "
                    "in your control workbook."
                    if persist
                    else "Preview only — nothing was written to the workbook."
                ),
                "written": written,
                "intelligence": report.as_dict(),
            }
        )

    @app.post("/followup/scan", tags=["followup"])
    def followup_scan(
        limit: int = 25,
        query: str | None = None,
        persist: bool = False,
    ) -> JSONResponse:
        """Surface what needs chasing: deadlines due soon or overdue, threads
        you're waiting on, and actions that have gone stale.

        Uses 3-business-day timers that skip weekends and US + Kenya public
        holidays. Read-only — proposes an `AI/Waiting-For-Reply` label but
        applies nothing. With `persist=true`, re-records deadlines with their
        sharpened (due-soon/overdue) status in the workbook.
        """
        from datetime import date

        from app.classification import pipeline
        from app.followup import service as followup_service
        from app.intelligence import persistence
        from app.sheets.repository import ControlWorkbook

        today = date.today()

        try:
            results = pipeline.preview_recent(
                limit=limit,
                query=query,
                use_ai=False,
                read_attachments=False,
            )
            report = pipeline.build_intelligence(results)
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        followup_service.refine_report(report, today)
        followups = followup_service.evaluate_from_results(results, report, today)

        written: dict[str, object] | None = None
        if persist:
            try:
                workbook = ControlWorkbook.connect()
                written = persistence.persist(workbook, report, today=today)
            except NotConnectedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "gmail_modified": False,
                "persisted": persist,
                "note": (
                    "These are things to chase, computed with 3-business-day "
                    "timers (US + Kenya holidays excluded). Nothing in your Gmail "
                    "was changed."
                ),
                "written": written,
                "followups": followups.as_dict(),
            }
        )

    # ---------- Audit trail (Phase 9) ----------

    @app.post("/audit/scan", tags=["audit"])
    def audit_scan(
        limit: int = 10,
        query: str | None = None,
        contacts: bool = True,
        rules: bool = True,
        ai: bool = True,
        attachments: bool = True,
        persist: bool = True,
    ) -> JSONResponse:
        """Run the same read-only pipeline as /classify/preview, and (by
        default) write one Audit_Log row per message recording the proposed
        decision — classification, priority, confidence, rules triggered, a
        short rationale. Still zero Gmail writes: this is the paper trail for
        proposals, and the substrate the future Undo Last Run (Phase 12) will
        read. Pass `persist=false` to see the same run without recording it.
        """
        from app.ai import build_provider
        from app.ai.costs import CostTracker
        from app.audit import service as audit_service
        from app.classification import pipeline

        provider = build_provider() if ai else None
        tracker = CostTracker()

        try:
            results = pipeline.preview_recent(
                limit=limit,
                query=query,
                include_contacts=contacts,
                include_workbook=rules,
                use_ai=ai,
                read_attachments=attachments,
                provider=provider,
                tracker=tracker,
            )
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        run_id = None
        if persist:
            from app.sheets.repository import ControlWorkbook

            try:
                workbook = ControlWorkbook.connect()
            except NotConnectedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            run_id = audit_service.record_run(workbook, results)

        return JSONResponse(
            {
                "gmail_modified": False,
                "persisted": persist,
                "run_id": run_id,
                "count": len(results),
                "cost": tracker.summary(),
                "note": (
                    "Recorded a proposed decision for each message in your "
                    "control workbook's Audit_Log. Nothing in Gmail was changed."
                    if persist
                    else "Preview only — nothing was written to the Audit_Log."
                ),
            }
        )

    # ---------- Learning: suggestions + approval follow-through (Phase 9) ----------

    @app.post("/learning/suggest-vips", tags=["learning"])
    def learning_suggest_vips(
        limit: int = 25,
        query: str | None = None,
        persist: bool = True,
    ) -> JSONResponse:
        """Look at recent mail for the correspondence signals CLAUDE.md §8
        names — frequent senders, active threads, starred messages — and
        propose VIPs. Always a suggestion: nothing is protected until you
        approve it in the control workbook. Read-only against Gmail.
        """
        from app.classification import pipeline
        from app.learning import service as learning_service
        from app.sheets.repository import ControlWorkbook

        try:
            results = pipeline.preview_recent(
                limit=limit, query=query, use_ai=False, read_attachments=False
            )
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        suggested: list[str] = []
        if persist:
            try:
                workbook = ControlWorkbook.connect()
            except NotConnectedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            suggested = learning_service.suggest_vips_from_results(workbook, results)

        return JSONResponse(
            {
                "gmail_modified": False,
                "persisted": persist,
                "suggested": suggested,
                "note": (
                    "Suggested VIPs from correspondence patterns in your recent "
                    "mail. They protect nothing until you approve them in the "
                    "control workbook."
                    if persist
                    else "Preview only — nothing was written to the workbook."
                ),
            }
        )

    @app.post("/learning/promote-suggestions", tags=["learning"])
    def learning_promote_suggestions() -> JSONResponse:
        """Turn every Learned_Rule_Suggestions row you've already marked
        `approved` in the control workbook into an active Sender_Rules or
        Domain_Rules row. This is the one place a suggestion becomes a real
        rule, and it only acts on approvals you've already made by hand
        (CLAUDE.md §11) — it never touches Gmail.
        """
        from app.learning import service as learning_service
        from app.sheets.repository import ControlWorkbook

        try:
            workbook = ControlWorkbook.connect()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        promoted = learning_service.promote_approved_suggestions(workbook)
        return JSONResponse(
            {
                "gmail_modified": False,
                "promoted": promoted,
                "note": (
                    "Promoted every approved rule suggestion into an active "
                    "rule. Nothing in Gmail was changed."
                ),
            }
        )

    # ---------- 250-email acceptance run (Phase 10, CLAUDE.md §15) ----------

    @app.post("/acceptance/run", tags=["acceptance"])
    def acceptance_run(
        target: int = 250,
        use_ai: bool = True,
        read_attachments: bool = True,
        contacts: bool = True,
        rules: bool = True,
        persist: bool = True,
    ) -> JSONResponse:
        """Pull a deliberately mixed, stratified sample of recent mail (up to
        `target`, default 250 — CLAUDE.md §15) and classify it with the same
        read-only pipeline as everywhere else. Reports the launch-quality-gate
        number: how many protected/important emails were routed to Review —
        must be zero before Phase 11 (live Gmail writes) is ever enabled.
        Always zero Gmail modifications. `persist=false` previews without
        writing to the control workbook.
        """
        from datetime import datetime, timezone

        from app.acceptance import service as acceptance_service

        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        try:
            report, results = acceptance_service.run_acceptance_test(
                target_total=target,
                use_ai=use_ai,
                read_attachments=read_attachments,
                include_contacts=contacts,
                include_workbook=rules,
            )
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if persist:
            from app.sheets.repository import ControlWorkbook

            try:
                workbook = ControlWorkbook.connect()
            except NotConnectedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            acceptance_service.persist_report(workbook, report, results, started_at)

        return JSONResponse(
            {
                "gmail_modified": False,
                "persisted": persist,
                "dashboard_url": f"/dashboard/acceptance/{report.run_id}",
                "note": (
                    "Read-only sample. Review the false_reviews list (should be "
                    "empty) and the linked dashboard report yourself before "
                    "treating this as a pass — the count only catches what the "
                    "app already recognized as protected (CLAUDE.md §15)."
                ),
                **report.as_dict(),
            }
        )

    # ---------- Command Center dashboard (Phase 8) ----------

    def _redirect_to_login() -> RedirectResponse:
        return RedirectResponse("/dashboard/login", status_code=303)

    @app.get("/dashboard", response_class=HTMLResponse, tags=["dashboard"])
    def dashboard_home(request: Request, limit: int = 50) -> HTMLResponse:
        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()
        try:
            center = dashboard_service.build_command_center(limit=limit)
        except NotConnectedError:
            return HTMLResponse(dashboard_views.render_not_connected(user))
        return HTMLResponse(dashboard_views.render_command_center(center, user))

    @app.get(
        "/dashboard/list/{card_key}",
        response_class=HTMLResponse,
        tags=["dashboard"],
    )
    def dashboard_list(
        request: Request,
        card_key: str,
        limit: int = 50,
        notice: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()
        if card_key not in dashboard_service.CARD_KEYS:
            raise HTTPException(status_code=404, detail="Unknown Command Center list.")
        try:
            center = dashboard_service.build_command_center(limit=limit)
        except NotConnectedError:
            return HTMLResponse(dashboard_views.render_not_connected(user))
        return HTMLResponse(
            dashboard_views.render_list(center, card_key, user, notice=notice, error=error)
        )

    @app.get(
        "/dashboard/trash-confirm", response_class=HTMLResponse, tags=["dashboard"]
    )
    def dashboard_trash_confirm(
        request: Request,
        message_id: str = "",
        thread_id: str = "",
        sender_email: str = "",
        sender_name: str = "",
        subject: str = "",
        classification: str = "",
        reason: str = "",
    ) -> HTMLResponse:
        """The required stop before Trash happens (CLAUDE.md §5). A GET here
        changes nothing — only the form on this page, submitted to
        ``/dashboard/action/trash``, can actually move anything to Trash.
        """
        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()
        if not message_id:
            raise HTTPException(status_code=400, detail="Missing message_id.")
        return HTMLResponse(
            dashboard_views.render_trash_confirm(
                account=user,
                message_id=message_id,
                thread_id=thread_id,
                sender_email=sender_email,
                sender_name=sender_name,
                subject=subject,
                classification=classification,
                reason=reason,
            )
        )

    @app.post("/dashboard/action/{action}", tags=["dashboard"])
    def dashboard_action(
        action: str,
        request: Request,
        message_id: str = Form(""),
        thread_id: str = Form(""),
        sender_email: str = Form(""),
        sender_name: str = Form(""),
        subject: str = Form(""),
        classification: str = Form(""),
        reason: str = Form(""),
    ) -> RedirectResponse:
        """One of the seven Review-queue buttons (§13). Keep, Review Correct,
        Make Sender Rule, Make Domain Rule and Suggest VIP write to the
        control workbook only. Restore to Inbox and Trash (Phase 11) call
        real Gmail — both refuse cleanly if live writes aren't turned on;
        Trash is only ever reached from its own confirmation page below.
        """
        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()
        if action not in dashboard_actions.ACTION_LABELS:
            raise HTTPException(status_code=404, detail="Unknown Review-queue action.")

        from app.sheets.repository import ControlWorkbook

        try:
            workbook = ControlWorkbook.connect()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — degrade to a friendly banner
            log.warning("dashboard_action_workbook_unavailable", extra={"error": str(exc)})
            note = quote("Could not reach your control workbook — try again shortly.")
            return RedirectResponse(f"/dashboard/list/review?error={note}", status_code=303)

        outcome = dashboard_actions.perform(
            action,
            workbook,
            message_id=message_id,
            thread_id=thread_id,
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            classification=classification,
            reason=reason,
        )
        param = "notice" if outcome.ok else "error"
        return RedirectResponse(
            f"/dashboard/list/review?{param}={quote(outcome.message)}", status_code=303
        )

    @app.get("/dashboard/acceptance", response_class=HTMLResponse, tags=["dashboard"])
    def dashboard_acceptance_latest(request: Request) -> HTMLResponse:
        """The most recent acceptance run, if one has run in this process."""
        from app.acceptance import service as acceptance_service
        from app.acceptance import views as acceptance_views

        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()
        report = acceptance_service.latest_report()
        if report is None:
            return HTMLResponse(acceptance_views.render_no_runs(user))
        return HTMLResponse(acceptance_views.render_report(report, user))

    @app.get(
        "/dashboard/acceptance/{run_id}", response_class=HTMLResponse, tags=["dashboard"]
    )
    def dashboard_acceptance_run(request: Request, run_id: str) -> HTMLResponse:
        from app.acceptance import service as acceptance_service
        from app.acceptance import views as acceptance_views

        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()
        report = acceptance_service.get_report(run_id)
        if report is None:
            return HTMLResponse(acceptance_views.render_run_not_found(run_id, user), status_code=404)
        return HTMLResponse(acceptance_views.render_report(report, user))

    # ---------- Undo Last Run (Phase 12) ----------

    def _connect_workbook_or_error_redirect(fallback_path: str):
        from app.sheets.repository import ControlWorkbook

        try:
            return ControlWorkbook.connect(), None
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — degrade to a friendly banner
            log.warning("undo_workbook_unavailable", extra={"error": str(exc)})
            note = quote("Could not reach your control workbook — try again shortly.")
            return None, RedirectResponse(f"{fallback_path}?error={note}", status_code=303)

    @app.get("/dashboard/undo", response_class=HTMLResponse, tags=["dashboard"])
    def dashboard_undo_preview(request: Request) -> HTMLResponse:
        """The required stop before Undo happens (mirrors Trash's confirm
        page, CLAUDE.md §5). A GET here changes nothing.
        """
        from app.undo import service as undo_service
        from app.undo import views as undo_views

        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()

        workbook, error_redirect = _connect_workbook_or_error_redirect("/dashboard")
        if error_redirect is not None:
            return error_redirect

        preview = undo_service.preview_last_run(workbook)
        if preview is None:
            return HTMLResponse(undo_views.render_no_undo(user))
        return HTMLResponse(undo_views.render_preview(preview, user))

    @app.post("/dashboard/undo", response_class=HTMLResponse, tags=["dashboard"])
    def dashboard_undo_confirm(request: Request, run_id: str = Form(...)) -> HTMLResponse:
        from app.undo import service as undo_service
        from app.undo import views as undo_views

        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()

        workbook, error_redirect = _connect_workbook_or_error_redirect("/dashboard")
        if error_redirect is not None:
            return error_redirect

        result = undo_service.undo_run(workbook, run_id)
        return HTMLResponse(undo_views.render_result(result, user))

    @app.get("/undo/preview", tags=["undo"])
    def undo_preview_route() -> JSONResponse:
        """JSON mirror of the dashboard preview — read-only, changes nothing."""
        from app.undo import service as undo_service
        from app.sheets.repository import ControlWorkbook

        try:
            workbook = ControlWorkbook.connect()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        preview = undo_service.preview_last_run(workbook)
        if preview is None:
            return JSONResponse({"available": False})
        return JSONResponse(
            {
                "available": True,
                "run_id": preview.run_id,
                "mode": preview.mode,
                "completed_at": preview.completed_at,
                "message_count": preview.message_count,
                "messages": [
                    {
                        "id": m.message_id,
                        "subject": m.subject,
                        "action_taken": m.action_taken,
                        "labels_before": m.labels_before,
                        "labels_after": m.labels_after,
                    }
                    for m in preview.messages
                ],
            }
        )

    @app.post("/undo/run", tags=["undo"])
    def undo_run_route(run_id: str, confirm: bool = False) -> JSONResponse:
        """Reverses one run — only if `confirm=true`. Same gate as every
        other write path (CLAUDE.md §21: one rule, no exceptions).
        """
        from app.undo import service as undo_service
        from app.sheets.repository import ControlWorkbook

        try:
            workbook = ControlWorkbook.connect()
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        if not confirm:
            return JSONResponse(
                {
                    "status": "not_confirmed",
                    "message": "Pass confirm=true to actually undo this run.",
                }
            )

        result = undo_service.undo_run(workbook, run_id)
        return JSONResponse(
            {
                "run_id": result.run_id,
                "status": result.status,
                "message": result.message,
                "gate_reasons": list(result.gate_reasons),
                "restored_count": result.restored_count,
                "outcomes": [
                    {"id": o.message_id, "outcome": o.outcome, "detail": o.detail}
                    for o in result.outcomes
                ],
            }
        )

    # ---------- Near-real-time processing (Phase 13) ----------

    @app.on_event("startup")
    async def _start_realtime_poller() -> None:
        if settings.realtime_enabled:
            app.state.realtime_poller.start()
        else:
            log.info("realtime_poller_not_started", extra={"realtime_enabled": False})

    @app.on_event("shutdown")
    async def _stop_realtime_poller() -> None:
        await app.state.realtime_poller.stop()

    @app.get("/realtime/status", tags=["realtime"])
    def realtime_status() -> JSONResponse:
        return JSONResponse(
            {
                "enabled": settings.realtime_enabled,
                "poll_interval_seconds": settings.realtime_poll_interval_seconds,
                **app.state.realtime_poller.status.as_dict(),
            }
        )

    @app.post("/realtime/poll", tags=["realtime"])
    def realtime_poll(use_ai: bool = True) -> JSONResponse:
        """Run exactly one poll cycle right now, whether or not the
        background loop (REALTIME_ENABLED) is on. The same function the
        background loop calls on a timer — a manual way to see near-real-time
        processing work without waiting for the interval, or to catch up
        immediately after turning REALTIME_ENABLED on.
        """
        from app.scheduling import poller as realtime_poller_module

        try:
            report = realtime_poller_module.run_poll_cycle(use_ai=use_ai)
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "note": (
                    "First poll for this workbook — recorded the current "
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

    # ---------- Daily digest (Phase 14) ----------

    @app.on_event("startup")
    async def _start_digest_scheduler() -> None:
        if settings.digest_scheduler_enabled:
            app.state.digest_scheduler.start()
        else:
            log.info(
                "digest_scheduler_not_started",
                extra={"digest_scheduler_enabled": False},
            )

    @app.on_event("shutdown")
    async def _stop_digest_scheduler() -> None:
        await app.state.digest_scheduler.stop()

    @app.get("/digest/status", tags=["digest"])
    def digest_status() -> JSONResponse:
        return JSONResponse(
            {
                "enabled": settings.digest_scheduler_enabled,
                "digest_hour": settings.digest_hour,
                "digest_timezone": settings.digest_timezone,
                **app.state.digest_scheduler.status.as_dict(),
            }
        )

    @app.post("/digest/scan", tags=["digest"])
    def digest_scan(
        limit: int = 50,
        query: str | None = None,
        persist: bool = True,
    ) -> JSONResponse:
        """Build today's digest right now, whether or not the scheduler has
        fired yet, and (by default) record a summary row in Digest_Log.

        Read-only against Gmail — persisting writes only to the control
        workbook, never to Gmail. Pass ``persist=false`` to preview without
        recording it.
        """
        from app.digest import persistence as digest_persistence
        from app.digest import service as digest_service
        from app.sheets.repository import ControlWorkbook

        # digest_timezone/digest_hour are workbook-editable (CLAUDE.md §12);
        # connect once and reuse for both the build and the persist below.
        # A workbook the user hasn't set up yet just means "use env config" —
        # not a reason to refuse the scan.
        try:
            workbook = ControlWorkbook.connect()
        except Exception as exc:  # noqa: BLE001
            log.info("digest_workbook_unavailable", extra={"error": str(exc)})
            workbook = None

        try:
            report = digest_service.build_digest(limit=limit, query=query, workbook=workbook)
        except NotConnectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        written: dict[str, object] | None = None
        if persist:
            try:
                if workbook is None:
                    workbook = ControlWorkbook.connect()
                written = digest_persistence.persist(workbook, report)
            except NotConnectedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

        return JSONResponse(
            {
                "gmail_modified": False,
                "persisted": persist,
                "note": (
                    "Built today's digest. Your Gmail was not changed. A "
                    "summary was recorded in Digest_Log."
                    if persist
                    else "Preview only — nothing was written to the workbook."
                ),
                "written": written,
                "digest": digest_service.report_as_dict(report),
            }
        )

    @app.get("/dashboard/digest", response_class=HTMLResponse, tags=["dashboard"])
    def dashboard_digest(request: Request, limit: int = 50) -> HTMLResponse:
        user = dashboard_auth.current_user(request)
        if user is None:
            return _redirect_to_login()
        from app.digest import service as digest_service
        from app.digest import views as digest_views
        from app.sheets.repository import ControlWorkbook

        # digest_timezone/digest_hour are workbook-editable (CLAUDE.md §12);
        # a missing/unreachable workbook just means "use env config" for
        # those two settings, same degrade-to-empty rule the Command Center
        # already applies to VIP suggestions — not a reason to fail the page.
        try:
            workbook = ControlWorkbook.connect()
        except Exception as exc:  # noqa: BLE001
            log.info("digest_workbook_unavailable", extra={"error": str(exc)})
            workbook = None

        try:
            report = digest_service.build_digest(limit=limit, workbook=workbook)
        except NotConnectedError:
            return HTMLResponse(digest_views.render_not_connected(user))
        return HTMLResponse(digest_views.render_digest(report, user))

    # ---------- 12-month historical cleanup (Phase 15) ----------

    @app.on_event("shutdown")
    async def _cancel_historical_runner() -> None:
        # Best-effort only: the sweep is cooperative-cancel between pages
        # (see app/historical/runner.py), not something a shutdown hook can
        # forcibly stop mid-page. This just asks it to wind down.
        app.state.historical_runner.request_cancel()

    @app.get("/historical/status", tags=["historical"])
    def historical_status() -> JSONResponse:
        return JSONResponse(app.state.historical_runner.status.as_dict())

    @app.post("/historical/start", tags=["historical"])
    async def historical_start(
        months: int = 12,
        confirm: bool = False,
        use_ai: bool = False,
        read_attachments: bool = False,
        batch_size: int = 100,
        max_messages: int | None = None,
    ) -> JSONResponse:
        """Start a 12-month sweep as a background task and return
        immediately — a large mailbox can take a while, so this never blocks
        the request waiting for it to finish. Poll ``GET /historical/status``
        for progress.

        ``confirm=false`` (the default) always previews, regardless of
        settings — the first run of this should be a dry run. Only one sweep
        may run at a time; starting a second while one is active is refused.

        Declared ``async`` (unlike most routes in this file) because
        ``HistoricalRunner.start()`` calls ``asyncio.create_task`` — that
        needs to run on the event loop itself, not in FastAPI's sync-route
        threadpool, which has no running loop of its own to create a task on.
        """
        if not gmail_tokens.token_exists():
            raise HTTPException(status_code=409, detail="Gmail isn't connected yet.")

        started = app.state.historical_runner.start(
            months=months,
            confirm=confirm,
            use_ai=use_ai,
            read_attachments=read_attachments,
            batch_size=batch_size,
            max_messages=max_messages,
        )
        if not started:
            raise HTTPException(
                status_code=409, detail="A historical cleanup run is already in progress."
            )
        return JSONResponse({"started": True, **app.state.historical_runner.status.as_dict()})

    @app.post("/historical/cancel", tags=["historical"])
    def historical_cancel() -> JSONResponse:
        cancelled = app.state.historical_runner.request_cancel()
        return JSONResponse(
            {"cancel_requested": cancelled, **app.state.historical_runner.status.as_dict()}
        )

    @app.get("/dashboard/login", response_class=HTMLResponse, tags=["dashboard"])
    def dashboard_login(request: Request) -> HTMLResponse:
        # Already signed in? Go straight to the Command Center.
        if dashboard_auth.current_user(request) is not None:
            return RedirectResponse("/dashboard", status_code=303)
        return HTMLResponse(dashboard_views.render_login())

    @app.get("/dashboard/auth/start", tags=["dashboard"])
    def dashboard_auth_start():
        try:
            url, _state = dashboard_auth.build_login_url()
        except RuntimeError as exc:
            return HTMLResponse(
                dashboard_views.render_login(str(exc)), status_code=500
            )
        return RedirectResponse(url, status_code=307)

    @app.get("/dashboard/auth/callback", response_class=HTMLResponse, tags=["dashboard"])
    def dashboard_auth_callback(request: Request) -> HTMLResponse:
        params = request.query_params
        if params.get("error"):
            return HTMLResponse(
                dashboard_views.render_login(
                    f"Google reported an error: {params.get('error')}"
                ),
                status_code=400,
            )

        code, state = params.get("code"), params.get("state")
        if not code or not state:
            return HTMLResponse(
                dashboard_views.render_login("Missing sign-in code or state."),
                status_code=400,
            )

        try:
            email = dashboard_auth.complete_login(code=code, state=state)
        except PermissionError as exc:
            return HTMLResponse(
                dashboard_views.render_login(str(exc)), status_code=400
            )
        except Exception as exc:  # noqa: BLE001 — surface a friendly message
            log.warning("dashboard_login_failed", extra={"error": str(exc)})
            return HTMLResponse(
                dashboard_views.render_login("Sign-in could not be completed."),
                status_code=400,
            )

        if not dashboard_auth.is_authorized(email):
            log.warning("dashboard_login_unauthorized", extra={"email": email})
            return HTMLResponse(
                dashboard_views.render_unauthorized(email), status_code=403
            )

        response = RedirectResponse("/dashboard", status_code=303)
        _set_dashboard_session(response, email)
        log.info("dashboard_login_ok", extra={"email": email})
        return response

    @app.post("/dashboard/logout", tags=["dashboard"])
    def dashboard_logout() -> RedirectResponse:
        response = RedirectResponse("/dashboard/login", status_code=303)
        response.delete_cookie(dashboard_auth.SESSION_COOKIE)
        return response

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


# ---------- HTML helpers (tiny, no template engine yet) ----------

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
        "<p><strong>Phase 2 — Gmail read-only + Sheets control workbook.</strong> "
        "Not connected yet.</p>"
        "<p>The app will ask Google for these permissions (and only these):</p>"
        f"<ul class=\"scopes\">{scope_html}</ul>"
        "<p><a class=\"btn\" href=\"/oauth/start\">Connect Gmail</a></p>"
        "<p style=\"margin-top:2rem;font-size:.9em;color:#555;\">"
        "You'll be sent to Google's consent screen. This app never asks for "
        "modify, send, or delete permissions."
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
            "The Sheets control workbook can't be set up until you reconnect. "
            "<a href=\"/oauth/start\">Reconnect now</a>.</div>"
        )

    workbook_section = (
        "<h2>Control workbook</h2>"
        "<p>Your settings, rules, and VIP list live in a Google Sheet that the "
        "app creates for you. You never have to build the tabs yourself.</p>"
        "<form method=\"post\" action=\"/sheets/init\">"
        "<button class=\"btn\" type=\"submit\">Create / update my control workbook</button>"
        "</form>"
        if not missing_scopes
        else ""
    )

    return (
        "<!doctype html><html><head><title>Gmail Intelligence Agent</title>"
        f"<style>{_BASE_CSS}</style></head><body>"
        "<h1>Gmail Intelligence Agent</h1>"
        f"<p class=\"ok\">Connected as <strong>{account_email}</strong>.</p>"
        f"{banner}"
        f"{reveal_section}"
        "<p>Gmail access now includes labels, archive, restore, and a "
        "user-confirmed Trash (Phase 11) — never sending email, and never a "
        "permanent delete. Every other write stays off until you deliberately "
        "set DRY_RUN=false and GMAIL_PROCESSING_ENABLED=true, and only after "
        "a passed 250-email acceptance run.</p>"
        "<p><a class=\"btn\" href=\"/gmail/preview\">Preview last 10 messages (metadata only)</a></p>"
        f"{workbook_section}"
        "<h2>Command Center</h2>"
        "<p>Your dashboard: P1/P2, Action Required, Waiting for Reply, Due Soon, "
        "Overdue, the AI Review queue, and more — each a card you can open. "
        "<strong>Nothing in Gmail is changed.</strong></p>"
        "<p><a class=\"btn\" href=\"/dashboard\">Open the Command Center</a></p>"
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
        "Workbook: <a href=\"/sheets/status\"><code>/sheets/status</code></a>"
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
