# Phase 7 — Stateful follow-up (plain English)

## The one sentence that matters

> **The app now keeps time for you: it knows when a bill is due soon, when one is overdue, when you're still waiting on a reply, and when something has needed your attention for three working days — counting only working days.**

Phase 6 found the dates. Phase 7 is what makes them *mean* something. And it still changes nothing in your Gmail.

## What was built

Four kinds of follow-up, all recomputed fresh every time you scan:

- **Due Soon** — a deadline within the next three *business* days.
- **Overdue** — a deadline whose date has already passed.
- **Waiting for Reply** — you sent something that expected an answer, and three business days have gone by with no reply.
- **Overdue Action** — an email that needs *your* action has been sitting for three business days without you responding.

Underneath all of them is a **business-day calendar** that knows US federal holidays *and* Kenyan public holidays.

## What "three business days" really counts

A business day is a weekday that isn't a public holiday. The app skips:

- **Saturdays and Sundays.**
- **US federal holidays** — New Year's, MLK Day, Presidents' Day, Memorial Day, Juneteenth, Independence Day, Labor Day, Columbus Day, Veterans Day, Thanksgiving, Christmas — including the "observed" shuffle when one lands on a weekend (if the 4th of July is a Saturday, the Friday before is the day off).
- **Kenyan public holidays** — New Year's, Good Friday, Easter Monday, Labour Day, Madaraka Day, Huduma Day, Mashujaa Day, Jamhuri Day, Christmas, Boxing Day — with the Kenyan rule that a holiday on a Sunday is observed the following Monday.

So if you send a message on the Thursday before a long weekend, the three-business-day clock doesn't run out until the middle of the next week — the app won't nag you over a holiday.

**These holidays are calculated, never typed into a list.** The spec is firm about this: "compute US + Kenya public holidays programmatically; never hard-code year lists." That means the calendar is correct for 2026, 2027, 2035 — any year — without anyone updating a table. Even Easter (which moves every year) is computed. There's a test that checks every year from 2018 to 2035 produces a sensible set of holidays.

**One honest gap:** Kenya's two Islamic holidays (Idd-ul-Fitr and Idd-ul-Azha) follow the lunar calendar and are announced by the government close to the date, so they can't be reliably computed in advance. They're the only holidays not included. The worst this causes is a follow-up firing one day early, once or twice a year, around those dates.

## How "Waiting for Reply" decides

It only flags a message that **reasonably expected an answer**. The spec says: *don't flag messages that clearly don't need a response.* So the app looks for a question mark or a request ("could you", "let me know", "please confirm", "your thoughts?"), and it deliberately ignores:

- a plain **"Thanks!"** or **"Got it"** — a sign-off, not a question;
- a message with **no recipient**;
- a **broadcast** to a big group — that's an announcement, not a one-to-one ask.

It's tuned to stay quiet unless there's a real question hanging. Better to miss one than to nag you about a thank-you note.

## How things clear themselves

This is the "stateful" part, and it's simpler than it sounds. The follow-up list is rebuilt from scratch every time you scan. Each item asks a question about the **most recent message** in a thread:

- **Waiting for Reply** looks at whether *your* message is the latest one. The moment the other person replies, their message becomes the latest — so the next scan simply doesn't show "waiting" any more. It cleared itself.
- **Overdue Action** looks at whether an *incoming* action item is the latest. The moment you reply, *your* message is latest — so it stops showing as overdue.

There's no stored "waiting" flag that could get stuck on. There are tests for both: a thread where the other party replied shows nothing to chase, and an action item you've responded to is no longer overdue.

## A worked example

Say today is **Thursday 20 August 2026**:

| Something in your mail | What the app says | Why |
|---|---|---|
| A bill due **Friday 21 Aug** | **Due Soon** | one business day away |
| A bill due **Thursday 27 Aug** | *Upcoming* (not flagged) | five business days away — not soon yet |
| A bill due **18 Aug** | **Overdue** | the date has passed |
| You emailed a question **Monday 17 Aug**, no reply | **Waiting for Reply** | Tue, Wed, Thu = 3 business days |
| A "please sign" email arrived **Monday 17 Aug**, no response | **Overdue Action** | 3 business days, still needs you |
| You emailed a question **Tuesday 18 Aug** | *nothing yet* | only 2 business days have passed |

## What it does *not* do

