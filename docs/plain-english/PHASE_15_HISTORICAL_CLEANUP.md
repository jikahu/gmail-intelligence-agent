# Phase 15 — 12-month historical cleanup (plain English)

## The one sentence that matters

> **You can now ask the app to sweep through your last 12 months of mail in one deliberate, watchable pass — it runs in the background so it doesn't block your browser, defaults to a dry run, and follows the exact same safety switches as every other real Gmail change.**

## What was built

1. **A 12-month sweep, started by hand.** `POST /historical/start` kicks off a pass over roughly the last year of mail (`after:` a specific date, computed by real calendar-month subtraction — 12 months back from today, not a rough "365 days"). This never happens automatically; the Phase 13 real-time loop and the Phase 14 digest scheduler never trigger it themselves. It's a deliberate action you take.
2. **Runs in the background, not inside your request.** A mailbox with a year of mail could have thousands of messages, which could take a long time to work through — far too long for a single web request to wait on. So this starts a background task and returns immediately; you check on it with `GET /historical/status` whenever you like, the same "started, then poll" shape as checking on a delivery.
3. **A dry run by default — CLAUDE.md's own instruction, satisfied for free.** Like every other write-capable endpoint since Phase 11, `confirm=false` (the default) always previews: it reads and classifies your mail but changes nothing in Gmail. You only get a real sweep by explicitly passing `confirm=true` — and even then, only if the same three-switch write gate from Phase 11 (`DRY_RUN=false`, `GMAIL_PROCESSING_ENABLED=true`, a passed acceptance run) agrees.
4. **Paged, not all-at-once.** The app asks Gmail for message ids a page at a time (100 by default), processes that page, then moves to the next — never trying to hold your whole year of mail in memory at once, and giving itself a natural, regular checkpoint to notice if you've asked it to stop.
5. **You can cancel it.** `POST /historical/cancel` asks a running sweep to stop after it finishes whatever page it's currently on — not instantly mid-message, but promptly.
6. **Errors on one message don't stop the sweep — except one specific kind that does, on purpose.** A message that fails to download, or a rare Gmail hiccup, is logged and skipped; the rest of the sweep keeps going. But if the app ever detects that a message it's protected from Review (banking, government, an active conversation, and so on) nearly got sent to Review anyway — a bug in the classifier itself, not a one-off — the *entire* sweep stops immediately rather than quietly continuing. CLAUDE.md's own words from Phase 10 apply here too: a crash beats a hidden email.
7. **Everything that changes is logged and undoable.** A confirmed sweep's real changes are recorded in your Audit_Log under one shared run id, and one summary row is added to System_Runs when the sweep finishes (or is cancelled, or fails). That means Undo Last Run (Phase 12) works on a historical sweep exactly the way it works on any other run — no special case needed.

## Key terms, explained

> *Sweep* — this phase's word for "work through a whole batch of mail, page by page, from start to finish," as opposed to the everyday real-time loop that only reacts to brand-new mail.

> *Page / batch* — one chunk of message ids (100 by default) fetched from Gmail at a time. Processing in pages, rather than trying to list your whole year of mail in one go, is what "batch safely" means in practice.

> *Estimated total* — a rough count Gmail itself hands back for how many messages match the search (`resultSizeEstimate`). It's an estimate, not an exact count — useful for a rough sense of progress, not a promise.

> *Safety invariant* — the app's own internal rule that a message it has decided is protected (banking, government, an active conversation, and more) must never also be marked for Review. The classifier checks this on every single message; if it's ever violated, that's a bug worth stopping everything for, not a message worth skipping.

## What it can and cannot change in your Gmail

- **Can, once confirmed and the write gate is open:** exactly what every other real write path can — add or remove its own `AI/*` labels, move a message into or out of your Inbox, add the Important flag (never remove it automatically).
- **Cannot, ever:** send email, permanently delete anything, or move anything to Trash. Trash stays a manual, confirmed, one-message-at-a-time dashboard action — nothing about a bulk historical sweep can reach it.
- **In preview mode (the default), changes nothing at all** — not Gmail, and (deliberately, to avoid piling thousands of "nothing happened" rows into your workbook) no per-message Audit_Log entries either. A preview run still gives you real counts — how many messages, how many would be flagged for Review, how many are protected — just not a message-by-message paper trail.

## What happens when it runs

