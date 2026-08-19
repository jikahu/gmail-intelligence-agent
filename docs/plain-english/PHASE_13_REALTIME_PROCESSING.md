# Phase 13 — Near-real-time processing (plain English)

## The one sentence that matters

> **The app can now notice new mail on its own and handle it automatically — but only if you deliberately turn that on, and it still can't do anything to your Gmail unless the same three safety switches from Phase 11 all agree.**

## What was built

1. **A background loop that checks for new mail.** Every couple of minutes (configurable), the app asks Gmail "what's changed since I last looked?" and gets back a short list of genuinely new messages — not a re-scan of your whole inbox. This is off by default; a new setting, `REALTIME_ENABLED`, has to be explicitly turned on.
2. **Polling, not push notifications — a deliberate choice.** Gmail offers two ways to notice new mail: it can *push* a notification to a public web address the instant something arrives, or your app can *poll* — periodically ask "what's new?" This phase uses polling. The alternative (push, via a Google Cloud service called Pub/Sub) needs its own cloud project, a public web address Google can reach, proof you own that address, and a renewal step every 7 days — a lot of extra setup for a personal, single-user app on a single Render web service. Polling needs none of that and works everywhere this app already runs. The trade-off: new mail is noticed within one polling interval (a couple of minutes), not the instant it lands — an acceptable "near" in "near-real-time."
3. **Whole-thread awareness for the first time.** Every earlier phase looked at one message at a time, without knowing whether it was part of a back-and-forth conversation. When new mail arrives now, the app fetches that message's *entire* thread in one step, so it can correctly tell whether you're actively part of an ongoing conversation — one of the protections CLAUDE.md always intended (active conversations are protected from Review) but earlier phases couldn't fully apply.
4. **The same write gate as everything else, no exceptions.** Turning on the background loop does not by itself allow the app to touch Gmail. Real writes still require `DRY_RUN=false`, `GMAIL_PROCESSING_ENABLED=true`, and a passed 250-email acceptance run — identical to Phase 11. With the loop on but the gate closed, it still classifies new mail and writes a proposal to your Audit_Log, so you can watch what it *would* do before ever flipping the switches.
5. **Retries and graceful failure.** A momentary Gmail hiccup (rate limiting, a brief server error) is retried automatically with a short wait in between. Anything that isn't momentary — a message that's already gone, a genuine permission problem — is logged and skipped, and never stops the rest of that cycle or crashes the loop.
6. **A manual "run one cycle right now" button.** `POST /realtime/poll` runs exactly one cycle immediately, whether or not the background loop is on — useful for testing, or for catching up right after you turn `REALTIME_ENABLED` on. `GET /realtime/status` shows whether the loop is running and what its last cycle did.

## Key terms, explained

> *Polling* — periodically asking "anything new?" instead of waiting to be told. This app polls Gmail's own change history, which is a short list of what changed, not a full re-read of your mailbox — so a poll is cheap even on a large inbox.

> *History id* — a bookmark Gmail hands out that means "the mailbox as of this exact moment." The app remembers the last one it saw and asks Gmail for everything that happened since. If that bookmark ever becomes too old (Gmail only keeps a limited window of history), the app resets to "now" and says so plainly — it does not guess at what it might have missed.

> *Thread-aware* — knowing not just "what does this one message say" but "is this part of a conversation the user is actively having." A reply in a two-way conversation is protected differently than a first message from a stranger, and only a full-thread fetch can tell the difference reliably.

> *The write gate* — the same three-switch check from Phase 11 (`DRY_RUN=false`, `GMAIL_PROCESSING_ENABLED=true`, a passed acceptance run). Turning on real-time processing is a fourth, independent switch (`REALTIME_ENABLED`) that only controls whether the background loop *runs* — it has no power to open the write gate on its own.

> *Idempotent* — doing something twice has the same effect as doing it once. If the same message is ever seen again (an overlapping check, a retried cycle), and it's already exactly where it should be, the app makes no Gmail call and writes no log entry for it a second time.

## What it can and cannot change in your Gmail

- **Can, once the full gate is open:** the same things Phase 11 could — add or remove its own `AI/*` labels, move a message into or out of your Inbox, add the Important flag (never remove it automatically) — now applied automatically to new mail instead of only when you click a button.
- **Cannot, ever:** send email, permanently delete anything, or trash a message automatically. Trash stays a manual, confirmed dashboard action — nothing here can move a message to Trash on its own.
- **Will not touch older mail in a thread.** Fetching a whole thread is only to understand context correctly; it never means the app goes back and relabels older messages just because a new reply arrived. If you've already corrected an older message by hand, a new reply in that same thread won't fight your correction.
- **Still requires you personally, twice over:** once to turn `REALTIME_ENABLED` on, and again (separately) to open the write gate. With the loop on but the gate closed, nothing in Gmail changes — the app only logs what it would have done.