- **It doesn't change your Gmail.** "Waiting for Reply" is *proposed* as a label; nothing is actually applied. Applying labels is Phase 11.
- **It doesn't send reminders or emails.** It surfaces the list; you act on it.
- **It doesn't guess about complicated threads.** It reasons from the most recent message. A tangled thread with several open questions is summarised simply, by its latest state.
- **It doesn't yet drive a dashboard.** For now you read the follow-up list as a JSON response from `/followup/scan`. The clickable Command Center is Phase 8; the midnight digest that emails you this list is Phase 14.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should say 644 passed
uvicorn app.main:app --reload --port 8000
```

Then:

1. **Call `POST /followup/scan?limit=25`.** You'll get four lists: `overdue_actions`, `overdue_deadlines`, `waiting_for_reply`, `due_soon`, plus a summary count.
2. **Check a due-soon or overdue bill** you know about lands in the right list, with the date it's keyed to.
3. **Confirm the reasons make sense** — each item explains itself ("You sent this on 17 Aug and haven't had a reply in 3 business days").
4. **Confirm nothing changed in Gmail** — the response says `gmail_modified: false`.
5. **Record the sharpened statuses:** `POST /followup/scan?limit=25&persist=true` updates the Deadlines tab in your spreadsheet so each deadline reads `due_soon`, `overdue` or `upcoming`.
6. **A holiday check:** the business-day maths is unit-tested, but if you want to see it, note that a Friday-plus-three-business-days lands the next Wednesday, and one spanning a public holiday lands a day later still.

## What could go wrong

- **A "Waiting for Reply" you expected isn't there** — your message may not have read as a question. The app needs a question mark or a clear request; a statement won't trigger it. This is deliberate caution.
- **A follow-up you'd resolved still shows** — it clears based on the *most recent message in the thread*. If your reply was in a different thread, or the scan window didn't include it, it can linger. Fetching whole threads every time is a refinement for the dashboard phase.
- **Something flagged over a holiday you don't observe** — the app counts a day as a holiday if it's a public holiday in *either* the US or Kenya, which makes timers a little more generous (they fire later). That's the safe direction — it errs toward not nagging.
- **An Islamic-holiday week is off by a day** — as noted, those two Kenyan holidays aren't computed. Tell me if this matters and we can wire in a small declared-dates list.
- **`/followup/scan` returned a 409** — you're not connected. Connect at `/oauth/start` first.

## How to undo it

Nothing to undo — no Gmail changes were made. The only thing written anywhere is the refined `status` on rows already in your Deadlines tab (and only if you passed `persist=true`); the next scan just recomputes them. The real **Undo Last Run** for Gmail actions is Phase 12, once there are Gmail actions to undo.

## What success looks like

- `pytest` reports **644 passed**.
- `POST /followup/scan` returns due-soon, overdue, waiting-for-reply and overdue-action lists.
- Timers skip weekends and both US and Kenya public holidays.
- A reply in a thread makes its "waiting" item disappear on the next scan.
- Holidays are correct for any year without a hand-maintained list.
- `gmail_modified` is still `false`.

## Short definitions

- **Business day** — a weekday that isn't a public holiday (US or Kenya). Weekends and holidays don't count toward a timer.
- **Observed holiday** — when a fixed-date holiday lands on a weekend, the day off moves (US: to the nearest weekday; Kenya: a Sunday holiday moves to Monday).
- **Due Soon** — within three business days. **Overdue** — the date has passed.
- **Waiting for Reply** — you asked something and no answer has come in three business days.
- **Overdue Action** — an email needing your action has sat three business days.
- **Stateful / re-evaluation** — the follow-up list is recomputed each scan from the thread's current state, so replies clear items automatically.

## A note on what's still missing

- **Whole-thread fetching.** The scan reasons over the messages in its window. For a thread whose relevant messages are older than the window, a proper fetch of the full thread (coming with the dashboard and real-time phases) would be more thorough.
- **No dashboard or digest yet.** These follow-ups are the exact contents of the future Command Center cards (Phase 8) and the midnight digest (Phase 14). Right now they live in a JSON response.
- **Resolved deadlines aren't auto-detected.** A deadline is "overdue" until its date passes; the app doesn't yet know you *paid* the bill. Marking things resolved comes with the dashboard's Keep/Resolve actions and the audit log (Phases 8–9).
- **Islamic holidays aren't computed** (explained above).

## Next phase

**Phase 8 — Command Center dashboard.** The first real screen: Google Sign-In, and cards you can click — P1 Urgent, Action Required, Waiting for Reply, Due Soon, Overdue, AI Review, VIP suggestions, Subscription review — each opening the list behind it. Everything Phases 3–7 have been computing finally gets a face. Still no automated Gmail changes.
