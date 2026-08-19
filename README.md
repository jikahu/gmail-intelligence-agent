# Gmail Intelligence Agent

Personal Gmail intelligence agent. Classifies with deterministic rules first, AI second. Keeps important email visible; routes low-value or uncertain email to a **Review** area (never auto-deletes). See `CLAUDE.md` for the full product spec.

> **Safety principle:** the app may organize aggressively, but it will never automatically delete an email.

## Status

**Phase 16 — Render deployment.** The app now deploys to Render's **free** plan (`render.yaml`) — no cost beyond what's already free, since this is a single-account personal project with no need for the $7/month always-on tier. That plan trades a real cost for a real limitation: a free service spins down after 15 minutes of no traffic and takes about a minute to wake back up, and its background loops (the real-time poller, the digest scheduler) are asleep along with it. The one genuine engineering problem this phase had to solve: Render wipes local disk on every redeploy, which would otherwise wipe the encrypted file that keeps the app signed into Gmail. Rather than pay for a persistent disk, the fix reuses something that already survives a redeploy for free — Render's own environment variables, the same place `GOOGLE_CLIENT_SECRET` already lived. A refresh token barely changes once issued, so `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` — shown once on the Gmail-connected confirmation page, pasted into Render's dashboard by hand — lets `app/gmail/tokens.py` rebuild its local file automatically on every boot. (A first idea — storing the token inside the Sheets control workbook instead — turned out to be circular: every Google API client in this app, Sheets included, needs a loaded token before it can connect, so the token can't live somewhere that itself requires the token to reach.) `create_app()` also now refuses to boot in production with a still-default `SESSION_SECRET`, since that value both signs dashboard sessions and encrypts the stored token. See `docs/plain-english/PHASE_16_RENDER_DEPLOYMENT.md` for the full step-by-step deploy walkthrough.

**Phase 15 — 12-month historical cleanup.** `POST /historical/start` sweeps roughly the last year of mail (`after:` a date computed by real calendar-month subtraction, not a rough day count) — a deliberate, manually-triggered pass, never something the real-time loop or the digest scheduler starts on its own. Because a year of mail could be thousands of messages, this runs as a **background task**: the request returns immediately with a run id, and `GET /historical/status` reports live progress (pages processed, messages seen/changed, errors) while `POST /historical/cancel` asks it to stop after its current page. `confirm=false` (the default) always previews — a dry run for free, the same shape Phase 11's `/gmail/apply` already established — and a confirmed run still needs the exact same three-switch write gate as every other real write. Gmail is paged 100 ids at a time (a new `GmailReadClient.list_message_ids`), so nothing tries to hold a year of mail in memory at once. A preview never writes per-message `Audit_Log` rows (CLAUDE.md asks for metrics, not a "nothing changed" row for every one of thousands of messages) — a confirmed run writes one only for a message that actually changed, and shares one run id end to end, so **Undo Last Run works on a historical sweep with zero changes to Phase 12**. One deliberate asymmetry in error handling: an ordinary per-message failure (a bad fetch, a transient Gmail hiccup) is logged and the sweep continues, but a *safety-invariant* violation — a protected email nearly routed to Review — aborts the entire sweep, extending Phase 10's "a crash beats a hidden email" philosophy to a much larger, potentially-live run. See `docs/plain-english/PHASE_15_HISTORICAL_CLEANUP.md`, including a real bug this phase caught (a route calling `asyncio.create_task` needed to be declared `async def`, not a plain `def`) and a test-harness quirk worth knowing about (Starlette's synchronous `TestClient` can't observe two truly concurrent background tasks the way a real server can).

**Phase 14 — Daily digest.** One page, `/dashboard/digest`, showing the exact seven CLAUDE.md §13 sections in order — P1, P2, Action Required, Overdue, Waiting for Reply, Due Soon, AI Review — built from the same data the Command Center already computes, not a second analysis. A background scheduler (**on by default** — a deliberate departure from Phase 13's real-time loop, since this one never touches Gmail and never spends AI money; `DIGEST_SCHEDULER_ENABLED=false` turns it off) checks the clock every five minutes and builds + records one digest per calendar day, no earlier than `digest_hour` in `digest_timezone` — both genuinely workbook-editable now via the control workbook's `Settings` tab. `POST /digest/scan` builds one immediately by hand; `GET /digest/status` shows what the scheduler last did. A product decision made up front (documented in `docs/plain-english/PHASE_14_DAILY_DIGEST.md`): the digest lives on this page for now, not in your inbox — actually emailing it would need a new `gmail.send` permission this app has never requested, and that's an explicit, separate follow-up rather than something built silently alongside this phase.

**Phase 13 — Near-real-time processing.** A background loop (off by default — `REALTIME_ENABLED=true` turns it on) polls Gmail's own change history every couple of minutes for genuinely new mail — no re-scan of your inbox, no Google Cloud Pub/Sub push setup, just a short "what changed?" call, a deliberate simplicity choice for a single-user app documented in `docs/plain-english/PHASE_13_REALTIME_PROCESSING.md`. New mail is classified with **real thread context** for the first time (a known gap since Phase 7): the whole thread is fetched in one call so active-conversation protection actually works, though only the genuinely new message is ever classified or relabelled — older messages in the same thread are never retroactively touched. The exact same three-switch write gate from Phase 11 still applies; turning the loop on is a fourth, independent switch that only controls whether it *runs*, not whether it can write. With the gate closed it still classifies and logs a proposal, so you can watch what it would do first. Transient Gmail errors retry automatically; a permanent one is logged and skipped without stopping the cycle. `POST /realtime/poll` runs one cycle by hand at any time; `GET /realtime/status` shows what the loop is doing.

**Phase 12 — Undo Last Run.** A dashboard button that reverses the most recent real Gmail write — a confirmed batch `/gmail/apply`, or a single Restore/Trash click, each of which now gets its own run so either kind can be undone. Restoration, not replay: every affected message goes back to *exactly* the labels and Inbox state it had before, using the real before/after state Phase 11 records — never through the classifier again. Same confirm-first discipline as Trash (a GET/preview page changes nothing; only the confirm button acts), and the same three-switch write gate as every other real write, with no exception for "the original write already happened." A Trashed message past Gmail's 30-day recovery window is reported honestly as unrecoverable, never silently ignored or falsely claimed as restored. See `docs/plain-english/PHASE_12_UNDO.md`.

**Phase 11 — Gmail write actions.** The first phase that can actually change your Gmail: add/remove `AI/*` labels, archive, restore to Inbox, Mark Important (add-only — never auto-removed), and a **user-confirmed Trash** (recoverable for 30 days — never a permanent delete; there's no API call this app's permissions even allow for that). Nothing writes until three independent switches all agree: `DRY_RUN=false`, `GMAIL_PROCESSING_ENABLED=true`, and a **passed** 250-email acceptance run recorded in your control workbook — the concrete, checkable gate CLAUDE.md §15 calls for. All three default to the safe state. `POST /gmail/apply` is a small, manual way to try a real write on a handful of messages, previewing by default the same way `/acceptance/run` does.

**Phase 10 — the 250-email dry run**, actually run against a live mailbox: `POST /acceptance/run?target=250` pulls a deliberately mixed sample of real mail — financial, security, government, personal, work, career, receipts, travel, education, newsletters, promotions, attachments, suspicious-looking mail, and more — classifies it, and reports the number that matters most: how many protected/important emails were wrongly routed to Review. **Zero**, on the real run. The report (`/dashboard/acceptance`) shows the full Review list too, since a clean count only catches what the app already knew to protect — a human still has to look. A 25-example **golden dataset** (`tests/golden_dataset/`) runs the same check automatically on every test run, as a permanent regression guard between real acceptance runs.

Prior phases still stand: Google OAuth Gmail + Contacts, an auto-created Sheets control workbook, the deterministic rules engine, an AI second opinion, attachment text extraction, the intelligence layer (deadlines/money/subscriptions/trips), time-aware follow-ups on 3-business-day timers that skip weekends and **US + Kenya public holidays** (computed programmatically, never hard-coded), the Command Center dashboard (nine clickable cards, locked behind Google Sign-In), and an audit trail with Review-queue feedback and learning (all seven Review actions are now live — five write to the control workbook, two now write to real Gmail; rule/VIP suggestions always need separate approval before they take effect).

Five guarantees the code enforces structurally, not by convention:

- **AI suggests; the rules engine decides.** The AI can add a label or raise a priority. It can never hide a protected email, lower a priority, or cause any Gmail action — its answer schema has no field for those.
- **Attachments are read as information, never executed.** No macros run, no embedded scripts are evaluated, and program files are refused without being opened — including ones disguised as documents. An unreadable attachment never causes an email to be hidden.
- **The intelligence layer only observes.** It never changes a classification, never touches Gmail, and only ever keeps the last four digits of an account or card number — enforced by a test, not a convention.
- **Every Gmail write — manual or automatic — passes the same gate, and Trash never skips confirmation.** Whether triggered by `/gmail/apply`, a dashboard click, the Phase 13 real-time loop, or the Phase 15 historical sweep, a change reaches Gmail only after the same three-switch gate agrees. Trash still always requires a second, separate confirmation naming the exact message first — nothing, including either automatic path, can reach it.
- **Undo restores, it doesn't re-decide.** Reversing a run puts a message back exactly where it was; it never asks the rules engine for a fresh opinion, and it never claims success on a message Gmail can no longer recover.

By default, though — `DRY_RUN=true`, `GMAIL_PROCESSING_ENABLED=false`, and `REALTIME_ENABLED=false` out of the box — the app still **cannot send, archive, label, or delete anything**, and doesn't even watch for new mail in the background. Decisions and follow-ups are computed and displayed; extracted intelligence is written only to your control spreadsheet, until you deliberately turn live writes on. The one background loop that *is* on by default is the Phase 14 digest scheduler (`DIGEST_SCHEDULER_ENABLED=true`) — it only reads mail to build a summary page and writes a once-a-day count row to your control workbook; it has no path to a Gmail write at all, so it doesn't carry the same reason to default off.

Open the dashboard at http://localhost:8000/dashboard once your account is connected.

Phase 16 was the last item on CLAUDE.md's phase plan. The V1 feature set (§19) is now essentially complete: Gmail connects securely, near-real-time processing and the 12-month sweep both work, the 250-email acceptance gate passed with zero protected emails misrouted, Review messages archive instead of deleting, the dashboard and digest both work, Undo Last Run works, and the app now deploys to Render for free with a Gmail connection that survives a redeploy. From here, further work is refinement against real-world use rather than a new numbered phase.

For per-phase progress and plain-English explainers, see [`docs/plain-english/`](docs/plain-english/) and [`docs/TECHNICAL_STATUS.md`](docs/TECHNICAL_STATUS.md).

## Requirements

- Python 3.13
- Windows / macOS / Linux

## Quick start (local development)

```powershell
# 1. Create a virtual environment (Windows PowerShell)
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install the project with dev extras
pip install -e ".[dev]"

# 3. Copy the example env file (do NOT commit .env)
Copy-Item .env.example .env

# 4. Run tests
pytest

# 5. Start the app
uvicorn app.main:app --reload --port 8000
```

macOS / Linux equivalent:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
pytest
uvicorn app.main:app --reload --port 8000
```

Visit http://localhost:8000/health — you should see `{"status": "ok", ...}`.

## Connecting your Google account

Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and a real `SESSION_SECRET` in `.env`, then open http://localhost:8000/ and click **Connect Gmail**. In the Google Cloud Console, enable the **Gmail**, **People**, **Google Sheets** and **Google Drive** APIs for your project.

Step-by-step walkthroughs live in [`docs/plain-english/`](docs/plain-english/).

## Layout

See [`CLAUDE.md` §4](CLAUDE.md) for the canonical repo layout.

## Safety defaults

- `DRY_RUN=true` — never modifies Gmail unless explicitly disabled.
- `GMAIL_PROCESSING_ENABLED=false` — no Gmail processing runs until you opt in.
- A third switch, outside your `.env`: live writes also require your last `POST /acceptance/run?target=250` to have recorded a passed result in the control workbook. All three must agree before any write path — `/gmail/apply`, a dashboard Restore/Trash click, or Undo Last Run — will touch Gmail for real. No exceptions: Undo is gated the same way even though the write it's reversing already happened.
- Gmail access is `gmail.readonly` plus, as of Phase 11, `gmail.modify` (labels, archive, Trash — never send, never a permanent delete). Drive access is limited to `drive.file` — files this app itself created. A test asserts no scope beyond that one documented write scope, and no full-Drive scope, is ever requested.
- Trash always requires a second, explicit confirmation naming the exact message — never a single click, never automatic.
- No AI keys are required to run the app at this phase.
- `DIGEST_SCHEDULER_ENABLED=true` by default — the one background loop that *is* on out of the box. It never has a path to a Gmail write (no `gmail.modify` call anywhere in `app/digest/`), so it doesn't carry `REALTIME_ENABLED`'s reasons to default off.
- The app refuses to start in production (`APP_ENV=production`) with a still-default `SESSION_SECRET` — a failed deploy with a clear error, rather than a live app signing dashboard sessions and encrypting the Gmail token with a secret published in this repo.

## Deployment

Deploys to Render's free plan via [`render.yaml`](render.yaml) — see `docs/plain-english/PHASE_16_RENDER_DEPLOYMENT.md` for the full step-by-step walkthrough (creating the Render service, setting secrets, connecting Gmail on the live site, and the one manual copy-paste step — `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` — that keeps the connection alive across redeploys without a paid persistent disk).
