# Technical Status

Running technical log of what has been built and what is next.

## Current phase

**Phase 16 — Render deployment** (complete). Full suite: **918 passing**.

`app/__init__.py::CURRENT_PHASE` is the single source of truth for the phase number reported by `/health`.

---

## Phase 16 deliverables

### The problem this phase actually had to solve

CLAUDE.md's Phase 16 row reads simply: "GitHub → Render config, environment variables, health checks, production startup, deployment docs." The config/env-var/health-check parts were mostly already in place from Phase 0's placeholder `render.yaml`. The real, non-trivial problem — flagged since Phase 1 in `app/gmail/tokens.py`'s own docstring ("Not durable on Render") — is that Render's filesystem is ephemeral: every redeploy rebuilds the container from the git repo, and any file the running app wrote to local disk (specifically, the encrypted OAuth token at `oauth_tokens/token.json.enc`) does not survive that. Left unfixed, every redeploy would silently break the Gmail connection until someone noticed and reconnected by hand.

### Plan choice: free tier, not Starter — decided with the user, not assumed

Render's paid persistent-disk feature (~$0.25/GB/month, 1GB minimum) would have solved the durability problem directly, but requires a paid plan (disks aren't available on the free tier at all) — Starter is $7/month. Asked directly, given this is a single-account personal project, the user chose to stay on Render's **free** plan and solve durability a different way rather than pay for either the disk or the always-on Starter tier. Documented here rather than assumed, matching this codebase's precedent for product-shape decisions CLAUDE.md itself leaves open (Phase 13's polling-vs-push, Phase 14's digest-delivery-mechanism).

### Rejected approach: storing the token in the Sheets control workbook

The first alternative considered (and initially proposed to the user before the code was actually checked) was storing the encrypted token inside the Sheets control workbook instead of a local file — Sheets already survives a redeploy, unlike local disk. This turns out to be structurally impossible, not just inconvenient: `app/google_api.py::build_service` — the single function every Google API client in this app goes through, Sheets and Drive included — calls `app.gmail.tokens.load_stored_token_or_raise()` to obtain credentials *before* it can build a Sheets client at all. Making `load_token()` itself read from the Sheets workbook would mean building the Sheets client requires a loaded token, and loading the token requires a built Sheets client — an unbreakable circular dependency on every cold boot, when neither exists yet. Caught by reading `app/sheets/client.py` and `app/google_api.py` before writing any code, not discovered by trial and error, and corrected with the user before implementation started.

### The actual fix: reseed the local file from a durable Render env var

Unlike the local disk, Render's own environment-variable store *does* survive a redeploy — it's how secrets like `GOOGLE_CLIENT_SECRET` already worked before this phase, with no special handling. The refresh token half of an OAuth credential is the one part that's genuinely durable in nature (issued once at consent, essentially never changes on its own — only the short-lived access token needs frequent renewal, which `google-auth` already handles automatically from the refresh token). So instead of moving storage anywhere new, `app/gmail/tokens.py::load_token()` gained a fallback: when the local file is missing *or* fails to decrypt, `_seed_from_env()` checks the new `google_oauth_seed_refresh_token` setting and, if set, rebuilds a `StoredToken` from it (`access_token`/`expiry_iso` left unset — `google-auth` refreshes them from the refresh token on the next API call) and persists it locally via the existing `save_token()`, exactly as if a normal load had succeeded. No new storage backend, no new dependency, no circular bootstrap problem — the seed is read directly from `Settings`, which never needs a Google API call to access.

### Where the seed value comes from: shown once, not auto-uploaded

Nothing in this app talks to Render's own API (that would need a separate Render API key as a new secret, and — since Render redeploys on every env var change — risks a redeploy loop if updated automatically on every token refresh). Instead, `/oauth/callback`'s success page (`app/main.py::_render_connected`, new `reveal_refresh_token` parameter) shows the refresh token in a copy-paste box exactly once, right after a fresh consent, with instructions to paste it into Render's dashboard as `GOOGLE_OAUTH_SEED_REFRESH_TOKEN`. It's suppressed when the freshly-obtained token already equals the configured seed (nothing new to copy). This keeps the whole mechanism to one manual, deliberate step per Gmail (re)connection — consistent with how every other Render secret in this app already gets set.

### The one accepted rough edge: disconnect doesn't survive a seeded restart

If `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` is still set in Render's environment after a user disconnects Gmail from the dashboard (`POST /oauth/disconnect`, `gmail_tokens.clear_token()`), the *next* restart reseeds the connection right back from the still-present env var — there's no durable place to record "the user explicitly disconnected" that itself survives a restart without reintroducing the same ephemeral-storage problem this phase exists to solve. Rather than build a second persistence mechanism to close this narrow edge case, `/oauth/disconnect` now returns a `note` field explaining it directly whenever a seed is configured, and the plain-English doc calls it out under "What could go wrong." Judged a reasonable trade-off for a single-user personal project rather than a bug to engineer around.

### Production fail-fast for a placeholder `SESSION_SECRET`

`SESSION_SECRET` does double duty — it signs dashboard session cookies *and* derives the Fernet key that encrypts the stored OAuth token (`app/gmail/tokens.py::_fernet`). Before this phase, only `_fernet()` itself refused a placeholder value, and only at the moment a token was actually saved/loaded — meaning a production deploy with the default `change-me-to-a-long-random-string` could boot cleanly and serve real dashboard sessions signed with a secret published in this very file. `create_app()` now checks `settings.is_production and settings.session_secret == _PLACEHOLDER_SESSION_SECRET` up front and raises `RuntimeError` before the app object is even constructed — a failed Render deploy with a clear log message, instead of a silently-insecure running service. Local development (`APP_ENV=development`, the default) is unaffected; the placeholder still works out of the box there.

### `render.yaml`, finalized