You call `POST /historical/start` (optionally confirming and choosing how far back to go). The response comes back right away with a run id and "running." Behind the scenes, the app pages through roughly the last 12 months of mail, classifying each message with the same rules every other phase uses, applying real changes only if you confirmed and the gate is open, and updating its own progress as it goes. You can check `GET /historical/status` at any point to see how many pages and messages it's gotten through, how many actually changed, and how many hit an error. When it's done (or you cancel it, or something goes wrong), the status settles into `completed`, `cancelled`, or `failed`, and a summary lands in your `System_Runs` tab.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should all pass
uvicorn app.main:app --reload --port 8000
```

1. With a connected account, try `POST /historical/start` from `/docs` (leave `confirm` at its default `false`). It should return right away with `"started": true`.
2. Poll `GET /historical/status` a few times — you should see `pages_processed`, `messages_seen`, and `messages_processed` climb, and eventually `"state": "completed"`.
3. Try starting a second sweep while the first is still running (`POST /historical/start` again quickly) — it should refuse with a 409, "already in progress."
4. Try `POST /historical/cancel` partway through a run — the next status check should show `"state": "cancelled"` once the current page finishes.
5. Check your workbook's `System_Runs` tab — you should see one new row, `mode: historical`, with the final counts.
6. Only once you're comfortable with what a preview shows: repeat with `confirm=true` (and the usual `DRY_RUN=false` / `GMAIL_PROCESSING_ENABLED=true` / a passed acceptance run) and confirm real labels change in Gmail, then try Undo Last Run from the Command Center to reverse it.

## What could go wrong

- **A very large mailbox takes a while.** This is expected, not a bug — a year of mail can be thousands of messages, and each one needs its own read from Gmail. Watch progress with `/historical/status`, or cancel and try a smaller `max_messages` first if you'd rather test with a bite-sized run.
- **A message fails to download, or Gmail briefly hiccups.** Logged, counted as an error, and skipped — the sweep keeps going.
- **The app restarts mid-sweep.** The sweep doesn't survive a server restart (it's a background task in that one running process, not a durable job queue) — you'd need to start a new one. Anything it had already confirmed and applied before the restart stays applied and logged; nothing is lost or duplicated if you start a fresh sweep afterward, since a message already in its desired state produces no change and no log row the second time around.
- **A safety-invariant violation.** Extremely unlikely (it would mean a real bug in the classifier), but if it happens, the whole sweep stops immediately rather than continuing past it — check `last_error` on the status for what it found.

## How to undo it

Any real changes a confirmed sweep made are logged under one run id, exactly like a manual `/gmail/apply` batch. Open the Command Center and click **Undo Last Run** to reverse the most recent one — same confirmation, same restrictions (it needs Gmail's own data to still allow the change to be reversed) as undoing any other run.

## What success looks like

- `pytest` passes.
- `POST /historical/start` returns immediately, without waiting for the sweep to finish.
- The default (`confirm=false`) never changes Gmail, no matter how many messages it finds.
- A second `/historical/start` while one is active is refused, not queued or stacked.
- `GET /historical/status` accurately reflects progress and the final outcome.
- A confirmed run's real changes are undoable via Phase 12's Undo Last Run.
- A safety-invariant violation stops the whole sweep, not just the one message.

## What it does *not* do

- **No scheduling.** This phase doesn't run itself on a timer — it's always a deliberate action, per CLAUDE.md's "run separately from real-time processing."
- **No resuming a sweep across a server restart.** Cancel or let it finish; a fresh start begins a new pass, not a continuation of an old one.
- **No per-message historical Audit_Log trail for a preview.** A preview reports counts, not a row for every one of possibly thousands of messages that didn't change — see "What it can and cannot change" above.

## A bug found and fixed while building this

The very first version of `POST /historical/start` was a plain (non-`async`) route function calling code that needs Python's event loop to schedule a background task. That combination doesn't work — FastAPI runs a plain route in a worker thread that has no event loop of its own, so creating the background task failed immediately with `RuntimeError: no running event loop`. The fix was declaring the route `async def`, which runs it directly on the loop instead. Caught by testing the route for real (not just the underlying pieces in isolation) before calling this phase done.

## Next phase

**Phase 16 — Render deployment.** GitHub → Render configuration, environment variables, health checks, and a production startup — the last piece needed to run this app somewhere other than your own machine.
