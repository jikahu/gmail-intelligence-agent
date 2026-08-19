# Phase 9 — Audit, feedback, and learning (plain English)

## The one sentence that matters

> **The app now writes down every decision it makes, and five of the Review queue's seven buttons actually do something — but "something" always means writing a note in your spreadsheet for you to look at, never quietly changing a rule on its own.**

## What was built

Three things:

1. **A paper trail.** Every time the app proposes a decision about an email, it can now write that decision down — what it decided, why, how confident it was — in a new **Audit_Log** sheet. Think of it as a diary the app keeps of its own reasoning, in your own words, not some hidden AI transcript.
2. **Five working buttons on the Review queue.** Keep, Review Correct, Make Sender Rule, Make Domain Rule, and Suggest VIP now actually record something when you click them. (Restore to Inbox and Trash are still greyed out — they need to touch Gmail itself, which is Phase 11.)
3. **A learning loop with a manual "yes, really" step.** When you click "Make Sender Rule" or "Make Domain Rule," the app doesn't quietly start applying a new rule. It writes a **suggestion** to your spreadsheet. You look at it, and if you agree, you flip its status to `approved`. Only then — and only when you tell the app to check — does it become a real, active rule.

## Key terms, explained

> *Audit trail* — a record of what happened and why, kept for accountability. Here it's a spreadsheet row per decision: not a security log nobody reads, but a plain-English note you can open any time.

> *Suggestion vs. rule* — a **suggestion** is the app saying "I think this sender should always be treated this way — what do you think?" A **rule** is the real thing that changes how future email from that sender gets sorted. Nothing skips straight from suggestion to rule without you saying yes.

> *Promotion* — the one-time act of turning an approved suggestion into a real rule. It's a separate step on purpose, so approving something in the spreadsheet doesn't instantly and invisibly change your mail sorting.

## Why two buttons changed their wording

In Phase 8, "Make Sender Rule" was labelled as if it took effect immediately ("Always handle this exact sender this way"). Looking more carefully at the spec's own rule — *"never silently create a permanent rule from a single correction"* — a single click on one email is exactly the situation that rule is meant to catch, whether the click is explicit or not. So Phase 9 changes the behavior (and the label) to match: both buttons now say **"Suggest…"**, and they land in the same "needs your approval" pile that VIP suggestions already used since Phase 8. Nothing about this weakens what you can do — it just adds one small, deliberate pause before a rule becomes permanent.

## What it can and cannot change in your Gmail

- **Still nothing in Gmail.** Every new button, every new route, only ever writes to your control spreadsheet. Your Inbox, your labels, your Important flags — untouched.
- **Rule suggestions can become real rules**, but only in the spreadsheet (Sender_Rules / Domain_Rules tabs), and only after you approve them there. Even then, an active rule changes how the app *proposes* to sort future mail — it still doesn't touch Gmail until Phase 11.
- **A safety net for domains stays in place.** If you try to make a domain-wide rule for something like `gmail.com` or `outlook.com`, the app refuses and tells you why — approving one person's Gmail address should never mean trusting every Gmail address on Earth.

## What happens when it runs

- **Clicking Keep or Review Correct** on a Review-queue email writes a one-line note to the spreadsheet ("kept" or "confirmed correct") and takes you right back to the list with a small green confirmation banner.
- **Clicking Make Sender Rule or Make Domain Rule** writes that same kind of note, plus a pending suggestion in a new tab. You'll see a banner confirming the suggestion was recorded — or, for a public email provider's domain, a banner explaining why it was refused.
- **Clicking Suggest VIP** proposes that sender as a VIP, exactly like before, now also logged as feedback.
- **Behind the scenes, three new triggers exist** (not on the dashboard yet — these are for you or a future scheduler to call):
  - `POST /audit/scan` — reviews a batch of recent mail and writes down what the app would decide about each one.
  - `POST /learning/suggest-vips` — looks at your recent mail for people you clearly correspond with a lot (or starred, or are mid-conversation with) and proposes them as VIPs.
  - `POST /learning/promote-suggestions` — checks the spreadsheet for any suggestion you've marked `approved`, and turns each one into a real rule.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should say 722 passed
