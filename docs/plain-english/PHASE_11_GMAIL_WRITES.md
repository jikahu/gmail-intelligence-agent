# Phase 11 — Gmail write actions (plain English)

## The one sentence that matters

> **This is the first phase where the app can actually change your Gmail — and it still can't do anything until you deliberately turn on three separate switches, one of which is the acceptance test you already passed.**

## What was built

1. **A real, write-capable connection to Gmail.** Up to now the app could only read your mail. It now has permission (once you reconnect and grant it) to add or remove its own `*` labels, move a message in or out of your Inbox, mark something Important, and move a message to Gmail's own Trash. It still cannot send mail, and there is no button anywhere — not even a hidden one — that permanently deletes anything.
2. **A single safety switch three things must all agree on.** Before any real Gmail change happens, the app checks: is `DRY_RUN` turned off? Is `GMAIL_PROCESSING_ENABLED` turned on? And did your last 250-email acceptance run (Phase 10) actually pass? If any one of those three is "no," the app refuses — with a plain-English reason — instead of silently doing nothing or guessing.
3. **A small, manual way to try it.** A new endpoint, `POST /gmail/apply`, works like Phase 10's acceptance run: point it at a handful of real messages, and by default it only shows you what it *would* do. Only when you explicitly add `confirm=true` — and only if the safety switch above agrees — does it actually change anything in Gmail.
4. **Two Command Center buttons that now really work.** "Restore to Inbox" and "Trash" were placeholders since Phase 8. Restore now really moves a message back to your Inbox. Trash now really moves a message to Gmail's Trash — but only after a second, separate confirmation screen that names the exact message and reminds you it's recoverable, not deleted. The other five buttons (Keep, Review Correct, Make Sender Rule, Make Domain Rule, Suggest VIP) are unchanged — they still only write to your control spreadsheet.
5. **A real audit trail.** Every actual Gmail change — whether triggered by the manual apply endpoint or by clicking Restore/Trash — is logged with what your message's labels and Inbox status genuinely were before and after, not a placeholder. That's the record a future "Undo Last Run" feature (Phase 12) will read from.

## Key terms, explained

> *DRY_RUN* — a setting in your `.env` file. When it's `true` (the default), the app can look at your mail and decide what it *would* do, but every actual change is skipped. Setting it to `false` is step one of three needed before anything real happens.

> *GMAIL_PROCESSING_ENABLED* — a second, separate setting. Even with `DRY_RUN` off, this must also be `true`. Two independent switches make it much harder to turn on live writes by accident.

> *The acceptance gate* — the third switch, and the only one this app checks for you automatically rather than trusting your `.env` file: it looks at whether your last 250-email test (Phase 10) recorded a genuine **PASSED**. If you've never run it, or it failed, live writes stay off no matter what your `.env` says.

> *gmail.modify scope* — the specific permission Google now asks you to grant. It allows labels, archiving, and Trash. It does **not** allow sending mail, and there is no Gmail permission this app ever requests that allows a permanent delete.

> *Idempotent* — a fancy word for "doing it twice has the same effect as doing it once." If a message is already exactly where it should be, the app makes zero Gmail calls for it — it doesn't relabel something that's already correctly labeled.

## What it can and cannot change in your Gmail

- **Can:** add or remove its own `*` labels, move a message into or out of your Inbox, add the Important flag (never remove it automatically), move a message to Gmail's Trash.
- **Cannot, ever:** send email, permanently delete anything, or touch any label that isn't one of its own `*` labels, `INBOX`, or `IMPORTANT`.
- **Still requires you personally:** every Trash action requires you to click through a page that names the exact message first. Nothing is ever trashed from a single click, and nothing is ever trashed automatically by the classifier.

## What happens when it runs

