# Phase 12 — Undo Last Run (plain English)

## The one sentence that matters

> **If a real Gmail change turns out to be wrong, there's now a button that puts things back the way they were — and it asks you to confirm first, exactly like Trash does.**

## What was built

1. **Every real Gmail write is now "a run."** Since Phase 11, a batch `/gmail/apply` was already grouped as one run. Now a single click of Restore or Trash on the dashboard gets the same treatment — its own run, its own record. That means Undo can reverse either kind: a big batch or one message you clicked by accident.
2. **A dedicated Undo screen.** A new "Undo Last Run" link on the Command Center home page takes you to a page that shows exactly what the most recent undoable run did — which message(s), what changed — before anything happens. Nothing is undone until you click the button on that page.
3. **Restoration, not re-classification.** Undo doesn't ask the rules engine "what would you do with this email today?" — it puts the message's labels and Inbox status back to *exactly* what they were the moment before the original change, using the record Phase 11 already kept. This matters because the rules (or your mailbox) might have changed since; Undo's only job is to reverse what this app did, not to make a fresh decision.
4. **Honest about what can and can't be recovered.** A label change or an Inbox move can always be reversed. A Trashed message can only be recovered if it's still sitting in Gmail's Trash — if you (or the 30-day clock) already emptied it, Undo says so plainly instead of pretending it worked.
5. **One run, one undo.** Once you've undone a run, it's marked as handled and won't be offered again — clicking Undo a second time finds the *next* most recent run that still needs it, if any.

## Key terms, explained

> *A "run"* — one batch of related Gmail changes, all sharing an ID. A confirmed `/gmail/apply` over 20 messages is one run of 20. A single click of "Restore to Inbox" is a run of 1. Either way, Undo works the same.

> *Restoration, not replay* — Undo doesn't re-think anything. It reads what a message's labels and Inbox status were *before* the app changed them, and sets them back to exactly that, message by message.

> *The write gate* — the same three-switch check from Phase 11 (`DRY_RUN=false`, `GMAIL_PROCESSING_ENABLED=true`, a passed acceptance run). Undo is itself a real Gmail write, so it goes through the identical check. If your `.env` has `DRY_RUN=true`, Undo refuses too — there's no special exception for "but the thing I'm undoing already happened for real."

## What it can and cannot change in your Gmail

- **Can:** put a message's labels and Inbox status back to what they were right before this app last changed them, and take a message out of Gmail's Trash (if it's still there).
- **Cannot:** undo something Gmail no longer has a record of (Trash emptied, or the message deleted some other way) — it tells you honestly instead of pretending.
- **Still requires you personally:** the Undo page always shows you what will be reversed before you click the button. A visit to the page by itself changes nothing.

## What happens when it runs

Open the Command Center and click **Undo Last Run**. If nothing is available to undo, it says so. If something is, you'll see the run's details and every affected message, with a single "Yes, undo this run" button. Click it, and each message is checked against Gmail's *current* state and restored — you'll see a result for every message: restored, already fine (nothing needed doing), or no longer recoverable.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should all pass
uvicorn app.main:app --reload --port 8000
```

1. With live writes still off (the default), click **Restore to Inbox** or **Trash** on the dashboard — you'll see the usual refusal, and nothing to undo yet.
2. When you're ready to test for real (same careful, deliberate steps as Phase 11 — flip `DRY_RUN`/`GMAIL_PROCESSING_ENABLED`, reconnect if needed, make sure your acceptance run has passed), click **Restore to Inbox** on a real message.
3. Go to the Command Center home page and click **Undo Last Run**. You should see that exact message listed.
4. Click "Yes, undo this run" and check the message in actual Gmail — it should be back exactly where it was.
5. Click **Undo Last Run** again — it should now say there's nothing to undo (or show the *next* most recent run, if you've made other changes since).
6. Try the same with a Trash action, and check the message really did come back out of Gmail's Trash.

## What could go wrong

- **Too much time has passed.** A Trashed message is only recoverable while it's still in Gmail's Trash (30 days). Past that, or if you emptied Trash yourself, Undo will tell you it's no longer recoverable rather than failing silently or claiming success.
- **Something else changed the message in the meantime.** Undo checks Gmail's *current* state before acting — if you'd already manually fixed something, it reports "already in that state" and makes no further change.
- **DRY_RUN is back on.** If you flip `DRY_RUN` back to `true` after making a real change, Undo for that change is blocked too, same as any other write, until you turn it back off.

## How to undo it

This phase *is* the undo mechanism — there's no separate "undo the undo." If an Undo itself turns out to have been a mistake, that's now its own new entry in your Audit_Log, and you'd address it the same way as any other manual correction (fix the label in Gmail directly, or use the dashboard's other actions).

## What success looks like

- `pytest` passes.
- The Command Center shows an "Undo Last Run" link.
- With nothing to undo, the page says so clearly.
- A real Restore or Trash action shows up on the Undo page, and clicking through actually reverses it in Gmail — verified by looking at the real message.
- After undoing, the same run isn't offered again.
- A Trash action past Gmail's recovery window is reported honestly as unrecoverable, not silently ignored or falsely reported as successful.

## What it does *not* do

- **No automatic or scheduled undo.** Always a deliberate click, always shown first.
- **No "redo."** Once undone, that's the end of that run's story.
- **Doesn't re-run the classifier.** It restores exactly what was there before — if you want the app's current opinion on a message, that's `/classify/preview`, not Undo.

## Next phase

**Phase 13 — near-real-time processing.** The first phase where the app watches for new mail and reacts on its own, rather than waiting for a manual `/gmail/apply` or a dashboard click. Idempotent, retries transient failures, and re-evaluates a thread when something about it changes (a reply arrives, a deadline passes, a manual correction is made) — building on the same write gate, write client, and now the Undo mechanism this and the previous phase already put in place.