uvicorn app.main:app --reload --port 8000
```

Then:

1. **Open the Command Center** and click into **AI Review**.
2. **Click "Keep"** on any message. You should land back on the same list with a green note near the top saying it was recorded.
3. **Click "Make Sender Rule"** on a message. Same green confirmation, and — if you check your control workbook's `Learned_Rule_Suggestions` tab — a new row waiting there with status `pending`.
4. **Try "Make Domain Rule" on a message from a Gmail/Outlook/Yahoo address** (or edit the test data to simulate one). You should get a red banner explaining the app won't create a domain-wide rule for a public provider.
5. **Approve a suggestion by hand**: open your control workbook, find a row in `Learned_Rule_Suggestions`, change its `status` cell to `approved`. Then call `POST /learning/promote-suggestions` (you can do this from the interactive API docs at `/docs`, or with `curl`/PowerShell's `Invoke-WebRequest`). Check `Sender_Rules` or `Domain_Rules` — the sender/domain should now show up there as `active`.
6. **Confirm nothing changed in Gmail** at any point — no new labels, no archived messages.

## What could go wrong

- **A banner says "Could not reach your control workbook."** The app couldn't read or write your spreadsheet just then (a connectivity hiccup, or the workbook isn't set up). Try again in a moment; nothing was lost, because nothing was written.
- **A rule suggestion doesn't seem to do anything.** That's expected until you approve it in the sheet *and* call the promotion step — suggestions are inert by design.
- **You approved a domain suggestion but it still didn't take effect.** If the domain is a public provider (gmail.com and friends), the app refuses to promote it even after approval, and logs why. Use a per-sender rule instead for that one address.
- **VIP suggestions seem to appear "out of nowhere."** They come from `POST /learning/suggest-vips`, looking at real patterns in your recent mail (how often someone writes to you, whether you've starred them, whether you're mid-conversation) — not a guess. They're still just suggestions until you approve them.

## How to undo it

- **A Review-queue click:** there's nothing to "undo" in Gmail, because nothing in Gmail changed. If you clicked the wrong button, the worst case is an extra row in `Review_Feedback` or a suggestion sitting in `Learned_Rule_Suggestions` that you simply never approve (or delete from the sheet).
- **A promoted rule:** find the row in `Sender_Rules` or `Domain_Rules` and either delete it or set its `status` to `paused`. The app checks status live, so this takes effect immediately, without a code change.
- **The real Undo Last Run** — for actual Gmail changes — is still Phase 12, because there's still nothing in Gmail to undo yet.

## What success looks like

- `pytest` reports **722 passed**.
- The Review queue's Keep, Review Correct, Make Sender Rule, Make Domain Rule, and Suggest VIP buttons all work and show a confirmation banner.
- Restore to Inbox and Trash are still visibly greyed out.
- A domain rule attempt on a public provider is refused with a clear explanation, not a silent failure.
- Approving a suggestion in the sheet and calling the promotion endpoint makes it show up as an active rule.
- Nothing in Gmail has changed, at any point.

## What it does *not* do

- **No Gmail changes**, still — that's Phase 11.
- **No automatic rule creation.** Every rule starts as a suggestion; a human approves it, and a separate, explicit step promotes it.
- **No calendar events, no auto-cancelled subscriptions, no automatic deletion** — none of that has changed from earlier phases.
- **Restore to Inbox and Trash remain inert.** They need real Gmail permission, which the app still doesn't have.
- **Promotion doesn't run automatically.** Approving something in the sheet is not itself the trigger — you (or, later, a schedule) have to call the promotion step.

## Next phase

**Phase 10 — the 250-email dry run.** The app will pull a carefully mixed sample of 250 real emails from your history — financial, security, receipts, travel, newsletters, promotions, suspicious-looking mail, and more — and show exactly what it would decide about each one, with zero changes made. The one number that matters most: **not a single protected or important email may be wrongly set aside for review.** If even one is, the app doesn't move on to Phase 11 until that's fixed.