## What happens when it runs

With `REALTIME_ENABLED` off (today's default), nothing runs in the background — you can still trigger a single cycle by hand with `POST /realtime/poll` to see exactly what it would do. The very first cycle for a freshly connected account doesn't process anything at all — it just remembers "now" as the starting point, so turning this on never suddenly sweeps through everything already sitting in your mailbox. Every cycle after that looks only at mail that's genuinely arrived since the last check, classifies it (fetching its whole thread first for context), and — only if the write gate is open — applies the result to Gmail, exactly the way `/gmail/apply` does for a manual batch.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should all pass
uvicorn app.main:app --reload --port 8000
```

**With everything at its default (safe) settings:**

1. Visit `/realtime/status` — you should see `"enabled": false` and `"running": false`.
2. Try `POST /realtime/poll` from `/docs` on a connected account. The first call should say it just recorded a starting point and processed nothing. Run it again — with no new mail, it should say "nothing new since the last poll."
3. Send yourself a test email, then run `POST /realtime/poll` again. You should see it listed as processed — and since the write gate is still closed by default, the response should say it was a proposal only, not a real Gmail change. Check your Audit_Log tab for the new row.

**When you're ready to test the background loop and real writes** (optional, and worth doing deliberately):

1. Set `REALTIME_ENABLED=true` in `.env` (and the same `DRY_RUN`/`GMAIL_PROCESSING_ENABLED`/passed-acceptance-run steps from Phase 11 if you want it to actually write).
2. Restart the app and check `/realtime/status` — `"running"` should now be `true`.
3. Send yourself a test email and wait one polling interval (`REALTIME_POLL_INTERVAL_SECONDS`, 2 minutes by default). Check `/realtime/status` again — `"last_result"` and `"last_messages_processed"` should reflect it.
4. If the write gate is also open, check the message in actual Gmail — its labels should match what the app decided, with no button click from you.
5. You can turn `REALTIME_ENABLED` back to `false` (or just `DRY_RUN` back to `true`) at any time — that immediately stops further automatic changes, the same as every other safety switch in this app.

## What could go wrong

- **A long outage.** If the app is offline longer than Gmail keeps history (its retention window is limited), the bookmark it was using expires. Rather than guessing at what was missed, the app resets to "now" and logs that a gap happened — anything that arrived during the gap needs a manual catch-up (`/gmail/apply` or a fresh `/acceptance/run`), not this loop.
- **A momentary Gmail error.** Rate limits and brief server hiccups are retried automatically with a short wait; you shouldn't see these as failures at all unless Gmail is having a sustained problem.
- **A single bad message.** If one message fails for a reason that isn't momentary (already deleted, a permission problem), it's logged and skipped — it never stops the rest of that cycle, and it's never retried forever, since the app never gets handed that same change twice.

## How to undo it

Anything the background loop actually writes to Gmail goes through the exact same path as a manual `/gmail/apply` — which means it's covered by Phase 12's **Undo Last Run** the same way. Open the Command Center and click Undo Last Run to reverse the most recent automatic change, same as any other run.

## What success looks like

- `pytest` passes.
- `REALTIME_ENABLED` is off by default, and `/realtime/status` reflects that honestly.
- A manual `POST /realtime/poll` works correctly whether or not the background loop is on, and its very first call for an account never processes a backlog.
- With the write gate closed, real-time processing still classifies new mail and logs a proposal — nothing in Gmail changes.
- With the write gate open and the loop running, a real test email gets classified and labeled automatically within one polling interval, and Undo Last Run can reverse it.
- A message reprocessed a second time (by design or by accident) produces no duplicate Gmail call and no duplicate log entry.

## What it does *not* do

- **No push notifications / Google Cloud Pub/Sub.** A deliberate simplicity choice for a single-user app — see "Polling, not push" above.
- **No retroactive reclassification.** A new reply in a thread never causes the app to go back and relabel older messages in that same thread.
- **No historical catch-up.** The very first poll for an account only bookmarks "now" — a full pass over your last 12 months of mail is Phase 15's job, not this one's.

## Next phase

**Phase 14 — the daily digest.** A midnight America/New_York email summarizing P1, P2, Action Required, Overdue, Waiting for Reply, Due Soon, and the Review queue — the first phase where the app proactively tells you something, rather than waiting for you to open the dashboard.