With the safety switch off (today's default), nothing changes — `/gmail/apply` behaves exactly like the read-only preview you already know from Phase 3, and Restore/Trash on the dashboard give you a clear "can't do that yet, here's why" message instead of an error. Once you turn the switch on, `/gmail/apply` with `confirm=true` looks at each message, works out the smallest set of real changes needed (skipping anything already correct), and applies it. Restore and Trash on the dashboard start actually working the moment you click them (Trash still asks you to confirm first, every time).

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should all pass
uvicorn app.main:app --reload --port 8000
```

**With the safety switch still off (the default) — confirm the refusals work:**

1. Open the Command Center's Review list and click **Restore to Inbox** on any message. You should see a message like *"Can't restore yet — DRY_RUN is true..."* — not an error page, and nothing in Gmail should change.
2. Click **Trash** on a message. You'll land on the confirmation page first — that page itself never changes anything. Click "Yes, move to Trash" and you should see the same kind of refusal message.
3. Try `POST /gmail/apply?limit=5&confirm=true` from `/docs`. The response should show `"wrote_to_gmail": false` and a `gate_reasons` list explaining why.

**When you're ready to actually test a real write** (optional, and worth doing deliberately, not by accident):

1. In `.env`, set `DRY_RUN=false` and `GMAIL_PROCESSING_ENABLED=true`.
2. Reconnect Gmail at `/oauth/start` — Google will show you the new permission (`gmail.modify`) and ask you to approve it. Nothing before this step needed the new permission.
3. Confirm `/health` now shows `"dry_run": false` and `"gmail_processing_enabled": true`.
4. Run `POST /acceptance/run?target=250` again if you haven't recently — the write gate reads that result, so it needs to be a real PASSED run.
5. Try `POST /gmail/apply?limit=3&confirm=true&use_ai=false` on a *small* number first. Check the response's `results` list — each entry shows exactly what changed.
6. Go look at those messages in actual Gmail and confirm the labels/Inbox state match what the response said.
7. Try Restore to Inbox and Trash from the dashboard on real messages you don't mind moving.
8. When you're done testing, you can set `DRY_RUN` back to `true` at any time — that immediately stops all further writes, again with no other change needed.

## What could go wrong

- **A stale connection.** If you connected Gmail before this phase, your token doesn't have the new `gmail.modify` permission yet. Any write attempt will tell you plainly to reconnect at `/oauth/start` rather than failing with a confusing Google error.
- **The acceptance run "expires."** There's no time limit today — a pass from last week still counts. If your rules or mailbox change a lot, it's worth rerunning the acceptance test occasionally, not just once.
- **A message doesn't look like you expected.** Every write is idempotent and reversible while Gmail still has the data (a label can be reapplied, Trash can be undone within Gmail's own 30-day window) — there is no failure mode here that loses a message permanently.

## How to undo it

Nothing built here undoes anything automatically yet — that's Phase 12, "Undo Last Run," which will read the before/after state this phase now records in your Audit_Log. Until then: a wrongly-added label can be removed by hand in Gmail; a wrongly-archived message can be dragged back to your Inbox; a wrongly-Trashed message can be recovered from Gmail's own Trash folder within 30 days, the same as if you'd trashed it yourself.

## What success looks like

- `pytest` passes.
- With the safety switch off, every write attempt (manual apply, Restore, Trash) refuses with a clear, specific reason — never a raw error, never a silent no-op.
- With the safety switch deliberately turned on, a small test batch through `/gmail/apply` changes exactly what it says it will, and you can verify that directly in Gmail.
- Restore to Inbox and Trash on the dashboard both work for real, and Trash always asks you to confirm the specific message first.
- Your Audit_Log tab shows genuine before/after label and Inbox state for every real change — not the "identical before/after" placeholder from earlier phases.

## What it does *not* do

- **No automatic processing yet.** Nothing runs on a schedule or watches for new mail — that's Phase 13. This phase only adds the *capability* to write, triggered by you.
- **No Undo button yet** — Phase 12.
- **No permanent delete, ever** — not in this phase, not in any future one this spec allows.

## Next phase

**Phase 12 — Undo Last Run.** Reads the before/after state this phase now records for every real Gmail write, shows you which run it would reverse and how many messages that touches, asks for confirmation, and restores the previous label/Inbox state wherever Gmail still allows it.
