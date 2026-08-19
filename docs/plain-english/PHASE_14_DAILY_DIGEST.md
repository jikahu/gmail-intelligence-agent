# Phase 14 — The daily digest (plain English)

## The one sentence that matters

> **The app now builds one summary page of everything that matters — Urgent, Important, Action Required, Overdue, Waiting for Reply, Due Soon, and Review — once a day on its own, and you can also pull it up yourself any time.**

## A decision made before building this

CLAUDE.md says the app "ships... a midnight America/New_York daily digest" but doesn't say exactly how that digest reaches you. There were two real options: a page inside the app you open in your browser, or an actual email sent to your own inbox. Sending real email needs a new Gmail permission (`gmail.send`) that this app has never asked for before — every phase up to now has been careful to request only read access and, since Phase 11, the narrower "modify" access (labels, archive, Trash). Asked directly, you chose **both, in order**: build the in-app page now, and treat real email delivery as a separate, later step once the page version has been used and reviewed. This phase builds the page. Sending an actual email is not built yet — see "What it does not do" below.

## What was built

1. **A digest page, `/dashboard/digest`.** It shows the exact same seven sections CLAUDE.md asks for, in this order: P1 Urgent, P2 Important, Action Required, Overdue, Waiting for Reply, Due Soon, and AI Review. Each row shows the sender, the subject, when it arrived, a one-line summary, why it was flagged (for Review rows) or what's happening and what's needed (for the rest), and a confidence score. It's read-only — there are no action buttons on this page; you still act on a message from the Command Center's own Review list, the same as before.
2. **It reuses what the dashboard already computes.** The digest doesn't run a second, separate analysis — it asks for the exact same Command Center data Phase 8 already builds, and just reorders and narrows it to the seven sections the digest wants (dropping VIP Suggestions and Subscription Review, which are dashboard-only). Whatever the Command Center would show you right now is exactly what the digest shows, just laid out as one page instead of nine clickable cards.
3. **A background scheduler that builds one automatically, once a day.** Every five minutes, a quiet background check asks "is it past the configured digest hour, in the configured timezone, and have we not already built today's digest?" The check itself costs nothing — it only reads your mail and records a summary once it's actually time. This is **on by default**, unlike the Phase 13 real-time loop — it never touches Gmail, never spends AI money, and the clock check itself makes no API call at all, so there wasn't the same reason to make you opt in.
4. **A record that a digest happened, in your control workbook.** A new `Digest_Log` tab gets one row per calendar day — just counts (how many P1s, how many in Review, and so on), not the full message list. The full list always comes from a fresh read the moment you open the page, the same "always current, never a stale copy" rule every other Command Center screen already follows. Building the same day's digest twice updates that one row instead of creating duplicates.
5. **A manual "build it right now" option.** `POST /digest/scan` builds today's digest immediately, whether or not the scheduler has gotten to it yet — useful for testing, or if you just want to see it without waiting for the clock. `GET /digest/status` shows whether the background scheduler is running and what it last did.
6. **Both settings you'd expect are editable in your control workbook.** `digest_timezone` and `digest_hour` already existed as Settings-tab rows since Phase 2 seeded the workbook; this phase is the first one that actually reads them. Change either in the sheet and the next digest — automatic or manual — uses the new value, no restart needed.

## Key terms, explained

> *Digest* — one page summarizing everything that currently matters, instead of nine separate clickable cards. Same underlying facts as the Command Center, different shape: built to be skimmed top to bottom in one sitting.

> *Digest_Log* — a short history in your control workbook of when digests were built and roughly what they contained (just counts). It's a receipt, not a copy of the digest itself — the digest page always shows you fresh, current data, not something read back from this log.

> *Background scheduler* — a quiet loop, similar to Phase 13's real-time loop, that wakes up every few minutes just to check the clock. Almost every wake-up does nothing at all; only once a day, right after the configured hour, does it actually build and record a digest.

## What it can and cannot change in your Gmail

