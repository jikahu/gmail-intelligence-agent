# Phase 6 — Intelligence features (plain English)

## The one sentence that matters

> **The app now reads the useful facts out of your mail — deadlines, amounts, subscriptions, trips, orders — but it still doesn't touch a single email.**

This phase is about *understanding*, not *acting*. It pulls structured facts out of messages you already have and writes some of them into your control spreadsheet so you can see them in one place. Your Gmail is not changed in any way.

## What was built

The app can now notice the things that are easy to miss in a busy inbox:

- **Deadlines.** "Payment due by 20 September", "respond by Friday", "renews on the 1st" — the app finds the date, works out what it means as a real calendar date, and records what you're being asked to do.
- **Money.** It reads amounts and currencies — `$342.50`, `KES 5,000`, `£49.99` — and, on bills and statements, the due date and the *last four digits* of an account. Never more than four. (More on that below.)
- **Subscriptions.** Netflix, your gym, that SaaS tool — it spots recurring charges, how often they bill, and when they next renew. When a free trial is about to turn into a paid plan, it flags it for your review. It never cancels anything.
- **Trips.** A flight confirmation and a hotel booking for the same city and dates get grouped into one *trip*, so you see "Boston, 10–12 September" instead of four scattered emails.
- **Orders and deliveries.** The order confirmation, the "it shipped" and the "out for delivery" emails get tied together by order number, and a delivery *problem* (delayed, failed, action needed) is flagged.
- **Material changes.** When a bank raises a fee or a service raises its price, it summarises what changed — old value, new value, effective date.
- **Duplicates.** If the same message arrives twice, it notices. (This only ever makes something *more* likely to be low-value — it never hides or deletes anything.)
- **Expired items.** Old codes, past events, finished deliveries get marked as expired.

Three tabs in your spreadsheet — **Deadlines**, **Subscriptions**, **Trips** — start filling up. They were empty until now.

## The one sentence about your account numbers

> **The app only ever keeps the last four digits of an account or card number.**

This isn't a setting or a preference — it's built into the code. There is no place in the system that stores a full account number. Even when an email contains one in the clear (`4111 1111 1111 1234`), the app reduces it to `1234` before anything else sees it. There's a test that feeds it full card numbers and checks that only four digits ever come out, and another that checks the middle digits of a card never land in your spreadsheet.

## What it does *not* do

- **It doesn't change your Gmail.** No labels applied, nothing archived, nothing moved. That's still Phase 11.
- **It doesn't create calendar events.** Deadlines show up in your dashboard and spreadsheet; nothing is added to your calendar. (The spec is explicit about this for V1.)
- **It doesn't cancel subscriptions.** It can *suggest* you review one. Cancelling is always your call.
- **It doesn't decide "due soon" or "overdue" by business days yet.** For now, a date that's already passed is "overdue" and one in the future is "upcoming", counted on the plain calendar. The proper version — skipping weekends and US/Kenya public holidays — is Phase 7.
- **It doesn't change any classification.** The intelligence layer only *observes*. It can't move an email to Review, can't lower a priority, can't override protection. There's a test that runs a classification, runs the intelligence pass over it, and confirms the decision is the exact same object, untouched.

## How the dates work

The app reads dates the way you would:

| What the email says | What the app records | How sure |
|---|---|---|
| `2026-09-15` | 15 September 2026 | very |
| `September 15, 2026` | 15 September 2026 | very |
| `respond by 9/15/2026` | 15 September 2026 | high |
| `September 15` (no year) | the *next* 15 September | medium — the year was guessed |
| `due tomorrow` | the day after the email was sent | high |
| `reply within 5 days` | five days after the email | high |
| `03/04/2026` | 3 April 2026, **flagged as ambiguous** | low |

That last row is worth explaining. `03/04/2026` means March 4th in the US and 4th March in most of the world. The app can't know which, so it makes the common (US) reading, **marks it as ambiguous, and lowers its confidence** so nothing important leans on a guess. When the numbers make it unambiguous — like `25/12/2026`, which can only be 25 December — there's no guessing.

Dates without a year are read as the *nearest future* one, because deadlines and renewals point forward. In an August email, "September 15" means this coming September; "March 1" means next March.

## How trips get grouped

Two travel emails join the same trip only when there's real evidence they belong together: they're in the same email thread, they share a booking reference, or they name the same destination within a few days of each other. When there's any doubt, they stay as separate trips — a wrongly-merged trip ("your Boston and Tokyo trip") is more misleading than two correct ones. This is best-effort, and it's honest about that.

## Where things get written

Recording a deadline in a spreadsheet is **not** a change to your mailbox — the two are completely separate. Writing to the workbook is the app's normal way of remembering what it learned, and it goes through the same safe repository layer built in Phase 2.