- `plan: free` (was a Phase-0 placeholder `starter`).
- `buildCommand: pip install -e ".[anthropic,openai]"` — both AI provider extras installed unconditionally, so switching `AI_PROVIDER` between the two is purely an env var change, never a rebuild. (The Phase-0 placeholder's `pip install -e .` alone would have left whichever provider was selected without its SDK installed.)
- Two new `sync: false` secrets: `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` and `GOOGLE_OAUTH_SEED_ACCOUNT_EMAIL`, both blank until set by hand after the first live Gmail connection.
- Comments clarify that `GOOGLE_OAUTH_REDIRECT_URI`/`DASHBOARD_LOGIN_REDIRECT_URI` can only be filled in correctly *after* the first deploy, once Render has assigned the service's real `onrender.com` URL — not something `render.yaml` itself can know in advance.
- `healthCheckPath: /health` and `autoDeploy: true` carried over unchanged from the Phase 0 placeholder.

### New Settings fields (`app/config/settings.py`)

`google_oauth_seed_refresh_token: str | None = None` and `google_oauth_seed_account_email: str | None = None` — both optional, both `None` by default, so local development (where the local token file is durable enough on its own) is entirely unaffected.

### Tests

**918 passing** (was 907). New: `tests/unit/test_tokens.py` gained 4 (seed-when-file-missing rebuilds and persists locally; no-seed-configured still returns `None`; an existing local file always wins over a static seed, never clobbered; an undecryptable file falls back to the seed rather than `None`). New `tests/unit/test_production_safety.py` (3 — production + placeholder refuses to boot, production + a real secret boots fine, development + placeholder still boots unaffected). `tests/unit/test_routes_oauth.py` gained 4 (the refresh token is revealed once on a successful callback with instructions naming the exact env var; the reveal is suppressed when the seed already matches; `/oauth/disconnect` carries no note with no seed configured; carries a warning note naming the env var when one is).

### Constraints honoured

- **No Gmail behavior change, anywhere (§5, §21).** This phase touches deployment plumbing and one storage-durability mechanism; no classification, priority, routing, or write-gate logic changed. Every existing Phase 1–15 test still passes unmodified.
- **No secrets committed (§16).** `.env.example` documents the two new variables with placeholder-empty values only; `render.yaml` lists them as `sync: false`, matching every other secret already there.
- **Simple, understandable architecture over clever (§21).** The rejected Sheets-token-storage idea was more "elegant" in reusing existing infrastructure but structurally broken; the shipped fix reuses a mechanism (Render env vars) that already demonstrably works for every other secret in this app.
- **A real product-shape decision, surfaced to the user, not assumed (§18's spirit, extended from Phase 13/14 precedent).** Free-vs-Starter and the token-durability approach were both decided with the user, including a direct correction after the Sheets idea turned out to be infeasible.

### What Phase 16 explicitly does NOT do

- **No Render API integration.** The app never calls Render's own API; the refresh-token seed is a one-time manual copy-paste, not an automated sync.
- **No keep-awake workaround.** The free tier's 15-minute idle spin-down is left as-is; an external ping service was considered and deliberately not built.
- **No automatic disconnect-cleanup of the seed variable.** See "the one accepted rough edge" above — documented, not engineered around.
- **No change to any classification, safety, or write-gate rule.** Every guarantee from Phases 1–15 carries forward unchanged.

---

## Phase 15 deliverables

### A background task, not a synchronous request — the key architectural choice this phase makes

Every prior batch-style endpoint in this app (`/acceptance/run`, `/gmail/apply`, `/intelligence/scan`, `/followup/scan`) runs synchronously inside one HTTP request, tolerable because each is explicitly bounded (250 messages at most). A 12-month sweep has no such bound — a real mailbox could hold many thousands of messages, and blocking an HTTP request for the minutes (or longer) that could take is impractical (client timeouts, Render's own request limits). So this phase introduces the first *unbounded*, *background-task* processing pattern: `POST /historical/start` returns immediately with a run id; the actual sweep runs via `asyncio.create_task` + `asyncio.to_thread` (the same mechanism `RealTimePoller`/`DigestScheduler` already use for their own loops), and `GET /historical/status` is polled for progress. Unlike those two, `HistoricalRunner` runs **once** per `start()` call rather than looping on a timer — CLAUDE.md §13's "run separately from real-time processing" is satisfied structurally: nothing else in the app ever calls `HistoricalRunner.start()` except the route itself.

### New package (`app/historical/`)

- `models.py` — `HistoricalRunStatus`, a single mutable progress object (state machine: `idle → running → completed | cancelled | failed | not_connected`), counters (`pages_processed`, `messages_seen/processed/changed`, `would_review_count`, `protected_count`, `errors`), and gate info. Only one run is ever active, so one status object is enough — the same shape `RealTimeStatus`/`DigestStatus` already use.
- `service.py` — `twelve_months_ago`/`historical_query` (real calendar-month subtraction via `calendar.monthrange`, stdlib only — CLAUDE.md §3's "never hard-code date arithmetic a library would get wrong" rule extended from holiday math to this); `run_historical_cleanup`, the one big worker function: connects the workbook, checks the write gate, builds classification context once, then pages through `GmailReadClient.list_message_ids` (new — see below), fetching and classifying each page via the existing `pipeline.classify_raw_messages`, applying via the existing `gmail_apply.plan_change`/`apply_to_message` when confirmed and the gate is open, and batching `Audit_Log` writes per page. Never raises — every failure path (not connected, a safety-invariant violation, anything else) is caught and reflected in `status` rather than propagating.
- `runner.py` — `HistoricalRunner`, the background-task controller. `start(**kwargs)` refuses (returns `False`) if a run is already active rather than queuing a second one; `request_cancel()` flips a cooperative flag the worker checks between pages (not a forceful stop — a real OS thread mid-`get_message` can't be interrupted, only asked to stop at its next natural checkpoint).

### `GmailReadClient.list_message_ids` — the missing piece for pagination

`list_recent_messages` (used by every earlier read-only phase) fetches full message bodies for *all* results of a single `messages.list` call with no pagination — fine for a bounded preview, unusable for a year of mail. The new method returns just the raw listing page (`{id, threadId}` stubs, `nextPageToken`, `resultSizeEstimate`), so the historical service can walk pages one at a time and only fetch full message content for the page it's actually processing. Still strictly read-only — covered by the existing `test_client_has_no_write_methods` structural check.

### Cost/storage discipline: a preview writes metrics, not thousands of rows

`apply_recent` (Phase 11) already writes an `Audit_Log` row only when a message actually changes — never one per unchanged message. This phase leans on that same rule at a much larger scale: a *preview* run (`confirm=false`, the default) writes **zero** per-message `Audit_Log` rows, only the counters in `HistoricalRunStatus` (and, at the end, one `System_Runs` summary row). CLAUDE.md §14 asks this phase to "produce metrics," not to write a "nothing changed" row for every one of possibly thousands of messages into a spreadsheet with its own API rate limits (the exact quota problem Phase 11's own bug-fix notes describe hitting at just 250 rows). A *confirmed* run writes a real `Audit_Log` row only for messages that actually changed, exactly mirroring `apply_recent`.

### Safety-invariant violations abort the whole run, not just one message

Every other per-message failure inside the sweep (a fetch that exhausted its retries, an unexpected error applying one message) is caught, logged, counted in `status.errors`, and the sweep continues — Phase 13's "log failures, keep going" contract, applied here too. The one deliberate exception: `AssertionError` from `engine._assert_safety_invariants()` (a protected email nearly routed to Review) is **not** caught inline — it propagates up to the run's own outer handler, which sets `status.state = "failed"` and stops the entire sweep. This is Phase 10's own philosophy ("a crash in a dry run beats a hidden email") extended to a much larger, potentially-live run: a safety-invariant violation indicates a bug in the classifier itself, not a one-off data problem, and is likely to recur on other messages in the same sweep — better to stop everything than to keep silently mis-classifying at scale.

### Keep run ids, keep Undo — for free, by construction

A confirmed sweep's changes share one `run_id` (via `audit_service.new_run_id()`) across every page, and one `System_Runs` row (`mode="historical"`) is written once, when the run ends (completed, cancelled, or failed) — not incrementally per page, matching every other `System_Runs` writer in this app (`persist_report`, `apply_recent`) which also write their summary row once, after the fact. Because Phase 12's `undo.service` already works generically over any `run_id`'s `Audit_Log` rows plus a `System_Runs.undo_available` flag, **no changes to Phase 12 were needed at all** — Undo Last Run reverses a historical sweep exactly like a manual batch apply, out of the box.

### Routes (`app/main.py`)

- `POST /historical/start?months=12&confirm=false&use_ai=false&read_attachments=false&batch_size=100&max_messages=` — starts a sweep as a background task; 409 if Gmail isn't connected, 409 if a sweep is already active. **Declared `async def`**, unlike almost every other route in this file — see "A bug found and fixed" below for why that matters.
- `GET /historical/status` — the live `HistoricalRunStatus`, no sign-in required, matching `/realtime/status`/`/digest/status`.
- `POST /historical/cancel` — sets the cooperative cancel flag; reports `cancel_requested: false` when nothing is running rather than an error.
- `@app.on_event("shutdown")` asks any active run to cancel (best-effort — a page already mid-flight isn't forcibly interrupted).

### A bug found and fixed during Phase 15

The first version of `POST /historical/start` was a plain `def` route (matching every other route in this file). `HistoricalRunner.start()` calls `asyncio.create_task(...)`, which requires a currently-running event loop *in the calling thread*. FastAPI dispatches a synchronous `def` route through a worker thread (`run_in_threadpool`) that has no event loop of its own — so the very first real test of this route failed immediately with `RuntimeError: no running event loop`. Fixed by declaring the route `async def`, which FastAPI instead runs directly on the event loop. Caught only because the route was tested for real (`tests/unit/test_routes_historical.py`) rather than trusting the underlying `HistoricalRunner`/`run_historical_cleanup` unit tests alone.

### A test-harness quirk worth knowing about (not a bug, but nearly looked like one)

Chasing that same fix down, a *second* symptom appeared: a test starting one run and immediately trying to start a second (expecting a 409, "already running") instead got a 200 both times. Investigation (see the debug session in this phase's own history) showed the cause is specific to Starlette's synchronous `TestClient`: each `.post()` call runs the ASGI app through its own short-lived event loop and — like `asyncio.run()` generally — cancels and *awaits* any task still pending when that call's own work is done, before control returns to the test. A background task created during request 1 is therefore always fully resolved (completed or cancelled) by the time request 1's response comes back, so request 2 never actually observes "still running." This is a property of that specific test transport, not of the real server: uvicorn keeps one persistent event loop for the whole process, which is what actually lets a task outlive the request that created it. Confirmed directly with an `httpx.AsyncClient` + `ASGITransport` smoke test against a real persistent loop, which showed the intended behavior exactly (start → 200 "running", a concurrent second start → 409, status/cancel both correct). The "only one run at a time" guarantee is therefore tested at the `HistoricalRunner` level (`test_historical_runner.py`, using real `async def` test functions that keep one loop alive across calls) rather than through `TestClient`, and `test_routes_historical.py` says so explicitly rather than silently dropping the scenario.

### Tests

**907 passing** (was 883). New: `test_historical_service.py` (21 — date math incl. a leap-day clamp, pagination across pages, preview-writes-no-audit-rows, confirmed-run-writes-only-changed-rows, per-message fetch-failure isolation, `max_messages` stopping mid-page, cooperative cancellation between pages, safety-invariant abort, not-connected handling), `test_historical_runner.py` (6 — single-run guard, cancel-flag plumbing, a failed worker recorded not raised, status resets on a fresh start), `test_routes_historical.py` (4 — status shape, the connected-account 409, parameter pass-through, cancel-when-idle), plus 2 new `GmailReadClient` tests for `list_message_ids`.

### Constraints honoured

- **The same write gate, no exceptions (§15, §21).** `run_historical_cleanup` calls the exact same `gmail_apply.check_write_gate` every other write path calls; `confirm=false` always previews regardless of settings.
- **No automatic Trash, ever (§5).** The apply path reuses `gmail_apply.apply_to_message` unchanged — there is still no code path from this sweep to `trash_message()`.
- **Run separately from real-time processing (§13).** Nothing but the route itself ever starts a historical run; it shares no loop or trigger with `RealTimePoller` or `DigestScheduler`.
- **Batch safely, respect API limits (§14).** Paginated (100 ids/page by default), each page's fetches retried via the existing `call_with_retry` (Phase 13), with a short pause between pages.
- **Keep run ids, keep Undo (§14).** One `run_id` per sweep; `System_Runs`/`Audit_Log` shaped identically to every other real write, so Phase 12's Undo needed zero changes.
- **Produce metrics (§14).** `HistoricalRunStatus` reports counts by outcome; a preview never needs a live mailbox's whole Audit_Log bloated just to answer "what would this do."
- **Err toward preserving important email (§21).** A safety-invariant violation stops the entire sweep rather than continuing past a classifier bug.

### What Phase 15 explicitly does NOT do

- **No scheduling.** Always a deliberate `POST /historical/start`; nothing triggers one automatically.
- **No durable job queue / resume-after-restart.** A sweep is one process's background task; a server restart loses its progress (though not its already-applied changes) and a fresh sweep must be started manually. Idempotency (an already-correct message produces no API call) means re-running is always safe, just not automatically resumed.
- **No dashboard UI.** JSON routes only in this phase, matching how CLAUDE.md §14's own Phase 15 row doesn't name a UI requirement (unlike Phase 10's acceptance run, which CLAUDE.md §15 explicitly calls a "review dashboard").

---

## Phase 14 deliverables

### Product decision: dashboard page now, real email later

CLAUDE.md describes "a midnight America/New_York daily digest" but never pins down the delivery mechanism, and README's own "Next up" line had informally called it "an email." Asked directly (the same kind of product-decision checkpoint Phase 13's push-vs-poll call used), the user chose **both, in that order**: a page inside the app now, and real `gmail.send` email delivery as an explicit, separate follow-up once the page version has been used. This phase builds only the page — nothing here sends mail, and no new OAuth scope was requested. Documented in `docs/plain-english/PHASE_14_DAILY_DIGEST.md` under "A decision made before building this."

### The digest is a reordering of Phase 8's own data, not a second analysis

`app/digest/service.py::build_digest` calls `dashboard.service.build_command_center` — the exact same read-only pass the Command Center itself runs — and keeps only `DIGEST_SECTION_KEYS = ("p1", "p2", "action", "overdue", "waiting", "due_soon", "review")`, CLAUDE.md §13's digest order (notably *not* the dashboard's own `CARD_KEYS` order — Overdue moves ahead of Waiting/Due Soon, and VIP Suggestions + Subscription Review are dropped entirely, since neither is part of the digest spec). `dashboard.service.Row`/`Card` are reused as-is rather than duplicated — `DigestSection` is a thin wrapper (title/blurb/rows) around what `CommandCenter.card()`/`.rows()` already return. Nothing in `app/digest/` reads Gmail directly; the one read-only pass stays in `dashboard.service`.

### New package (`app/digest/`)

- `models.py` — `DigestSection`, `DigestReport` (frozen-shaped dataclasses; `counts()`/`total`/`is_empty` convenience methods).
- `service.py` — `digest_timezone(workbook)`/`digest_hour(workbook)` (workbook `Settings` tab first, env `Settings` as the fallback — mirroring `ai.factory`'s own workbook-first pattern for `ai_provider`/`anthropic_model`); `build_digest(...)` (the reorder described above); `report_as_dict(...)` (JSON shape for the scan route); `generate_if_due(workbook)` + `DigestCheckOutcome` — the clock-aware check the scheduler calls, deliberately kept separate from `build_digest` itself: a page load should always show a *fresh* digest (the same "recompute, never read back a stale copy" rule every other Command Center screen follows), but the *scheduled* midnight digest must only ever fire once per calendar day. `generate_if_due` enforces the "once per day" property by checking `Digest_Log` for an existing row before doing any work, not by tracking a separate cursor.
- `persistence.py` — `persist(workbook, report)`, mapping a `DigestReport` to one `DigestRepository.record(...)` call. Kept as its own module for the same reason `app/intelligence/persistence.py` is: the builder (`service.py`) stays pure and Sheets-free; only this module touches the workbook.
- `views.py` — server-rendered HTML for `GET /dashboard/digest`, matching `app/dashboard/views.py`'s own no-JS, escape-everything approach (a second, independent choke point for the same reason that one is — every sender/subject/summary shown here originates in untrusted email). Deliberately has no action buttons: acting on a message still happens from the Command Center's own Review list.
- `scheduler.py` — `DigestScheduler`, a background `asyncio` loop structurally identical to Phase 13's `RealTimePoller`: `start()`/`stop()` own one task, each tick runs `generate_if_due` inside `asyncio.to_thread` (Sheets/Gmail calls are synchronous), and no exception ever escapes the loop — a bad check is recorded in `DigestStatus` and the loop ticks again next interval. `DIGEST_CHECK_INTERVAL_SECONDS = 300` — cheap to check often, since almost every check is just a timezone-aware clock comparison with no Gmail/Sheets call at all unless it's actually time to build a digest.

### New workbook tab: `Digest_Log`

Appended to `WORKBOOK_TABS` (schema is additive-only, per `app/sheets/schema.py`'s own rule). One row per calendar date — `digest_id`, `digest_date`, `generated_at`, `timezone`, `account`, and one `*_count` column per digest section plus `total_count`. Deliberately a summary record, not a snapshot of the digest's actual content: like `System_Runs`, it says a run happened and what it found; `Audit_Log` already owns per-message history, and duplicating that here would just be a second source of truth for the same facts. `DigestRepository` (`app/sheets/repository.py`) is keyed on `digest_date` via the existing `_KeyedTable` helper, so re-generating the same day's digest (a manual `/digest/scan` call, or the scheduler recovering after a restart) updates the one row instead of piling up duplicates — the same idempotency guarantee `DeadlinesRepository`/`SubscriptionsRepository`/`TripsRepository` already give. `for_date`/`latest`/`all` round out the read side (not needed by any keyed repository until now, since none of the earlier ones needed a "does today's already exist?" check).

### `digest_scheduler_enabled` defaults to `True` — a deliberate departure from Phase 13's default-off pattern

`REALTIME_ENABLED` defaults `false` because that loop can lead to real Gmail writes once the write gate is open, and polls Gmail's history API every `REALTIME_POLL_INTERVAL_SECONDS` (120s by default) around the clock. `DigestScheduler` carries neither risk: it never writes to Gmail (there is no code path from `generate_if_due` to any Gmail-modifying call), never spends AI budget (`build_digest` → `build_command_center` runs with `use_ai=False`, matching the dashboard's own AI-free-by-default policy), and its own 5-minute check-the-clock tick makes no external call at all unless it's genuinely time to build a digest. Since CLAUDE.md frames the digest as a core shipped V1 feature (§19: "midnight NY digest works") rather than an opt-in extra like real-time processing was, and the risk profile is benign, it defaults on. Documented here and in the plain-English doc rather than left as a silent behavior difference from Phase 13's precedent.

### `digest_timezone`/`digest_hour` become genuinely workbook-editable

Both Settings keys existed since Phase 2 seeded the workbook but nothing read them until now. `service.digest_timezone`/`digest_hour` check `workbook.settings.get(...)`/`get_int(...)` first, falling back to the env `Settings` singleton (what a fresh workbook is seeded with anyway) when no workbook is available or the stored value is missing/invalid — the same "workbook first, env as fallback" shape `ai.factory` already established for `ai_provider`/model names. Both `/dashboard/digest` and `POST /digest/scan` in `app/main.py` attempt `ControlWorkbook.connect()` up front and degrade to `workbook=None` on *any* failure (not just `NotConnectedError` — a broad catch, matching `dashboard.service._load_vip_suggestions`'s own precedent for "a workbook that's missing or unreachable just means degrade, not fail the page"), so a user who hasn't set up Sheets yet still gets a working digest built from `.env` defaults.

### Routes (`app/main.py`)

- `GET /dashboard/digest` — signed-in only (same `dashboard_auth.current_user` guard as every other dashboard page); always recomputes fresh, never reads `Digest_Log` back for its content.
- `GET /digest/status` — `{enabled, digest_hour, digest_timezone, running, check_count, last_check_at, last_result, last_error, last_digest_date}`, no sign-in required, matching `/realtime/status`/`/health`'s existing pattern.
- `POST /digest/scan?limit=&query=&persist=` — builds today's digest immediately regardless of whether the scheduler has fired yet, and (by default) records a `Digest_Log` row. `persist=false` previews without writing. 409 when Gmail isn't connected.
- `@app.on_event("startup")`/`"shutdown"` start/stop `app.state.digest_scheduler` (a `DigestScheduler` instance created once in `create_app()`), gated on `settings.digest_scheduler_enabled`.
- The Command Center home page (`dashboard/views.py::render_command_center`) gained a "Today's Digest" link next to the existing "Undo Last Run" one.

### Tests

**883 passing** (was 845 — the suite had grown by four beyond the 841 recorded at the end of Phase 13's own entry above; unrelated to this phase). New: `test_digest_service.py` (23 — section order/exclusion, row content, timezone-default resolution, `digest_timezone`/`digest_hour` workbook-first-then-env fallback, `generate_if_due`'s not-yet-due/generated/idempotent-same-day paths), `test_digest_scheduler.py` (8 — each `DigestStatus` outcome, start/stop lifecycle, idempotent `start()`, `stop()` without `start()`, mirroring `test_scheduling_service.py`'s own structure), `test_routes_digest.py` (11 — both JSON routes, the dashboard page's auth gate, XSS-escaping, the dry-run banner), plus 6 new `DigestRepository` tests in `test_sheets_repository.py` (record/for_date/idempotent-upsert/latest, empty cases).

### Constraints honoured

- **No Gmail writes, anywhere in this phase (§5, §21).** `app/digest/` has no import of `app.gmail.write_client` or anything that could reach a Gmail-modifying call; the only writes are Sheets (`Digest_Log`).
- **Dashboard renders stay live, never read from a cache (§13's spirit, applied consistently).** `/dashboard/digest` always calls `build_digest` fresh, exactly like `/dashboard` itself does — `Digest_Log` is a receipt, not a source for the page.
- **No hidden reasoning (§13).** Digest rows show the same `reason`/`note` fields the Command Center already shows, not a new or different rationale.
- **Untrusted input (§16).** Every sender/subject/summary on the digest page passes through `html.escape` in `app/digest/views.py`, tested directly against an XSS-shaped subject.
- **Cost discipline (§17).** Both the scheduled and manual digest paths run with `use_ai=False`, matching the dashboard's own default; the scheduler's own clock check costs nothing until it's actually time to build one.

### What Phase 14 explicitly does NOT do

- **No real email delivery.** See "Product decision" above — deferred, with the new OAuth scope (`gmail.send`) it would require, to an explicit follow-up.
- **No historical digest content.** `Digest_Log` keeps counts only; there's no stored copy of a past digest's actual message list to browse later.
- **No digest content beyond CLAUDE.md §13's seven sections.** VIP Suggestions and Subscription Review remain dashboard-only.

---

## Phase 13 deliverables

### Product decision: polling over push

CLAUDE.md §3 says "prefer push/real-time notifications; fall back to polling if required" but doesn't decide for this app. Asked directly, the user chose **polling via Gmail's `history.list`** over Gmail push notifications (Google Cloud Pub/Sub): no new GCP project, no public webhook to secure, no domain verification, no 7-day `watch()` renewal, and it runs unchanged on the single Render web service this app already deploys as. Documented in `docs/plain-english/PHASE_13_REALTIME_PROCESSING.md` under "Polling, not push."

### New package (`app/scheduling/`)

- `history.py` — `scan_for_changes(gmail, start_history_id)`: pages through `users.history.list` filtered to `historyTypes=["messageAdded"]` only (deliberately excludes `labelsAdded`/`labelsRemoved` — reacting to label changes would mean reacting to this app's *own* prior writes, an unnecessary feedback risk `plan_change`'s idempotency would make harmless but wasteful). Dedupes messages seen twice in one page. A 404 (the stored history id has expired — Gmail only retains history for a limited window) is caught, not raised: the cursor resets to the mailbox's current history id and the result carries `history_gap=True`, so the caller can log the fact plainly rather than crash or silently miss the gap.
- `retry.py` — `call_with_retry(fn, attempts=3, base_delay_seconds=1.0)`: exponential backoff (1s, 2s, ...) for transient failures only (`HttpError` with status in `{429, 500, 502, 503, 504}`, or a `ConnectionError`/`TimeoutError`). Anything else (404, 403, a bad request) is re-raised on the first attempt — retrying a permanent answer just wastes cycles. Shared by the history scan and the message-apply call.
- `poller.py` — `run_poll_cycle(workbook=None, use_ai=True)`, the one real implementation both the background loop and the manual route share. Groups this cycle's changed messages by thread, fetches each thread once via the new `GmailReadClient.get_thread_full`, builds `EmailMessage`s with **genuine thread context** via `from_gmail_thread` (see below), classifies only the messages that actually appeared as new (never the rest of the thread), and — only when `gmail.apply.check_write_gate` allows it — applies each change through the same `plan_change`/`apply_to_message` Phase 11 already built. Skips the user's own outgoing copy (`sent_by_user`). One bad thread fetch or one failed apply is caught per-message/per-thread and recorded as an error, never aborting the rest of the cycle.
- `service.py` — `RealTimePoller`, the background `asyncio` loop. `start()`/`stop()` own one `asyncio.Task`; each tick calls `run_poll_cycle` inside `asyncio.to_thread` (Gmail/Sheets calls are synchronous — `googleapiclient` has no asyncio support — so a poll must not block the event loop serving the dashboard and every other route). Never lets an exception escape the loop: `NotConnectedError` and any other failure are caught, recorded in `RealTimeStatus`, and the loop ticks again next interval.

### Thread-aware classification, finally

Phase 7's own notes flagged this gap twice ("full-thread pulls come with the dashboard/real-time phases") and Phase 10 repeated it as a known limitation. Every earlier read path (`preview_recent`, the dashboard, the acceptance run) classifies messages fetched individually, so `EmailMessage.thread_message_count`/`user_in_thread` silently default to "just this one message." This phase is the one that finally closes it — but narrowly and safely: the *new* message's whole thread is fetched (one API call, `GmailReadClient.get_thread_full`, format=`full`) so CLAUDE.md §8's "active email conversations" protection sees real thread state. Fetching the thread is for **context only** — older messages in that thread are never reclassified or relabelled just because a new reply arrived, so an explicit user correction on an older message is never silently fought by the next reply (CLAUDE.md §11: explicit user decisions outrank behavioral inference).

### `build_live_context` gained an optional `workbook` parameter

`app/classification/pipeline.py::build_live_context` previously always opened its own `ControlWorkbook.connect()`. The poller already holds a connected workbook (it needs one for the history cursor and audit rows regardless), so a new optional `workbook=` parameter lets it pass that through instead of paying for a second, redundant connection. Backward compatible — every existing caller that omits it behaves identically.

### The write gate, unchanged, now with a fourth, independent switch in front of it

`REALTIME_ENABLED` (env, default `false`) and `REALTIME_POLL_INTERVAL_SECONDS` (default 120, `Field(ge=30, le=3600)`) are new `Settings` fields. `REALTIME_ENABLED` only controls whether `RealTimePoller.start()` is called at all — it has no effect on `check_write_gate`, which still requires `DRY_RUN=false`, `GMAIL_PROCESSING_ENABLED=true`, and a passed acceptance run, exactly as Phase 11 left it. With the loop on but the gate closed, `run_poll_cycle` still classifies new mail and writes a dry-style Audit_Log row via the existing `audit_service.event_from_result` (the same shape `/audit/scan` already produces) — visibility without risk, so a user can watch what real-time processing *would* do before opening the gate.

### First-poll bootstrap, not a backlog sweep

A workbook with no stored `real_time_last_history_id` (the Settings key the cursor lives under — control-workbook operational state, the same reason `last_acceptance_passed` lives there rather than in env config) doesn't process anything on its first cycle; it only records the mailbox's current history id as the starting point. The same conservative-until-opted-in pattern as every other safety default in this app: turning `REALTIME_ENABLED` on for the first time never suddenly sweeps through however much mail is already sitting in the inbox.

### Idempotency and "avoid endless reclassification"

Both fall out of properties already built in earlier phases, not new machinery: `gmail.apply.plan_change` computes an empty diff for a message already in its desired state, so a message seen twice (an overlapping history page, a retried cycle) triggers no second Gmail call and no second audit row. And because the history cursor only ever advances and Gmail never hands the same history record back twice, a message that fails permanently is reported once and not retried forever.

### Read-client additions (`app/gmail/client.py`)

- `get_thread_full(thread_id)` — `threads().get(format="full")` in one call; still read-only, still nothing here that could look like a mutation (the existing `test_client_has_no_write_methods` structural check covers it too).
- `list_history(start_history_id, history_types=None, page_token=None)` — thin wrapper over `users.history.list`.

### New routes (`app/main.py`)

- `GET /realtime/status` — `{enabled, poll_interval_seconds, running, poll_count, last_run_at, last_result, last_error, last_messages_processed, last_changed_count}`. No sign-in required, matching `/health`/`/oauth/status`'s existing pattern of system-status endpoints.
- `POST /realtime/poll?use_ai=` — runs exactly one cycle immediately via the same `run_poll_cycle` the background loop calls, whether or not `REALTIME_ENABLED` is on. 409 on `NotConnectedError`, matching every other Gmail-backed route.
- `@app.on_event("startup")`/`"shutdown"` start/stop `app.state.realtime_poller` (a `RealTimePoller` instance created once in `create_app()`), gated on `settings.realtime_enabled`.

### Tests

**841 passing** (was 802). New: `test_scheduling_retry.py` (7), `test_scheduling_history.py` (7), `test_scheduling_poller.py` (13 — bootstrap, no-changes, history-gap, gate-closed dry logging, gate-open real apply + undoable run, idempotent reprocessing, skip-the-user's-own-mail, a vanished message, error isolation across threads, one-fetch-per-thread), `test_scheduling_service.py` (7 — each `RealTimeStatus` outcome, start/stop lifecycle, idempotent `start()`, `stop()` without `start()`), `test_routes_realtime.py` (5), plus 2 new `GmailReadClient` tests (`get_thread_full`, `list_history`). The poller tests monkeypatch `app.scheduling.poller.classify` directly rather than depending on the real rules engine's exact output — they exercise the poller's own orchestration (gate checks, thread fetch, apply, audit rows, idempotency, error isolation), which the classification engine's own exhaustive suite doesn't cover.

### A pre-existing issue fixed in passing

`.env.example` contained what looked like real Google OAuth client credentials rather than placeholders, contradicting CLAUDE.md §16 ("`.env.example` has placeholder names only"). Replaced with placeholder text. Unrelated to Phase 13's actual work but caught while touching the file for the new `REALTIME_*` settings.

### Constraints honoured

- **No automatic Trash, ever (§5).** The real-time apply path reuses `gmail.apply.apply_to_message` unchanged — there is still no code path from an automatic decision to `trash_message()`.
- **Never pretend to have processed a gap it couldn't see (§5's spirit, extended).** A history-cursor expiry is reported (`history_gap=True`), not silently absorbed or guessed at.
- **Explicit user decisions outrank behavioral inference (§11).** Fetching a whole thread is for classification context only; only the genuinely new message(s) in that thread are ever classified or relabelled.
- **Log failures, never crash the loop (§13).** Both per-message and per-thread failures are caught and recorded; `RealTimePoller` catches everything out of `run_poll_cycle` so one bad cycle never ends the loop.
- **Cost discipline (§17).** Context (contacts + workbook rules) is built once per cycle, not once per message; `use_ai` still only consults AI for messages the deterministic rules flagged `needs_ai`.

### What Phase 13 explicitly does NOT do

- **No push notifications.** See "Product decision" above.
- **No retroactive reclassification of a thread's older messages.**
- **No historical backlog processing.** The first poll for an account only bookmarks "now" — Phase 15 covers a full 12-month pass.
- **No dashboard UI for real-time status yet.** `/realtime/status` is JSON-only; a Command Center card is a small addition a future phase (or a follow-up) can add without touching this phase's core mechanism.

---

## Phase 12 deliverables

### New package (`app/undo/`)

- `service.py` — `preview_last_run(workbook)` / `preview_run(workbook, run_id)` (read-only — build an `UndoPreview` from a `System_Runs` row plus its reversible `Audit_Log` rows) and `undo_run(workbook, run_id)` (the actual reversal). Checks `app.gmail.apply.check_write_gate` first, same as every other write path — `DRY_RUN=true` blocks Undo too, deliberately with no carve-out for "the original write already happened for real" (documented as a resolved open item from Phase 11, in favor of one rule with no exceptions over CLAUDE.md §21). For each affected message: reads Gmail's *current* label state (`gmail_apply.fetch_current_labels`), diffs it against the audit row's `labels_before`, and issues the minimal `modify`/`untrash` call to get there — restoration, not replay; the classifier is never consulted. A 404 from Gmail (message genuinely gone) is caught and reported per-message as `"not_found"` rather than crashing the whole undo or claiming success (CLAUDE.md §5: never pretend an operation is reversible if Gmail no longer allows it).
- `views.py` — mirrors `app/acceptance/views.py`'s pattern (its own `_page()` wrapper, same reasons). `render_preview` is the required stop before anything happens — a GET that changes nothing, listing every affected message and what will be restored, with a single confirm button. `render_result` reports outcomes individually (restored / already fine / no longer recoverable), never one all-or-nothing verdict.

### "A run" now includes a single dashboard click

Phase 11's `write_service.apply_recent` already grouped a batch apply under one `run_id` with a `System_Runs` row. `app/gmail/dashboard_ops.py::restore_to_inbox` / `trash_message` now do the same for a *single* click — their own `run_id`, their own one-row `System_Runs` entry (`mode="live"`, `undo_available=True`). Without this, only batch runs would ever be undoable, which would make Undo far less useful than the button it exists to reverse. `apply.py`'s single-message label-fetch helper (previously private to `dashboard_ops`) is now `apply.fetch_current_labels`, shared by both call sites.

### The one deliberate exception to "append-only"

`Audit_Log` stays fully append-only — Undo never rewrites a row there; it appends a new one describing the undo itself (`action_taken="Undo Last Run: ..."`, sharing the original `run_id` so the full story reads coherently in order). `System_Runs.undo_available`, though, is a genuine live status field, not a historical fact fixed at write time — its whole purpose is to flip once a run is reversed. `SystemRunsRepository` gained `mark_undone(run_id)` (built on the generic `SheetTable.update()` every other repository already uses for its own current-status fields, e.g. `Settings`) and `latest_undoable()` (scans for the most recently appended row with `undo_available=true`). This is documented in the repository's own docstring as a narrow, deliberate exception — not a quiet erosion of the append-only guarantee that makes `Audit_Log` trustworthy.

### Routes (`app/main.py`)

- `GET /dashboard/undo` / `POST /dashboard/undo` — the confirm-then-act pair, same shape as Trash's confirmation page from Phase 11. A GET never changes anything.
- `GET /undo/preview` / `POST /undo/run?run_id=&confirm=` — JSON mirrors for programmatic use, matching the pattern established by `/acceptance/run` and `/gmail/apply`. `confirm=true` is required explicitly; omitting it returns `status: "not_confirmed"` and touches nothing.
- The Command Center home page (`dashboard/views.py::render_command_center`) gained an "Undo Last Run" link.

### Tests

**802 passing** (was 780). New: `test_undo_service.py` (9 — preview shape, reversible-row filtering, the write gate refusing Undo, restoring labels/Inbox, idempotency when already in the target state, restoring out of Trash, and a 404-from-Gmail handled per-message without crashing), `test_routes_undo.py` (9 — sign-in gate, "nothing to undo," the preview page never itself undoing anything, the confirm route's gate-closed and gate-open paths, and both JSON routes), plus 4 new `SystemRunsRepository` tests in `test_sheets_repository.py` (`latest_undoable` empty/most-recent, `mark_undone` success/unknown-run).

### Constraints honoured

- **Never pretend an operation is reversible if Gmail no longer allows it (§5).** Every message gets its own outcome; a 404 is reported, not swallowed or faked as success.
- **Confirmation before acting, every time (§5).** Both the dashboard and JSON paths require an explicit second step; a GET or an unconfirmed POST never writes.
- **No hidden reasoning (§13).** The undo confirmation page explains itself in the same plain terms as every other screen — what will change, not why the app thinks it should.
- **One write gate, no exceptions (§21).** Undo is a real Gmail write and is gated exactly like every other one, including while `DRY_RUN` is back on.

### What Phase 12 explicitly does NOT do

- **No "redo."** Once a run is undone, that's the end of its story — there's no code path to reverse an undo.
- **No automatic or scheduled undo.** Always a deliberate, confirmed click or an explicit `confirm=true`.
- **Doesn't re-run the classifier.** Deliberately — Undo's job is narrower than reclassification, and conflating the two would make "restore exactly what was there" an unreliable guarantee.

---

## Phase 11 deliverables

### New modules (`app/gmail/`)

- `write_client.py` — `GmailWriteClient`, deliberately a *separate* class from the read-only `GmailReadClient` (which has a standing test, `test_client_has_no_write_methods`, that fails if it ever grows a method that looks like a mutation). `ensure_labels(names)` lists Gmail's labels once, caches for the client's lifetime, and creates only what's missing (the same header-caching lesson Phase 10 learned the hard way about Sheets, applied here before it could become a bug). `modify_message(id, add_label_ids, remove_label_ids)` combines every label change into one `messages.modify` call. `trash_message` / `untrash_message` wrap Gmail's own Trash, which is recoverable for 30 days. There is no method here that could reach a permanent delete — the class simply never calls that endpoint.
- `apply.py` — the safety-critical half. `check_write_gate(workbook)` is the one function every write path calls first: `DRY_RUN` must be `false`, `GMAIL_PROCESSING_ENABLED` must be `true`, *and* `workbook.settings.get_bool("last_acceptance_passed", False)` must be `true` — the concrete gate Phase 10 promised and left for this phase to actually enforce. `plan_change(message, classification, label_name_to_id)` computes the minimal label diff: add/remove `AI/*` labels to match `classification.gmail_label_names` exactly (so a re-evaluation removes a stale label, not just adds new ones), touch `INBOX` only when the classification has an actual opinion (`keep_in_inbox` or `archive` — "neither" must never read as "archive it," mirroring the same rule in `labels.py`'s `CombinedPolicy`), and add `IMPORTANT` only — never remove it, mirroring the AI layer's own "can raise, never lower" guarantee from Phase 4. `apply_to_message` executes the plan as one API call if non-empty, and trusts Gmail's own returned `labelIds` for the after-state rather than reconstructing it — the API response is the actual source of truth.
- `write_service.py` — `apply_recent(limit, query, confirm, ...)`, the bounded, manually-triggered counterpart to Phase 10's acceptance run: runs the same read-only pipeline, and only if `confirm=True` *and* the gate allows it, actually writes. `confirm=False` (the default) always previews, regardless of settings — same "see it before you do it" shape as `/acceptance/run`. Continuous processing of new mail is explicitly Phase 13's job, not this module's.
- `dashboard_ops.py` — `restore_to_inbox` / `trash_message`, the two direct, single-message user actions (not classification-driven, so they don't go through `apply.py`'s diff machinery — there's no "what would the rules engine decide" question for a user clicking a button). Both check the write gate and a missing-scope guard first, so a stale pre-Phase-11 token or a closed gate produces a clear refusal instead of a raw Google 403 or a silent no-op.

### The write gate, and why it's three independent checks

CLAUDE.md §15 says a failed acceptance run must block live writes; Phase 10 recorded `last_acceptance_passed` in Settings specifically so this phase could read it. `check_write_gate` requires all three of `DRY_RUN=false`, `GMAIL_PROCESSING_ENABLED=true`, and that flag — every real write path (`/gmail/apply`, Restore, Trash) calls it before doing anything, and every one degrades to a plain-English refusal listing exactly which conditions aren't met yet, never an exception or a silent skip.

### Two Review-queue buttons go live

`dashboard/actions.py::perform()` now dispatches `restore` and `trash` to `gmail.dashboard_ops` instead of leaving them disabled. Trash is never a single click: `views.py::_row_actions_html` renders it as a link to `GET /dashboard/trash-confirm` (a page that changes nothing — the confirm page's own form is the only thing that posts to `/dashboard/action/trash`), naming the exact sender and subject before the user can proceed. The five workbook-only buttons from Phase 9 are unchanged.

### Real before/after auditing

`audit/service.py` gained `event_from_applied_change`, used by every real write. Unlike Phase 9's placeholder events (`labels_before == labels_after` always, because nothing had changed yet), these rows carry Gmail's genuine label and Inbox state on both sides, `reversible=True`, and `undo_status="not_undone"` — exactly what Phase 12's Undo Last Run will need, and nothing reads that status yet.

### The new OAuth scope

`gmail.modify` (add/remove labels, archive, Trash — not send, not a permanent delete) is added as `PHASE_11_SCOPES` in `app/gmail/scopes.py` and folded into `ACTIVE_SCOPES`. A token from before this phase is missing it; `missing_scopes()` (already built in Phase 1/2) detects that automatically, so every write path refuses with a "reconnect at /oauth/start" message rather than a raw permission error. `test_oauth_scopes.py`'s old blanket "no gmail.modify, ever" assertion is now scoped down to "no scope beyond this one documented write scope, ever" — a deliberate, tested loosening, not a silent one.

### Routes (`app/main.py`)

- `POST /gmail/apply?limit=&query=&confirm=&use_ai=&contacts=&rules=&attachments=` — the manual batch-apply endpoint. `confirm=false` always previews. 409 on a pre-Phase-11 token (missing scope) or a disconnected account.
- `GET /dashboard/trash-confirm?message_id=&...` — the required stop before Trash (CLAUDE.md §5). Signed-in only; 400 without a `message_id`.
- `POST /dashboard/action/restore` / `POST /dashboard/action/trash` — now real Gmail writes, gated the same way as `/gmail/apply`.

### Tests

**780 passing** (was 748). New: `test_gmail_write_client.py` (6 — label caching, combined modify call, trash/untrash, no delete/batchDelete endpoint ever called), `test_gmail_apply.py` (13 — the write gate's three conditions independently and together, the label diff's every rule including the two "never automatically" guarantees and the Trash-Candidate exclusion, `apply_to_message`'s no-op-skips-the-API-call and single-call behavior), `test_routes_gmail_write.py` (12 — Restore/Trash refuse when the gate is closed and succeed when it's open, the confirm page never itself trashes anything, `/gmail/apply`'s preview-by-default and confirm+gate-open write paths, and that apply never calls Trash). `test_oauth_scopes.py` gained 2 (missing-scope detection for a pre-Phase-11 token; the new scope is exactly `gmail.modify`, nothing more).

### Bugs found and fixed during Phase 11

While actually running the real acceptance test against a live mailbox for the first time (the trigger for starting this phase — CLAUDE.md §15 gates Phase 11 on a real pass, not just a green test suite), three unrelated bugs surfaced and were fixed along the way:

1. **OAuth token exchange failed on `http://localhost` with `InsecureTransportError`.** `google-auth-oauthlib` refuses to exchange a code over plain HTTP unless `OAUTHLIB_INSECURE_TRANSPORT=1` is set. Fixed in `app/gmail/oauth.py` — set automatically, but only for a non-production app using an `http://` redirect URI; a no-op on Render, where the redirect is always HTTPS.
2. **OAuth token exchange then failed with `invalid_grant: Missing code verifier`.** `google-auth-oauthlib`'s `Flow` auto-generates a PKCE code verifier, but `build_authorization_url()` and `exchange_code_for_token()` each construct a *separate* `Flow` object — the verifier from the first was never available to the second. Since this app already authenticates with a `client_secret` (a confidential client), PKCE isn't needed; fixed by passing `autogenerate_code_verifier=False`.
3. **A legitimate Anthropic billing email was routed to Review as "suspicious."** `signals._detect_suspicion`'s Reply-To check compared exact domain strings, so a sender on `mail.anthropic.com` replying via `anthropic.com` — completely normal bulk-mail infrastructure — scored as a "redirect." Fixed by comparing registrable domains (`registrable_domain()`, already used elsewhere for the same reason), matching the codebase's existing "widen, never narrow" pattern for domain protection.
4. **The Sheets API read quota (60/minute) was exhausted writing a single acceptance run's Audit_Log.** `SheetTable.append()` called `self._load()` (a full tab read) on every single row, and `invalidate()` at the end of every write wiped that read straight back out — so 250 audit rows meant 250 reads in a few minutes. Fixed with a separate, longer-lived header cache (`_cached_header()`) that write paths use instead of the row-data cache, since a tab's column names don't change mid-run the way its rows do.
5. **The Sheets API *write* quota (also 60/minute) was then exhausted by the same run.** `record_run` appended one Audit_Log row per message — 250 separate `values.append` calls. Fixed by adding `SheetTable.append_many()` (one API call for the whole batch) and having `audit.service.record_run` build all rows first and call it once. The same lesson (batch writes, not one-per-item) was then applied proactively in this phase's own new code — `write_service.apply_recent` batches its audit rows the same way from the start.

None of these were classification-logic bugs; the rules engine's actual decisions were correct throughout. All five are now guarded by regression tests, and #3 specifically is why `test_oauth_scopes.py` and the acceptance run both needed a real, live mailbox to catch — no mocked test corpus would have produced Anthropic's actual Reply-To header shape.

### Constraints honoured

- **No automatic Trash, ever (§5).** There is no code path from a classification decision to `trash_message()` — `apply.py`'s module docstring states this as a structural guarantee, and `test_apply_never_calls_trash` checks it. Trash is reachable only from the dashboard's confirm-then-post flow.
- **Add-only Important (§21, mirroring §4's AI guarantee).** `plan_change` never emits `IMPORTANT` in `remove_label_names` — tested directly.
- **The gate is three independent conditions, not one (§15).** A `.env` flip alone is never enough; the acceptance-passed check reads live workbook state every time, so a failed rerun immediately closes the gate again without any code change.
- **`AI/Trash-Candidate` never reaches Gmail (§6).** Still true after this phase — `plan_change` builds its add-set from `classification.gmail_label_names`, which already filters it out (Phase 3's `gmail_labels()`), and a dedicated test re-confirms it end-to-end through the diff logic.

### What Phase 11 explicitly does NOT do

- **No automatic or scheduled processing.** `/gmail/apply` and the dashboard buttons are all user-triggered. Continuous handling of new mail is Phase 13.
- **No Undo yet.** The audit rows now carry genuine before/after state and `undo_status="not_undone"`, but nothing reads that status back — Phase 12.
- **No permanent delete, anywhere, ever.** Not a missing feature — there is no Gmail API method for it that `gmail.modify` even grants, so the write client structurally cannot reach one.

---

## Phase 10 deliverables

### Real acceptance run + bugs found running it for real

The 250-email acceptance run described below was actually executed against a live, connected mailbox during Phase 11's work (running it for real is what CLAUDE.md §15 requires before Phase 11 may ship, so the two phases' real-world testing happened together). It passed cleanly on the first attempt with the classification engine unchanged — `protected_routed_to_review: 0`, 250/250 classified. Getting to that point surfaced five real bugs, none in the classification logic itself: two in the OAuth token-exchange flow, one in a suspicion heuristic (a false positive on a legitimate sender's Reply-To header shape), and two in the Sheets repository layer's API-quota handling. See "Bugs found and fixed during Phase 11" above for the detail — they're recorded there because fixing them was Phase 11 work, even though the run itself exercises Phase 10 code.

### New package (`app/acceptance/`)

The CLAUDE.md §15 launch quality gate, built as infrastructure the user (or Claude Code, walking them through it) runs against their real, connected mailbox — this is the one phase whose actual acceptance run can't happen inside a coding session, since it needs a live OAuth token. What Phase 10 ships is everything short of that: the sampling, the classification wiring, the report, and a permanent, fully-automated stand-in (the golden dataset) that runs on every `pytest` pass.

- `strata.py` — `STRATA`, the 18 category buckets CLAUDE.md §15 names (financial, security, government, personal, work, career, receipts, purchases, travel, educational, Substack, other newsletters, promotions, automated notifications, cold outreach, attachments, active threads, suspicious), plus a `catch_all` topper. Targets sum to exactly `DEFAULT_SAMPLE_TARGET = 250`. Each bucket carries a real Gmail search query — **honestly documented as a best-effort approximation**, not an exact partition: Gmail search has no operator for "an email a human would call personal" or "a thread with back-and-forth in it," so `personal`/`work` share one query (excluding Gmail's own bulk categories) and rely on the classifier's own labels to separate them afterward, and `active_threads` has no query at all, falling through to the same broad recent-mail pull as `catch_all`.
- `service.py` — `build_stratified_sample(gmail, target_total)` runs each bucket's search, deduplicates by message id, and tops up from a plain recent-mail pull if any bucket came up short. `run_acceptance_test(...)` wires that into `pipeline.classify_raw_messages` (see the pipeline refactor below) and builds an `AcceptanceReport`. `persist_report(workbook, report, results, started_at)` is a separate, explicit step — called from the route, never automatically — that writes the run to `System_Runs`, writes one `Audit_Log` row per message (reusing `audit.service.record_run`), and stamps three `Settings` flags: `last_acceptance_run_id`, `last_acceptance_passed`, `last_acceptance_at`. That last one is the concrete, checkable gate a future Phase 11 can read before ever allowing a live Gmail write — CLAUDE.md §15's gate stops being just a document convention and becomes something code can consult. A small in-memory cache (last 5 runs) lets the dashboard report page show a run's result after the `POST` that produced it, in the same single-process style as everything else in this app (see Known Limitations).
- `golden.py` — `GoldenExpectation`, `GoldenExample`, `evaluate()`. Pure and reusable; deliberately never imports from `tests/`, so the app package stays independent of test fixtures. Computes the same headline number the live run does — `protected_false_review_rate` — plus overall accuracy and a per-category breakdown, matching CLAUDE.md §15's metrics list exactly.
- `models.py` — `Stratum`, `FalseReviewCase`, `AcceptanceReport` (`.passed` is just `len(false_reviews) == 0`).
- `views.py` — the "review dashboard" CLAUDE.md §15 asks for: a gate banner (green PASSED / red FAILED with the count), the sample's category breakdown against target, summary stats, and — the human-in-the-loop part no metric can replace — the full list of everything the run routed to Review, for the user to actually read. The banner says so explicitly: the automated count only catches what the app already recognized as protected; a coverage gap in the engine's own protection logic (the harder failure mode) needs a human's eyes on the Review list itself.

### The pipeline refactor that made this possible

`app/classification/pipeline.py` gained `classify_raw_messages(raw_messages, user_email, context, gmail, ...)` — the per-message classify loop extracted out of `preview_recent`, which now just fetches one bounded window and calls it. `MAX_PREVIEW_MESSAGES = 50` exists to stop one ad-hoc query from sweeping the mailbox; it has nothing useful to say about a deliberately assembled, already-bounded 250-message batch built from many small stratified searches, so the acceptance service calls `classify_raw_messages` directly rather than working around the cap. Purely additive — `preview_recent`'s external behavior is unchanged, and the whole existing test suite proved that on the first run after the refactor.

### `System_Runs` gets its first writer

The `System_Runs` tab (CLAUDE.md §12) existed since Phase 2's schema but had never been written to. `SystemRunsRepository` (`app/sheets/repository.py`) wraps it — append-only, like `Audit_Log`, with `record()` and `for_run()`. Every acceptance run writes one row here (`mode="dry_run"`) alongside its per-message `Audit_Log` detail, joined by `run_id`.

### Why an acceptance-run crash is itself a gate failure, not a bug to catch

`engine._assert_safety_invariants` already raises if a protected email is ever routed to Review (CLAUDE.md §3 Phase 3). That means `AcceptanceReport.false_reviews` can, in ordinary operation, only ever be empty — a violation crashes the run before a report is even built. Nothing in `run_acceptance_test` or `classify_raw_messages` catches that exception. This is deliberate, not an oversight: swallowing it would defeat the point of the gate. An acceptance run that crashes with a safety-invariant error is the single worst possible outcome the app could report, and it should stop the whole process cold, exactly like the code that guards it already says: *"a crash in a dry run beats a hidden email."*

The real value of the live 250-email run is therefore in what the invariant *can't* catch: a real email a human would call important that the engine's protection logic never recognized as protected in the first place (so it was never a "violation," just a miss), and that's why the report leads with the Review list itself, not just the pass/fail count.

### The golden dataset (`tests/golden_dataset/`)

25 hand-labeled, realistic examples in `dataset.py`, covering every one of CLAUDE.md §15's named categories plus the protection mechanisms that matter most: VIP, known contact, prior correspondent, active thread, and starring (each shown actually rescuing a message that would otherwise have been reviewed), and — the sharpest test in the set — an attachment protecting a message whose subject reads as pure "50% off, shop now!" promotional bait. One example demonstrates the *other* safety net: a P2 material-change message with bulk-mail headers, correctly kept out of Review by the priority veto rather than the protection veto. Every expectation was checked against the real engine (`engine.py`, `protection.py`, `signals.py`, `patterns.py`) while writing it, not guessed — all 25 matched on the first test run.

`test_golden_dataset.py` runs on every `pytest` pass and asserts `protected_false_reviews == 0` and 100% match against the hand-labeled expectations — a permanent regression check per CLAUDE.md §15's "used to compare classifier versions," and the part of the launch gate that doesn't need a live mailbox to verify.

### Routes (`app/main.py`)

- `POST /acceptance/run?target=250&use_ai=&read_attachments=&contacts=&rules=&persist=true` — runs the stratified sample through the same read-only pipeline as everywhere else. `gmail_modified: false` always. Returns the full report; `persist=false` previews without writing to the workbook. 409 when disconnected — the one route in this phase that genuinely needs the user's own mailbox.
- `GET /dashboard/acceptance` / `GET /dashboard/acceptance/{run_id}` — the report view, gated behind the same Google Sign-In as the rest of the Command Center. Reads the in-memory cache only; a report from before the last server restart, or older than the last 5 runs, is gone (the durable record is `System_Runs` + `Audit_Log`).

### Tests

**748 passing** (was 722). New: `test_golden_dataset.py` (4), `test_acceptance_strata.py` (5), `test_acceptance_service.py` (7), `test_routes_acceptance.py` (8), plus `test_sheets_repository.py` additions for `SystemRunsRepository` (+2).

### Constraints honoured

- **No Gmail writes.** Every acceptance-run write lands in the control workbook only.
- **Err toward preserving important email (§21).** The dashboard banner tells the user not to trust the pass/fail count alone — read the Review list.
- **Cost discipline (§17).** `use_ai` defaults on for a real run (matching `/classify/preview`) but every test in this phase runs with `use_ai=false`, so the suite spends nothing.

### What Phase 10 explicitly does NOT do

- **It doesn't run the real 250-email test itself.** That needs a connected Gmail account with real mail behind it — a step only the user (with Claude Code's help, if wanted) can actually take. Everything needed to run it, `POST /acceptance/run?target=250`, is in place.
- **No automatic gate enforcement yet.** `last_acceptance_passed` is recorded in Settings for Phase 11 to read, but Phase 11 (the only thing that could act on it) doesn't exist yet.
- **No thread-aware `active_threads` targeting.** Same known limitation as the dashboard and `/classify/preview` since Phase 7 — full-thread fetching is a later phase's job.

---

## Phase 9 deliverables

### New packages (`app/audit/`, `app/learning/`) and `app/dashboard/actions.py`

- `app/audit/models.py` — `AuditEvent`, a 1:1 mirror of every `Audit_Log` column (CLAUDE.md §12). `as_row()` is the single place field-to-column mapping happens. `safe_subject_ref()` truncates to 120 chars — the audit trail never carries more of an email than it needs (§16).
- `app/audit/service.py` — `event_from_result(result, run_id)` builds one row per classified message; `event_from_action(...)` builds one row per dashboard click (`actor="user"`); `record_run(workbook, results, run_id=None)` writes a whole batch under one shared `run_id` — the key the future Undo Last Run (Phase 12) will read via `AuditRepository.for_run`.
- `app/learning/service.py` — `keep`, `review_correct` (feedback-only), `make_sender_rule`, `make_domain_rule` (suggestion-only, refuses public providers), `suggest_vip`, `suggest_vips_from_results` (correspondence-pattern heuristic), `promote_approved_suggestions` (the one place a suggestion becomes a real rule).
- `app/dashboard/actions.py` — thin dispatcher: `perform(action, workbook, ...)` calls the right `learning.service` function, then always writes one `audit.service` event, whether the action succeeded or was refused.

### The audit trail never claims a Gmail change that didn't happen

Phase 9 still makes **zero Gmail writes**. So `event_from_result` sets `labels_before == labels_after` and `inbox_before == inbox_after` on every row, always — what the rules engine *would* do lives in the `classification`, `rules_triggered`, and `ai_reason_summary` columns instead, not in a fabricated "after" state. `ai_reason_summary` is always the same short, user-facing rationale already shown elsewhere in the app — never a hidden reasoning transcript (§13). `reversible` is `False` and `undo_status` explains why (`"not_applicable (dry run)"` / `"not_applicable (workbook-only action)"`) on every Phase 9 row, because nothing has happened yet that Phase 12 could undo.

`POST /audit/scan` runs the identical read-only pipeline `/classify/preview` already exposes (same `contacts`/`rules`/`ai`/`attachments` params) and additionally records it, one row per message, all sharing a `run_id`. `persist=false` previews the run without writing it.

### The five live Review-queue buttons

CLAUDE.md §13 lists seven Review-queue actions. Phase 9 turns five of them on — Restore to Inbox and Trash stay disabled because they'd need a real Gmail write, which is Phase 11.

| Button | What it does now |
|---|---|
| Keep | `Review_Feedback` row, `user_decision=kept`. Deliberately a neutral, no-strong-opinion signal — distinct from Review Correct, and doesn't feed rule suggestions on its own (§11: behavioral signals must not silently create rules). |
| Review Correct | `Review_Feedback` row, `user_decision=review_correct` — the explicit "the app was right" confirmation. |
| Make Sender Rule | Feedback row **and** a *pending* `Learned_Rule_Suggestions` row (`RulesRepository.add_rule_suggestion`). Never an active rule from one click. |
| Make Domain Rule | Same, but refuses outright for public mailbox providers (gmail.com, outlook.com, …) with an on-page explanation, rather than silently creating a suggestion that would later be ignored anyway. |
| Suggest VIP | Feedback row and a pending `VIPs` row (`VIPRepository.suggest`, unchanged since Phase 2/8). |

A note on product interpretation: Phase 8 shipped "Make Sender/Domain Rule" with copy implying immediate effect ("Always handle this exact sender this way"). CLAUDE.md §11 and §19 say **every** learned rule is a suggestion requiring approval, without carving out an exception for an explicit click versus an inferred pattern — and the risk the rule is guarding against ("one message → a permanent domain-wide rule") is exactly what a single click produces. Phase 9 resolves the ambiguity toward that stricter, spec-literal reading: both buttons now say "Suggest…" and land in `Learned_Rule_Suggestions`, matching how VIP already worked.

### Closing the approval loop: promotion

A suggestion in `Learned_Rule_Suggestions` did nothing by itself — approving it in the sheet doesn't change classification unless something turns it into a real `Sender_Rules`/`Domain_Rules` row. That mechanical step was missing until now:

- `RulesRepository.add_sender_rule` / `.add_domain_rule` — idempotent upserts, built on the same `_KeyedTable` pattern Phase 6 introduced for Deadlines/Subscriptions/Trips, keyed on sender/domain so re-running never duplicates.
- `RulesRepository.approved_suggestions()` — reads `Learned_Rule_Suggestions` rows the user has flipped to `approved` by hand in the sheet.
- `learning.service.promote_approved_suggestions(workbook)` — turns each into an active rule. Refuses (and logs) a public-provider domain even if it was somehow approved, on top of the existing `context.build_rule` safety net that would silently ignore it at classification time regardless.
- `POST /learning/promote-suggestions` is the only thing that calls it. Deliberately **not** automatic on a classification run or a dashboard page load — that would make a `GET` request silently mutate rules, breaking the "dashboard renders are read-only" guarantee from Phase 8. The user (or a future scheduler) triggers it explicitly, after approving suggestions in the sheet.

### VIP suggestions from correspondence patterns

`learning.service.suggest_vips_from_results(workbook, results)` scans one classification batch for the CLAUDE.md §8 signals — frequent correspondence (≥3 messages from the same sender in the window, `VIP_FREQUENCY_THRESHOLD`), an active back-and-forth thread, or a starred message — and proposes a VIP for any sender clearing one of those bars **and** whose mail the engine already classified Personal or Work/Business (so a frequently-arriving newsletter doesn't qualify). Reuses data Phases 3-8 already compute; no extra Gmail calls. Always a suggestion — `VIPRepository.suggest` is pending-only and idempotent, so re-running is safe. Exposed at `POST /learning/suggest-vips?limit=&query=&persist=true`, a scan endpoint rather than something dashboard page loads trigger, for the same read-only-GET reason as promotion.

### New repositories (`app/sheets/repository.py`)

- `ReviewFeedbackRepository` — `record()` (append, auto-stamped timestamp), `for_message()`, `all()`.
- `AuditRepository` — `record()`, `for_run()`, `all()`.
- `RulesRepository` gained `add_sender_rule`, `add_domain_rule` (idempotent), `approved_suggestions()`.
- `ControlWorkbook` gained `.review_feedback` and `.audit_log`.

### Dashboard wiring (`app/main.py`, `app/dashboard/views.py`)

`POST /dashboard/action/{action}` handles the five live buttons: reads the row's hidden form fields (message/thread id, sender, subject, classification, reason — no re-fetch from Gmail needed), connects the workbook, calls `dashboard.actions.perform`, and redirects back to the Review list with `?notice=` or `?error=` (rendered as a green confirmation or a red banner). A workbook that's unreachable degrades to the same error banner rather than a 500. The Review-queue buttons are now real `<form method="post">`s with hidden fields instead of static disabled `<button>`s, for the five that are live.

### New dependency

`python-multipart` — FastAPI needs it to parse the Review-queue buttons' `POST` form bodies. Small, standard, no wider footprint.

### Tests

**722 passing** (was 670). New: `test_audit_service.py` (8), `test_learning_service.py` (21), `test_routes_audit.py` (3), `test_routes_learning.py` (6), plus additions to `test_sheets_repository.py` (new repository/writer coverage) and `test_routes_dashboard.py` (+7: each live action's success path, the public-provider refusal banner, an unknown-action 404, and a workbook-unreachable degrade path).

### Constraints honoured

- **No Gmail writes.** Every new route and every dashboard action writes only to the control workbook.
- **Never silently create a permanent rule (§11).** A suggestion is the only thing a click produces; promotion only ever acts on rows the user has already approved by hand in the sheet.
- **Domain rules never trust a public provider (§8).** Enforced at three independent points: suggestion creation (with an on-page explanation), promotion (silently skipped + logged), and classification (`context.build_rule`, in place since Phase 3).
- **No hidden AI chain-of-thought (§13).** `ai_reason_summary` is always the same short rationale shown elsewhere, never a reasoning transcript.

---

## Phase 8 deliverables

### New package (`app/dashboard/`)

The first real UI (CLAUDE.md §13). The dashboard is *mostly a view* — Phases 3–7 already compute everything it shows.

- `auth.py` — dashboard access control. Two independent halves. **Sessions:** signed cookies via `itsdangerous.URLSafeTimedSerializer` (salt `gmail-agent.dashboard-session`, the same `session_secret` the OAuth `state` uses), `issue_session`/`read_session` with expiry from `dashboard_session_max_age_hours`. **Authorization:** `authorized_emails()` = the connected Gmail account's email **plus** the `dashboard_authorized_emails` allowlist (the future-accounts seam; no multi-tenant). `current_user(request)` re-checks authorization on *every* request, so disconnecting Gmail or editing the allowlist invalidates old cookies immediately. The **Google Sign-In** round-trip (`build_login_url` / `complete_login`) uses identity-only scopes (`openid`, `userinfo.email`) — it learns *who* you are, never mailbox access. `complete_login` is the single network function; everything security-critical around it is pure and unit-tested without Google.
- `service.py` — `build_command_center(limit, query, today)` runs the read-only pipeline once (`use_ai=False`, `read_attachments=False` — a page load is fast and free), builds the Phase 6 intelligence, refines + evaluates Phase 7 follow-ups, and arranges everything into nine `Card`s and their backing `Row` lists. `CARD_DEFS` fixes the §13 order (P1, P2, Action, Waiting, Due Soon, Overdue, Review, VIP, Subscription). VIP suggestions read from `workbook.vips.suggested()`, degrading to empty when the workbook is unreachable. Attachment *text* is not downloaded — the 📎 indicator comes from metadata.
- `views.py` — server-rendered HTML. **The single choke point where untrusted email text becomes HTML:** every sender/subject/summary passes through `html.escape` here (test: a `<script>` subject renders inert). Collapsible explainers use native `<details>` — the page needs no JavaScript. The Review queue lays out the seven §13 action buttons as `disabled` with per-button plain-English help; they activate in Phase 11.

### Auth model (V1, single-user with a seam)

`is_authorized(email)` ⟺ `email ∈ authorized_emails()`. The owner is whoever connected Gmail (`StoredToken.account_email`); `DASHBOARD_AUTHORIZED_EMAILS` adds more without a rewrite. **Connecting Gmail also issues a dashboard session** (`oauth_callback` sets the cookie for the authorized account), so the normal path needs no second Google screen. The standalone `/dashboard/auth/start` → Google → `/dashboard/auth/callback` flow requires `DASHBOARD_LOGIN_REDIRECT_URI` to be registered on the OAuth client (documented deployment step).

### Routes (`app/main.py`)

- `GET /dashboard` — guard → `build_command_center` → home. No session → 303 to `/dashboard/login`; connected-but-no-Gmail → a "connect Gmail" page (catches `NotConnectedError`).
- `GET /dashboard/list/{card_key}` — guard → list view; unknown card → 404. The Review list is the one with the action toolbar.
- `GET /dashboard/login` — sign-in page (redirects to `/dashboard` if already in).
- `GET /dashboard/auth/start` — builds the Google login URL (307 redirect); missing client creds → friendly 500 page.
- `GET /dashboard/auth/callback` — `complete_login` → `is_authorized`? set cookie + 303 to `/dashboard` : 403 unauthorized page. `PermissionError`/failures render the login page with a message.
- `POST /dashboard/logout` — clears the cookie, 303 to login.
- `/static` is mounted from the project-root `static/` dir (absolute path off `app/main.py`), serving `dashboard.css`.

Landing page (`/`) gained an **Open the Command Center** button; the JSON classify preview is now labelled as the raw view.

### Settings (`app/config/settings.py`)

`dashboard_authorized_emails: str = ""` (+ parsed `dashboard_authorized_email_list` property), `dashboard_session_max_age_hours: int = 12`, `dashboard_login_redirect_uri`. `reload_settings` is now re-exported from `app.config`.

### The deliberate HTMX deviation (documented per rule 18.6)

CLAUDE.md §3/§14 name HTMX for Phase 8. It was **not** added yet: in Phase 8 no button executes, so there is no partial-update to perform — HTMX would be weight with no job. Disclosure widgets use native `<details>` (zero JS). HTMX is deferred to **Phase 11**, where "Restore"/"Trash" genuinely need in-place row updates. Flagged in the plain-English doc and to the user rather than skipped silently. Reversible: the views are structured as fragment-friendly `<article class="row">` blocks that drop straight into `hx-*` swaps later.

### Tests

**670 passing** (was 644). New: `test_dashboard_auth.py` (11 — session round-trip/tamper/foreign-secret/expiry, authorization incl. allowlist + case-insensitivity + no-connection, `current_user`), `test_dashboard_service.py` (7 — card order, count↔list consistency, populated cards, row display fields, P1-never-in-Review, dry-run flag), `test_routes_dashboard.py` (8 — access gate 303, login page, rendered cards + read-only banner, **XSS subject escaped**, disabled action buttons, unknown-list 404, authorized/unauthorized callback, logout, static CSS).

### Constraints honoured

- **No Gmail writes.** The pipeline the dashboard calls is the same read-only one; buttons are inert. Every page renders the DRY-RUN banner.
- **Untrusted input (§16).** All email-sourced text is HTML-escaped at the one render choke point.
- **No multi-tenant (§13).** One authorized account; a config allowlist is the only seam.
- **Cost (§17).** Dashboard renders are AI-free and don't download attachments.

---

## Phase 7 deliverables

### New package (`app/followup/`)

- `businessdays.py` — the calendar. `holidays(year)` computes **US federal + Kenya public holidays programmatically** (CLAUDE.md §3 — no hard-coded year tables). Floating holidays via `_nth_weekday`/`_last_weekday`; Easter via the Anonymous Gregorian algorithm (drives Kenya's Good Friday / Easter Monday); US observance (Sat→Fri, Sun→Mon) and Kenya observance (Sun→Mon, cascading). Generated for `year-1…year+1` and filtered to the target year so a New-Year-observed-on-31-Dec spills correctly. `is_business_day`, `add_business_days`, `business_days_between` (half-open `(start, end]`), `is_overdue_by`. `FOLLOWUP_BUSINESS_DAYS = 3`.
- `models.py` — `FollowUpKind` (DUE_SOON / OVERDUE_DEADLINE / WAITING_FOR_REPLY / OVERDUE_ACTION), `FollowUpItem`, `FollowUpReport`.
- `deadlines.py` — `deadline_status` (business-day `overdue`/`due_soon`/`upcoming`), `refine` (copy with recomputed status), `followups` (deadlines → due-soon/overdue items; informational renewals aren't chased).
- `threads.py` — `expects_reply` (question/request cue, not a closing, has a recipient, not a broadcast) and `evaluate_thread` (Waiting for Reply / Overdue Action off the thread's most-recent message).
- `service.py` — `evaluate` (deadlines + per-thread), `evaluate_from_results` (over pipeline `PreviewResult`s), `refine_report` (sharpens an `IntelligenceReport`'s deadline statuses in place).

### Self-clearing by design (CLAUDE.md §13)

Both thread signals key off the **most recent message**: Waiting for Reply requires the user's message to be latest, Overdue Action requires an incoming action item to be latest. So a reply flips "latest" and the next scan simply stops emitting the item — no stored flag to reset. Tested both directions (`test_reply_clears_waiting_for_reply`, `test_user_reply_clears_overdue_action`).

### Islamic-holiday limitation

Kenya's Idd-ul-Fitr / Idd-ul-Azha are lunar and government-declared, so not computed. Effect: a timer spanning one of those two days a year may fire a day early. Documented in the module and the plain-English doc.

### Route

`POST /followup/scan?limit=&query=&persist=` — reads recent mail (read-only, `use_ai=false`), builds the Phase 6 intelligence, `refine_report`s deadline statuses, and `evaluate`s thread state. Returns the four follow-up lists + summary. `persist=true` re-records deadlines with the sharpened status (idempotent via the keyed repository). `gmail_modified: false`; 409 when disconnected. `AI/Waiting-For-Reply` is *proposed*, never applied.

### Tests

**644 passing** (was 592). New: `test_followup_businessdays.py` (22), `test_followup_threads.py` (11), `test_followup_service.py` (12), `test_routes_followup.py` (3). Route tests stay deterministic against the real `date.today()` by using relative wording ("due tomorrow" is always ≤3 business days → due_soon; "due January 1, 2020" is always overdue).

### Deferred as still-not-done

- Whole-thread fetching (the scan reasons over its window; full-thread pulls come with the dashboard/real-time phases).
- Auto-detecting a *resolved* deadline (paid bill) — needs the dashboard Keep/Resolve actions + audit log.

---

## Phase 6 deliverables

### New package (`app/intelligence/`)

A pure, side-effect-free extraction layer. **Structurally decoupled**: `test_intelligence_package_imports_neither_gmail_nor_sheets` walks every module with `ast` and fails if any imports `app.gmail`, `app.sheets`, or `googleapiclient`. Persistence receives a workbook *handle* as an argument; it never imports the Sheets client itself, so the whole package is inert by construction.

- `dates.py` — stdlib-only date extraction. ISO, US/intl numeric, month-name (with and without year), ordinals, `today`/`tomorrow`, `in N days`, weekday names. Each `ExtractedDate` carries the original wording, a confidence, and two flags: `year_was_inferred` and `is_ambiguous`. Overlapping matches are resolved longest/most-specific-first, so `September 15, 2026` wins over its `September 15` sub-match. Numeric dates without a year are **not** parsed (avoids reading `24/7` as a date); month-name dates without a year infer the nearest future year.
- `money.py` — currency+amount extraction (symbols and codes incl. `KES`/`KSh`), and **account masking**. There is no code path returning a full number: `extract_account_refs` reduces any account/card number to its last four digits, and `AccountRef` has only `last4` + `original_text`. A bare number is never money — an amount needs an adjacent currency marker.
- `deadlines.py` — pairs a date with nearby action wording (pay/respond/renew/register/interview/appointment). A date with no cue is not a deadline. `status` is `overdue`/`upcoming` on the plain calendar; business-day nuance is Phase 7.
- `financial.py` — the §7 minimum: kind, amount, currency, due date, safe (last-4) ref.
- `subscriptions.py` — service (via `senders.brand_name`), amount, billing frequency, renewal date; `suggested_review` on trial-conversion / price-rise wording. Never cancels.
- `material.py` — kind (price/fee/interest_rate/coverage/service/terms), old→new value via a strict "from X to Y" parser, effective date, action flag.
- `travel.py` — union-find trip grouping by shared thread, booking reference, or same destination within 3 days. Conservative: doubt → separate trips.
- `orders.py` — groups by order number (≥4 chars) or thread; tracks the delivery lifecycle and flags problems (→ the engine's Action-Required).
- `duplicates.py` — same-sender Jaccard (≥0.85) union-find over subject+snippet tokens. Reports groups; **never** feeds the engine, so it can't route a protected email to Review.
- `senders.py` — shared `brand_name()` helper.
- `models.py` — the frozen records + `MessageIntelligence` / `BatchIntelligence` / `IntelligenceReport` with `as_dict()` / `summary()`.
- `service.py` — `analyze_message` (per-message) + `analyze_batch` (cross-message) + `analyze` (the full pass). Reads a `Classification` only as a hint (action-required, expired); never mutates it.
- `persistence.py` — maps a report to Deadlines/Subscriptions/Trips rows and upserts them.

### First workbook writes

Phase 6 is the first phase that writes to the workbook. New repositories in `app/sheets/repository.py`: `DeadlinesRepository` (keyed on `message_id`+`normalized_date`), `SubscriptionsRepository` (keyed on `service`+`sender_domain`), `TripsRepository` (keyed on `trip_id`), all built on a shared `_KeyedTable.upsert` that finds-then-updates-or-appends. **Idempotent**: `test_persist_is_idempotent` runs a scan twice and asserts row counts don't grow. Exposed as `workbook.deadlines` / `.subscriptions` / `.trips`. Writing to Sheets is not a Gmail write — the dry-run guarantee is untouched.

### Route + pipeline wiring

- `PreviewResult` gained an `intelligence` field; `pipeline.build_intelligence(results)` runs the pass, attaches per-message intelligence for display, and returns the full report (incl. `batch`). Read-only.
- `GET /classify/preview` now includes an `intelligence` block (per-message + cross-message summary). `protected_routed_to_review` is unaffected — a test asserts intelligence doesn't mutate the classification object.
- `POST /intelligence/scan?limit=&query=&persist=` — extracts and (by default) persists to the workbook. `persist=false` is preview-only. Gmail read-only; `gmail_modified: false`. 409 when disconnected.

### Constraints honoured (from CLAUDE.md §10)

- No calendar events. Deadlines surface in the preview/scan and the workbook only.
- No auto-cancellation. `suggested_review` is a hint.
- Minimum financial detail; never a full account number.
- Duplicate detection reports; it does not delete and does not override protection.
- Business-day logic deliberately deferred to Phase 7.

### Tests

**592 passing** (was 529). New: `test_intelligence_dates.py` (17), `test_intelligence_money.py` (12), `test_intelligence_extractors.py` (14), `test_intelligence_grouping.py` (12), `test_intelligence_persistence.py` (6), `test_intelligence_safety.py` (4), `test_routes_intelligence.py` (4).

### Bugs found and fixed during Phase 6

1. **A full card number leaked its BIN.** `extract_account_refs` matched the *first* four digits of `4111 1111 1111 1234` (via the "card …" cue) as well as the last four. A trailing negative lookahead `(?![\s-]?\d)` now restricts the ending-pattern to a final four-digit group, leaving the full number to the masking path that keeps only `1234`.
2. **A monetary value swallowed a trailing comma.** The material-change `_VALUE` regex used `\d[\d,]*`, so `$8,` parsed as `$8,`. Replaced with a strict thousands-aware number pattern; `$8` now.
3. **A fee change was typed as "terms".** The `fee` detector required "fee change"/"fee increase" adjacency and missed "fee is changing"; the `price` detector required "price change" and missed "price is increasing". Both loosened to the word stems.

---

## Phase 5 deliverables

### New modules (`app/attachments/`)

- `types.py` — `AttachmentKind`, the dangerous-extension blocklist, MIME map, magic-byte signatures, and every hard limit. `classify_attachment()` weighs extension + declared MIME + magic bytes and **returns the most dangerous answer** on disagreement.
- `models.py` — `ExtractionStatus` (10 outcomes, with `is_success` / `is_failure`), `ExtractedAttachment`, `AttachmentReport`, and plain-English `STATUS_EXPLANATIONS`.
- `extract.py` — the extractors: text, CSV, PDF (pypdf), DOCX (python-docx). Never raises; every failure becomes a recorded status.
- `service.py` — read-only Gmail download (`users().messages().attachments().get`) and per-message orchestration.

### The never-execute guarantee

1. **Parsers, not runtimes.** pypdf reads text objects; python-docx reads the archive's XML. Neither can run embedded JavaScript or VBA.
2. **Macro-capable formats never reach a parser** — `.docm`, `.xlsm`, `.pptm`, `.xlam`, `.xlsb`, `.dotm` are in `DANGEROUS_EXTENSIONS`.
3. **Structurally verified.** `test_attachment_code_contains_no_execution_primitives` walks the package with `ast` and fails on `import subprocess|pty|ctypes|multiprocessing|runpy`, a call to `eval|exec|compile|__import__`, or an attribute call named `system|popen|spawn|execv|execl|startfile`. AST rather than grep, so a docstring mentioning `subprocess` doesn't trip it and a real call can't hide behind one.

PDF embedded JavaScript / `/OpenAction` / `/Launch` is detected and recorded as a warning. Detected only.

### The failure-is-inert guarantee (§11)

> an attachment-processing failure must never by itself route an email to Review

Structural, not a rule to remember: attachment-bearing emails are hard-protected (§8), and the only thing extracted text can do downstream is *add* a label. Nothing reads `ExtractionStatus` and decides to hide anything. `test_an_unreadable_attachment_never_sends_an_email_to_review` covers corrupt, encrypted, bomb, executable and image cases; `test_classification_is_identical_with_and_without_a_failed_attachment` asserts the decision is unchanged.

### Limits

| Limit | Value |
|---|---|
| `MAX_ATTACHMENT_BYTES` | 10 MB (oversized files are never downloaded) |
| `MAX_EXTRACTED_CHARS` | 50,000 |
| `MAX_PDF_PAGES` | 100 |
| `MAX_CSV_ROWS` | 1,000 |
| `MAX_DOCX_PARAGRAPHS` | 5,000 |
| `MAX_ATTACHMENTS_PER_MESSAGE` | 10 |
| `MAX_UNCOMPRESSED_BYTES` / `MAX_COMPRESSION_RATIO` | 100 MB / 200:1 |

The decompression-bomb check reads the ZIP central directory's declared `file_size` totals, so it decides **without expanding the payload**.

### Classification integration

`Attachment` gained `attachment_id`, `inline_data`, `extracted_text`, `extraction_status` (and is no longer frozen). `EmailMessage.attachment_text` joins and lowercases the extracted text.

`engine._assign_categories` now decides `AI/Important-Document` from real evidence: explicit document wording in the headline, **or** a document attachment alongside another protected topic, **or** a document attachment whose *contents* match the important-document / financial / legal / insurance patterns. A promotional PDF still doesn't qualify — tested.

`/classify/preview` gained `attachments=true|false` and reports per-file status plus `with_attachments`, `attachments_unreadable`, `attachments_blocked`. The extracted text is never serialized.

### Dependencies

`pypdf>=5.1,<7.0` and `python-docx>=1.1,<2.0` added to main dependencies (both pure-Python parsers). Pillow was **deliberately not** added: images have no text without OCR, and image parsers are a well-known source of memory-corruption bugs, so leaving them unopened is both the honest and the safer behaviour.

### Tests

**529 passing** (was 454). New: `test_attachments_extract.py` (52), `test_attachments_service.py` (23), plus `tests/fixtures/attachments.py` which generates **real** files — actual `.docx` via python-docx, a hand-built PDF whose text pypdf can genuinely extract, and a real compressible zip bomb.

### Bugs found and fixed during Phase 5

1. **An empty file reported `not_attempted`.** `if not data` conflated `None` (download failed) with `b""` (the file is genuinely empty) — two different outcomes reported as one. Now `None` → `NOT_ATTEMPTED`, `b""` → `EMPTY`.
2. **The blocked-file explanation said the same thing twice** — "This is a program file. It was not opened, and it was not run. Note: this is a program file; it was not opened or run." The redundant warning is gone; a warning now only carries information the status doesn't, such as a program disguised under a document name.
3. **pypdf's own warnings leaked into our logs** ("EOF marker not found") for malformed PDFs, which are routine in email and already reported as `CORRUPTED`. Its logger is now pinned to ERROR.

---

## What Phase 5 explicitly does NOT do

- No Gmail writes.
- **No OCR.** Scanned PDFs and images return no text, reported as `EMPTY` / `UNSUPPORTED`.
- No `.xlsx`, `.pptx`, or legacy `.doc`. V1 is the four types in §11.
- Archives are not unpacked.
- Nothing is persisted — attachment results live for one request.
- Attachment text is **not** included in the AI prompt. `analyze_attachment()` remains a stub; the text informs the deterministic rules only.

---

## Phase 4 deliverables

### New modules (`app/ai/`)

- `schemas.py` — `AISuggestion`, the Pydantic contract. Non-taxonomy labels are dropped, free text is length-capped and control-character-stripped, confidence is normalised. `response_json_schema()` is generated from the model so the wire schema can't drift from the validator.
- `sanitize.py` — injection detection, Unicode normalisation, content neutralisation, and the delimited email block.
- `prompts.py` — `PROMPT_VERSION`, system instructions, application policy, and prompt assembly. The version string is recorded on every AI-assisted classification.
- `base.py` — `AIProvider` ABC, `AIResult`, `ProviderConfig`. Providers never raise; failures become results.
- `anthropic_provider.py` / `openai_provider.py` — the only two modules that import a vendor SDK, both lazily.
- `factory.py` — provider resolution (argument → workbook → env → default) plus `NullProvider`.
- `validator.py` — the §11 step 9 policy validator.
- `costs.py` — price table, per-call cost estimation, `CostTracker`.
- `assist.py` — the consult-or-not gate and the merge.

### The AI's authority, enforced

| AI may | AI may never |
|---|---|
| Add taxonomy labels | Route a protected email to Review |
| Raise priority | Route a P1/P2 email to Review |
| Flag action required | Lower a priority |
| Supply summary + rationale | Remove protection |
| Signal uncertainty | Apply `AI/Trash-Candidate`, `AI/Review`, or `AI/Low-Value` directly |

`AI/Review` and `AI/Low-Value` are in `FORBIDDEN_AI_LABELS` alongside `AI/Trash-Candidate` for a specific reason: they're *consequences* of the Review decision, not categories. Letting the AI assert them meant a vetoed Review still left the message labelled `AI/Review` while sitting in the Inbox. The AI now asks for Review via `review_reason`, and whether it gets it is decided by the same two vetoes the engine applies.

After merging, `validate()` re-runs `engine._assert_safety_invariants()` over the result. If the merge would violate an invariant, the AI's entire contribution is discarded and the deterministic decision is returned.

### Prompt-injection defense (§16)

Three layers, in decreasing order of how much they're relied on:

1. **Structural** — the output schema has no field meaning archive, trash, delete, send, or apply. A fully successful injection has nothing to ask for. A test asserts no field name contains those verbs.
2. **Separation** — content is wrapped in `<<<UNTRUSTED_EMAIL_CONTENT>>>` markers, with the markers themselves neutralised if they appear in the content, and the prompt states the content is data.
3. **Detection** — 14 patterns over NFKC-normalised, format-character-stripped text. A hit means the AI is never called for that message.

Only sender, display name, subject, attachment names, date and ≤4000 chars of body are sent. Recipients and headers are not.

### Cost discipline (§17)

AI is consulted only when `Classification.needs_ai` is true. Across the pipeline test corpus that's 1 message in 3, and that corpus is weighted toward hard cases. `CostTracker.avoidable_calls` counts calls a hard rule could have handled — the metric that says whether the rules engine is earning its keep.

### Model defaults updated

`claude-sonnet-4-6` → `claude-opus-5` in `settings.py`, `sheets/schema.py` DEFAULT_SETTINGS, and `.env.example`; new `ai_effort` setting defaulting to `low`. Both SDKs are optional extras (`.[anthropic]`, `.[openai]`) — the app boots and the suite runs with neither installed.

### Tests

**454 passing** (was 332). New: `test_ai_schemas.py` (29), `test_ai_validator.py` (32), `test_ai_sanitize.py` (25), `test_ai_providers.py` (23), `test_ai_pipeline.py` (13), plus `tests/fixtures/fake_ai.py`.

Two structural tests worth noting: `test_no_vendor_sdk_is_imported_outside_its_own_provider_module` walks every file under `app/` and fails if `anthropic` or `openai` is imported anywhere but its own provider; `test_a_hostile_ai_cannot_hide_protected_email` runs a 9-case protected corpus against an AI instructed to bury everything at confidence 1.0.

### Bugs found and fixed during Phase 4

1. **A vetoed Review left the `AI/Review` label attached**, producing a message labelled Review while staying in the Inbox. Fixed by treating Review/Low-Value as decision outcomes the AI can't assert.
2. **Confidence `1.5` became `0.015`.** The 0-100 percentage tolerance divided any out-of-range value by 100, so a slightly-malformed 0-1 value read as near-zero confidence. Now: 0–1 used as given, 2–100 read as a percentage, everything else → 0.0 (unknown), which is the safe direction since low confidence routes to Review rather than granting authority.
3. **Zero-width character stripping defeated the injection scan.** Removing the zero-width spaces in `Ignore​all​previous​instructions` left the words run together, and the patterns required `\s+`. Now the normaliser strips all Unicode `Cf` category characters and the patterns use `\s*`.

---

## What Phase 4 explicitly does NOT do

- No Gmail writes.
- Nothing is persisted — AI results and costs live for one request; the audit log is Phase 9.
- Deadline/amount fields exist on the schema but nothing consumes them until Phase 6.
- `analyze_attachment()` is a stub returning "arrives in Phase 5".
- The OpenAI price entries are indicative and unverified; the Anthropic figures were checked against current pricing (2026-06-24).

---

## Phase 3 deliverables

### New modules (`app/classification/`)

- `labels.py` — the 18 `AI/*` labels, `Priority` (P1/P2/P3), and a per-label `LabelPolicy` (inbox / archive / important / internal-only). `combine_policies()` resolves multi-label conflicts toward visibility: any label wanting the Inbox wins. `gmail_labels()` strips internal-only labels so `AI/Trash-Candidate` can never reach Gmail.
- `message.py` — `EmailMessage`, the provider-agnostic model, plus `from_gmail()` / `from_gmail_thread()`. Handles metadata *and* full formats, prefers `text/plain`, falls back to stripped HTML, extracts attachment metadata, tolerates malformed base64 and unparseable dates. Body text capped at 20k chars.
- `patterns.py` — `PatternSet` and the keyword sets. Boundaries are applied **per phrase**, not around the alternation, so `% off` matches inside `50% off`.
- `signals.py` — bulk/list-header detection, Substack detection (domain *and* List-Id, so custom domains work), promotional/social signals, robot senders, and a suspicion score (threshold 3).
- `context.py` — `ClassificationContext` and `build_rule()`, which refuses domain rules on public mailbox providers.
- `protection.py` — the §8 protection evaluation and the security override.
- `engine.py` — the §11 pipeline, `Classification`, and `_assert_safety_invariants()`.
- `pipeline.py` — the only module that touches Gmail, Sheets and Contacts together. Degrades gracefully: a missing workbook or Contacts failure thins the context rather than failing the run (the safe direction — less is eligible for Review, never more).

### Safety properties, enforced structurally

| Invariant | How it's guaranteed |
|---|---|
| A protected email is never routed to Review | Protection is computed before the Review branch and vetoes it. Not a keyword contest. |
| A P1/P2 email is never routed to Review | Second veto, after the protection veto. Catches priority earned from something that isn't a protected topic (e.g. a price increase). |
| A P1 email is never archived | Placement override after policy combination. |
| `AI/Trash-Candidate` never reaches Gmail | `applied_to_gmail=False` in its policy; filtered by `gmail_labels()`. |
| Review always means archive, never delete | No delete/trash concept exists on `Classification` at all. |

`_assert_safety_invariants()` re-checks the engine's own output and raises on violation. A crash in a dry run beats a hidden email.

Both vetoes carve out `signals.is_suspicious`, so phishing is still caught (§7: security may override relationship protection).

### Read-only route

`GET /classify/preview?limit=N&query=…&contacts=bool&rules=bool` — classifies recent mail and reports proposed decisions plus a summary carrying `protected_routed_to_review`, the §15 launch-gate metric. Capped at 50 messages. Response asserts `gmail_modified: false`. The serialized view deliberately omits body text.

`GmailReadClient` gained `get_message()` and `list_recent_messages()` (format `full`). Still no mutation methods — the existing test asserting that still passes.

### Tests

**332 passing** (was 121). New: `test_classification_message.py` (19), `test_classification_signals.py` (25), `test_classification_protection.py` (35), `test_classification_engine.py` (45), `test_classification_precedence.py` (74), `test_classification_pipeline.py` (14). `tests/fixtures/emails.py` provides message builders and raw Gmail resources.

The important one is `test_no_protected_email_is_ever_routed_to_review`: a 26-case stratified corpus (financial, tax, government, legal, insurance, medical, receipts, delivery, travel, calendar, security, education, career, attachments, Substack, contacts, prior correspondents, VIP, active threads, starred), run twice — once plain, once with bulk headers on every message, since real receipts and statements do carry them. 52 assertions, all passing. Paired with `test_review_candidates_still_get_reviewed` so the gate can't be satisfied by simply never reviewing anything.

### Bugs found and fixed during Phase 3

1. **`% off` never matched.** `PatternSet` wrapped the whole alternation in `(?<!\w)…(?!\w)`, so any phrase starting with a non-word character failed after a digit. Fixed by applying boundaries per phrase.
2. **Substack was routed to Review.** It carries `List-Unsubscribe` like every mass mailing, so the generic bulk rule caught it. §9 explicitly says bulk must not override Substack — fixed in `protection.py`, recorded as a reason not a topic so impersonation can still be caught.
3. **`AI/Newsletter` was applied to bank statements and receipts**, which carry `List-Id` too. Now applied only when nothing more specific matched, or when it's genuinely Substack.
4. **`AI/Newsletter`'s policy forced the Inbox**, pulling course material out of its own filing. The label is now neutral; the engine keeps Substack and whitelisted senders in the Inbox explicitly.
5. **A P2 price-increase notice was routed to Review.** Material change earns P2 but isn't a protected *topic*. Fixed with the P1/P2 veto above.
6. **Cancelled flights were archived.** Travel bookings normally archive; P1 now overrides placement.
7. **An attached PDF inflated confidence.** `attachment` is excluded from the confidence-boosting topic check — it's a strong reason to protect but says nothing about what the message is.
8. **"No visible recipient" alone marked mail as bulk.** Now only corroborates other bulk evidence.

---

## Phase 2 deliverables

### New modules

- `app/google_api.py` — shared authenticated-service construction. `build_service(api, version, stored=None)` loads the encrypted token, builds `Credentials`, and re-saves the token when `google-auth` refreshes the access token mid-call. Introduces `NotConnectedError(FileNotFoundError)` so existing Phase 1 callers that catch `FileNotFoundError` keep working. `app/gmail/client.py` and `app/gmail/people.py` were refactored onto it (the refresh-and-resave logic previously existed in two places and is now in one).
- `app/sheets/scopes.py` — Phase 2 scopes: `spreadsheets` + the narrow `drive.file`. Full-Drive scopes are never requested.
- `app/oauth_scopes.py` — aggregates every phase's scopes into `ACTIVE_SCOPES`, the single source of truth for the consent screen. `missing_from()` powers reconnect detection.
- `app/sheets/schema.py` — all 11 tabs from CLAUDE.md §12 as frozen dataclasses, plus `DEFAULT_SETTINGS`. `validate_schema()` runs at import and raises on duplicate tab/column names.
- `app/sheets/client.py` — `get_sheets_service()` (Sheets v4) and `get_drive_service()` (Drive v3).
- `app/sheets/workbook.py` — `ensure_workbook()`: find-or-create, add missing tabs, additive column reconciliation, settings seeding, header styling. Idempotent.
- `app/sheets/repository.py` — `ControlWorkbook` → `SheetTable` → `SettingsRepository` / `RulesRepository` / `VIPRepository`. Columns addressed by name; 30s TTL read cache invalidated on write.

### Workbook discovery

Resolution order in `ensure_workbook()`:

1. Explicit `spreadsheet_id` argument.
2. `SHEETS_WORKBOOK_ID` env var.
3. Drive search for `name = "Gmail Agent Control Workbook"`, spreadsheet MIME, not trashed.
4. Create a new one.

Drive search rather than local state is deliberate — Render's filesystem is ephemeral, so a locally-cached ID would be lost on restart. Under `drive.file`, the listing can only ever return files this app created.

### Drift policy (additive only)

- Missing tab → created with a header row.
- Schema gained a column → appended to the **end** of the existing header.
- Column present in the sheet but not in the schema → left alone.
- Columns are never renamed, reordered, or removed; the repository addresses them by name, so a user reordering columns is a no-op for the app.
- `Settings` is seeded only when it has zero data rows, and seeded rows are laid out to match the sheet's *actual* header order, not the schema's.

### New / updated routes (`app/main.py`)

- `GET /health` — now `phase: 2`, adds `reconnect_required`.
- `GET /` — consent list now renders `ACTIVE_SCOPES` (was Phase 1 only, so the Sheets scopes would not have been shown). Renders a reconnect banner and the workbook button.
- `GET /sheets/status` — connected / reconnect_required / initialized / workbook_id / workbook_url.
- `POST /sheets/init` — runs `ensure_workbook()`, reports created / tabs_created / columns_added / settings_seeded / changed.
- `GET /sheets/settings` — reads the Settings tab back through the repository.

Routes that touch Sheets reject a pre-Phase-2 token with **409** and an actionable message rather than failing inside a Google call.

### Dependencies

None added. `google-api-python-client` already covers Sheets v4 and Drive v3.

### Tests

**121 passing** (was 44). New files:

- `tests/fixtures/fake_sheets.py` — in-memory Sheets v4 + Drive v3 fakes. Reproduces two real API behaviours that break naive code: trailing empty cells/rows are trimmed on read, and `values().append` lands after the last non-empty row.
- `tests/unit/test_oauth_scopes.py` — `ACTIVE_SCOPES` equals the sum of registered phases; no Gmail write scope; no full-Drive scope; every scope documented; stale-grant detection.
- `tests/unit/test_sheets_schema.py` — required tabs present, duplicate detection, audit-log columns sufficient for Undo, safe defaults.
- `tests/unit/test_sheets_workbook.py` — creation, headers, seeding, idempotency (no duplicate workbook / rows), tab recreation, additive columns preserving user order, seeding by header name, existing rows never overwritten, Drive query shape.
- `tests/unit/test_sheets_repository.py` — settings round-trips and typed getters, rule filtering/normalisation, suggestions land as `pending`, VIP approval gating, column-reorder tolerance, short-row padding, blank-row skipping, row numbering, cache hit/invalidate/expiry, guard rails.
- `tests/unit/test_routes_sheets.py` — status/init/settings routes, stale-grant 409s, idempotency through HTTP, landing-page states.

### Bug found and fixed during Phase 2

`log.info(..., extra={"created": ...})` in `ensure_workbook()` raised `KeyError: "Attempt to overwrite 'created' in LogRecord"`. `created` is a reserved `LogRecord` attribute. It was invisible in isolated test runs because the root logger sits at WARNING by default, so `log.info` short-circuits before building the record — it only fired once another test had called `configure_logging()`. In production, where logging is configured at INFO, it would have crashed every workbook init.

Fixed by renaming the key to `workbook_created`, and by adding an autouse fixture in `tests/conftest.py` that forces the root logger to INFO for every test, so this class of bug can never hide again.

---

## What Phase 3 explicitly does NOT do

- No Gmail writes. `GmailReadClient` still has no mutation methods.
- No AI. Unresolved messages are flagged `needs_ai` and left alone.
- No extraction of deadlines, amounts, trips or subscriptions — labels exist, extraction is Phase 6.
- No persistence of decisions. Nothing is written to the workbook yet; the audit log lands in Phase 9.
- "Prior correspondent" is approximated by the Contacts list; real reply history needs Phase 9.

## What Phase 2 explicitly does NOT do

- No Gmail writes. Gmail access is still `gmail.readonly`.
- Nothing *reads* the Settings values yet — the rules engine arrives in Phase 3. Editing `dry_run` today changes no behaviour.
- No dashboard beyond the landing page and three JSON endpoints.
- No audit rows are written yet; the tab exists but stays empty until Phase 9.

## Known limitations to revisit later

- **Token durability on Render**: `oauth_tokens/` is still a local file and Render's filesystem is ephemeral. Deferred to Phase 16. (Considered storing the encrypted blob in the workbook this phase; rejected — it puts a decryptable secret in a document the user shares and edits by hand.)
- **Sheets API quotas**: reads are cached for 30s per tab, which is enough for a single-user workload but not tuned. Phase 15 (12-month cleanup) should batch reads and revisit the TTL.
- **Single-user only**: one token file, one workbook, per process.
- **Duplicate workbooks**: if two files share the name, the first Drive result wins and a warning is logged. `SHEETS_WORKBOOK_ID` is the escape hatch.

## Next phase

**Phase 13 — near-real-time processing** (CLAUDE.md §13, §14): the first phase where the app acts on its own, rather than waiting for a manual `/gmail/apply` call or a dashboard click.

- **New-message triggering.** Either Gmail push notifications (Cloud Pub/Sub, the CLAUDE.md-preferred option) or polling as the documented fallback — either way, a mechanism that notices new mail without the user asking.
- **Idempotent, with no duplicate actions.** Re-processing the same message (a retry, an overlapping poll window) must never double-apply a label or re-archive something already archived — `apply.py`'s `plan_change` already produces an empty plan when nothing needs to change, which is most of this guarantee for free; the new work is making sure the *triggering* layer doesn't re-submit the same message redundantly in the first place.
- **Retry transient failures, log failures that don't resolve.** A Sheets 429 or a Gmail 5xx shouldn't lose a message's processing — but also shouldn't retry forever. Phase 10's own quota bugs (batched writes, cached headers) are the concrete lesson here: real-time processing at any real cadence will hit the same class of quota wall a lot faster than a one-off batch run did.
- **Thread re-evaluation on state change** — a reply arrives, the other party replies, a deadline passes, a manual dashboard correction happens, a rule gets approved. CLAUDE.md names these explicitly; each is a trigger to re-run classification on a thread, not just new mail.
- **Still gated the same way.** New-mail processing is a write path like any other — `check_write_gate` applies unchanged. The open question flagged at the end of Phase 12 (below) is specifically about this phase.

What Phase 13 draws from, all already in place:

- **`app.gmail.apply`** (Phase 11) — `check_write_gate`, `plan_change`, `apply_to_message` are already exactly the "decide what changed, apply it once" primitives a real-time loop needs; no new Gmail-facing logic should be required, only a trigger and a loop around what already exists.
- **`write_service.apply_recent`** (Phase 11) is close to a batch version of what real-time processing needs per-message — worth deciding whether Phase 13 reuses it directly (over a Pub/Sub-delivered message ID) or the two diverge because streaming and batch have different failure/retry shapes.
- **The Undo mechanism** (Phase 12) — real-time processing will make far more individual runs than manual testing did; Undo needs to keep working at that volume, and `System_Runs.latest_undoable()`'s "most recent" semantics are worth re-checking once runs are frequent rather than occasional.

Constraints carried in:

- **No duplicate Gmail actions** (§13) — idempotency isn't optional at this phase the way it was a nice-to-have before; a bug here means real Gmail gets modified repeatedly, not just observed repeatedly.
- **Err toward preserving important email** (§21) — a real-time loop that's more aggressive than the manual tools it's built on would be a regression, not an improvement.
- **The write gate applies with no exceptions** (§21, resolved explicitly in Phase 12) — real-time processing doesn't get to skip DRY_RUN/GMAIL_PROCESSING_ENABLED/acceptance-passed just because it's "automatic."

Open items still to confirm with the user, carried forward from earlier phases and still unresolved:

- **Duplicates are reported, not wired into the engine** (§9). Conservative choice to protect the launch gate; revisit if duplicates should actually nudge Review.
- **Expired protected mail stays filed.** `is_expired` is an observation; the protection veto keeps a completed-delivery notice out of Review.
- **Ambiguous numeric dates default to US (MM/DD)**, flagged low-confidence. The user has Kenya (DD/MM) context — worth confirming.
- **The VIP correspondence-suggestion thresholds** (§8: 3 messages in-window, or one star, or one active thread) are a first-pass heuristic with no tuning data yet — worth revisiting now that a real 250-email run has actually happened.
- **The stratified-sample search queries are a best-effort approximation** (documented in `app/acceptance/strata.py`) — `personal`/`work` share a query, and `active_threads` has none at all.
- **The write gate treats a past acceptance PASS as good indefinitely** — no expiry, no re-check against how much the mailbox or rules have drifted since. Carried from Phase 11; now sharper, since Phase 13 is exactly the phase where a stale pass would matter most — a rule change six months ago plus an acceptance run from before it would leave real-time processing running on an assumption nobody re-verified.