- **Can:** read your recent mail to build the digest page — exactly the same read-only access the Command Center has always used.
- **Can:** write one summary row per day to your control workbook's `Digest_Log` tab. That's a spreadsheet write, not a Gmail change.
- **Cannot, at all, in this phase:** send you an email, or change anything in Gmail — no labels, no archiving, nothing. The digest is purely something you look at; acting on a message still happens from the Command Center's Review list, exactly as before.

## What happens when it runs

With everything at its default settings, five minutes or so after the app starts, and every five minutes after that, a quiet check runs in the background. On almost every check, the answer is "not time yet" and nothing else happens. Once it's past the configured hour (midnight America/New_York by default) and today's digest hasn't been built yet, it builds one, using your current mail, and records a one-line summary in `Digest_Log`. You never have to wait for that, though — `/dashboard/digest` always shows you a freshly built digest the moment you open it, whether or not the scheduler has gotten around to today's yet.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should all pass
uvicorn app.main:app --reload --port 8000
```

1. Sign in to the Command Center (`/dashboard`) and click **Today's Digest**, or go straight to `/dashboard/digest`. You should see the seven sections, in order, matching what the individual Command Center cards show you.
2. Check `GET /digest/status` — you should see `"enabled": true` and your configured `digest_hour`/`digest_timezone`.
3. Try `POST /digest/scan` from `/docs`. It should return the same seven sections as JSON and, by default, record a row in your workbook's new `Digest_Log` tab — open the sheet and check.
4. Run `POST /digest/scan` a second time the same day — the `Digest_Log` tab should still have only one row for today (updated, not duplicated).
5. In your control workbook's `Settings` tab, change `digest_hour` to a different value and run `POST /digest/scan` again — nothing else needs to change; the new value takes effect immediately.

## What could go wrong

- **Gmail isn't connected yet.** The digest page shows a plain "connect Gmail first" message instead of an error; `/digest/scan` returns a clear 409 rather than a crash.
- **Your control workbook isn't set up yet, or is briefly unreachable.** The digest page still works — it just falls back to the `.env` file's digest hour/timezone instead of the workbook's, and doesn't record a `Digest_Log` row until the workbook is reachable again.
- **The app was offline right through the digest hour.** The next background check after it comes back up still builds and records that day's digest as soon as it notices it's overdue — it does not try to reconstruct digests for days it missed entirely, since a digest only ever reflects mail *as it stands right now*, not a historical snapshot of a day gone by.

## How to undo it

Nothing here needs undoing — this phase makes no Gmail changes at all, only a read (to build the page) and a workbook summary row (to record that it happened). If you never want the background scheduler running, set `DIGEST_SCHEDULER_ENABLED=false` in `.env` and restart; the digest page still works on demand either way.

## What success looks like

- `pytest` passes.
- `/dashboard/digest` shows the seven CLAUDE.md §13 sections, in the right order, matching the Command Center's own cards.
- The background scheduler is on by default and reports its status honestly at `/digest/status`.
- Building the same day's digest twice never creates a duplicate `Digest_Log` row.
- `digest_timezone`/`digest_hour` changes in the control workbook take effect without a restart.

## What it does *not* do

- **No real email delivery yet.** This was a deliberate, discussed choice (see above) — the digest lives on a page in the app for now. Actually emailing it to you is a separate follow-up that would add a new Gmail permission (`gmail.send`) this app has never requested before.
- **No historical digest browsing.** `Digest_Log` keeps counts, not full message lists, so there's no "show me last Tuesday's digest exactly as it looked" — only today's, built fresh.
- **No digest of anything other than the seven CLAUDE.md §13 sections.** VIP Suggestions and Subscription Review stay dashboard-only, matching the spec.

## Next phase

**Phase 15 — 12-month historical cleanup.** A separate, deliberately run pass over the last 12 months of mail (not the everyday real-time or digest paths), starting with its own dry run, respecting Gmail's API limits, and keeping full Undo support.