It's also **idempotent**, which is a technical word for a simple promise: *running a scan twice doesn't create duplicate rows.* Each row has a stable key (a message and its date, a service name, a trip), so a second run updates the existing row instead of piling up copies.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should say 592 passed
uvicorn app.main:app --reload --port 8000
```

Then:

1. **Look at the new `intelligence` block** on `/classify/preview?limit=25`. Alongside each message you'll now see any deadlines, money, subscription or material change the app found in it. At the top there's a summary: how many deadlines, subscriptions, trips, orders, duplicates.
2. **Confirm nothing changed in Gmail.** The response still says `gmail_modified: false`, and `protected_routed_to_review` is still **0**. Intelligence must not move that number.
3. **Find a bill or statement** and check the money block: amount, currency, due date, and an account reference shown as `••••1234` — four digits, never more.
4. **Find a subscription email** (a renewal notice, a receipt from a monthly service) and check it shows the service, the amount, how often it bills, and the renewal date.
5. **Record it.** Call `POST /intelligence/scan?limit=25` — this reads your recent mail and writes the deadlines, subscriptions and trips into your control spreadsheet. Open the sheet; those three tabs should now have rows.
6. **Run the same scan again.** The row counts should *not* grow — it updates, it doesn't duplicate.
7. **Preview without writing:** `POST /intelligence/scan?limit=25&persist=false` shows exactly what *would* be written, and writes nothing.

## What could go wrong

- **A date came out wrong on an ambiguous email** — check whether it was a numeric date like `03/04`. Those are flagged as ambiguous with low confidence for exactly this reason. A written-out month ("4 March") is never ambiguous.
- **A trip that should be one is showing as two** — the two emails probably shared no thread, no booking reference, and didn't name the destination the same way. The app groups conservatively on purpose. Tell me which emails and I can look at the wording.
- **A subscription's amount is blank** — the email didn't state a clear price near recurring wording (some renewal reminders don't). The subscription is still recorded; the amount is just unknown.
- **`/intelligence/scan` returned a 409** — you're not connected, or the control workbook hasn't been set up. Connect at `/oauth/start` and create the workbook first.
- **A deadline looks like a plain date that isn't really a deadline** — the app only records a date as a deadline when action wording ("due", "respond by", "renew") sits next to it, so this should be rare. If it happens, send me the subject line.
- **A number I expected as money wasn't picked up** — the app only reads an amount when a currency symbol or code sits right next to it. A bare "500" with no `$`/`KES`/`USD` is deliberately ignored, so order numbers and years don't get read as money.

## How to undo it

There's nothing to undo in Gmail — nothing there was touched. If you want to clear the intelligence the app recorded, delete the rows in the **Deadlines**, **Subscriptions** and **Trips** tabs of your spreadsheet; the next scan will simply re-create the current ones. A full "Undo Last Run" for *Gmail* actions arrives in Phase 12, once there are Gmail actions to undo.

## What success looks like

- `pytest` reports **592 passed**.
- `/classify/preview` shows an `intelligence` block with deadlines, money, subscriptions and cross-message groupings.
- `protected_routed_to_review` is still **0** — intelligence changed no classification.
- `POST /intelligence/scan` fills the Deadlines, Subscriptions and Trips tabs, and running it twice doesn't duplicate rows.
- Account references are never longer than four digits, anywhere.
- Trips group a flight and a hotel for the same city and dates into one entry.
- A delivery problem is flagged; a normal delivery isn't.

## Short definitions

- **Normalize (a date)** — turn "next Friday" or "Sept 15" into an exact calendar date like `2026-09-15`.
- **Confidence** — how sure the app is about a fact it extracted, from 0 to 1. Ambiguous or guessed values get low confidence.
- **Idempotent** — running the same thing twice has the same effect as running it once; here, no duplicate spreadsheet rows.
- **Material change** — a change to price, fees, coverage or terms that's easy to miss but worth knowing about.
- **Business day** — a weekday that isn't a public holiday. The app doesn't reason in business days *yet* — that's Phase 7.
- **Account reference** — the last four digits of an account or card, kept as a safe way to tell accounts apart without storing the real number.

## A note on what's still missing

- **No business-day timers.** "Due soon", "overdue after 3 business days", and the US/Kenya holiday calendar are Phase 7. This phase extracts the dates; the next one reasons about them.
- **No follow-up tracking.** "You're still waiting on a reply" (Waiting for Reply) is also Phase 7, once there's date logic to hang it on.
- **Grouping is best-effort.** Trips and orders are grouped with sensible rules, but unusual senders or wording can leave related emails ungrouped. It errs toward keeping things separate rather than merging things that don't belong.
- **The dashboard is still JSON.** You're reading this in `/classify/preview` and `/intelligence/scan` responses for now. The real Command Center — cards you can click, a Review queue — is Phase 8.
- **Intelligence isn't fed back into classification.** A detected duplicate *could* nudge Review confidence, but to keep the launch-gate guarantee airtight, duplicates are reported for you to see rather than wired into the engine's decision. That's a deliberate, conservative choice.

## Next phase

**Phase 7 — Stateful follow-up.** This is where the dates start to mean something: 3-business-day timers that skip weekends and US + Kenya public holidays, "Due Soon" and "Overdue" flags on the deadlines this phase extracted, "Waiting for Reply" when you've sent something and heard nothing back, and re-checking a thread when the other person finally replies.
