# Phase 10 — the 250-email dry run (plain English)

## The one sentence that matters

> **Before this app is ever allowed to touch your real Gmail, it has to prove — on 250 real, deliberately varied emails from your own mailbox — that it never mistakenly hides anything important. This phase builds that test. You're the one who runs it.**

## What was built

1. **A "grab a realistic mix" tool.** Instead of just looking at your 250 most recent emails (which would be mostly whatever you get a lot of lately), the app searches for a deliberate spread: bank statements, security alerts, government mail, personal messages, work email, job-related mail, receipts, orders, travel bookings, course material, Substack newsletters, other newsletters, promotions, automated notifications, cold sales pitches, anything with an attachment, ongoing conversations, and suspicious-looking messages. Then it tops the sample up with plain recent mail until it reaches 250.
2. **A report.** Once it's classified all 250 (read-only — nothing in Gmail changes), it shows you: how the sample broke down by category, a summary of what it decided, and — most importantly — **the full list of everything it set aside for Review**, so you can read through it yourself.
3. **The one number that matters most, front and center:** how many emails that should have been protected — a bank statement, a receipt, a security alert, anything important — got wrongly set aside for Review. **This must be zero.** If it isn't, the app tells you plainly: don't move forward until it's fixed.
4. **A stand-in you don't have to run yourself.** Because the real 250-email test needs your actual connected mailbox, it can't run automatically as part of routine testing. So there's a second thing: a small set of 25 realistic, hand-checked example emails built into the app itself, covering the same categories, that *does* run automatically every time the code is tested. It won't replace your own real test, but it means a future code change that accidentally breaks something gets caught immediately, not just when someone happens to run the real thing.

## Key terms, explained

> *Stratified sample* — "stratified" just means "deliberately mixed," the opposite of "whatever's most recent." If the app only looked at your most recent 20 emails, it might miss testing anything related to travel or job applications just because you haven't gotten one lately. This makes sure every important category gets a fair look.

> *Protected email* — anything the app's own rules recognize as important enough that it should never be quietly set aside: banking, receipts, government mail, security alerts, anything with an attachment, people you know, conversations you're part of, and more (the full list is in `CLAUDE.md` §8).

> *False Review* — when a protected or important email gets wrongly sent to the Review pile anyway. This is the single worst mistake this app can make short of actually deleting something, which it never does.

> *Golden dataset* — a small set of test emails with known, hand-checked correct answers, kept inside the app's own test suite. It's "golden" because the right answer for each one is already settled — so every time the code changes, the tests can instantly check nothing broke.

## What it can and cannot change in your Gmail

- **Still nothing.** Running the 250-email test reads your mail and writes a report — a spreadsheet row, not a Gmail change. No label, no archive, no Important flag, nothing.
- **The report explicitly says so** on the page, same as every other screen in this app.

## What happens when it runs

You'll trigger it yourself (see "what you should test" below). The app searches your mailbox for the mixed sample, classifies all 250 read-only, and builds a report: a green **PASSED** banner if nothing protected was wrongly reviewed, or a red **FAILED** banner naming exactly which messages were affected if it wasn't. Either way, the full Review list is shown underneath — because a clean pass/fail count only catches mistakes the app *already knows* to protect against. The harder, more important check is you actually reading through what it set aside, in case something looks wrong that the app's own rules didn't anticipate.

## What you should test

This phase is different from the others — the meaningful test **is** the real 250-email run, and only you (with your real, connected Gmail account) can do it.

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should say 748 passed
uvicorn app.main:app --reload --port 8000
```

Then:

1. **Make sure Gmail is connected** at http://localhost:8000/.
2. **Run the acceptance test.** The simplest way is opening `http://localhost:8000/docs` (the interactive API page), finding `POST /acceptance/run`, and running it with `target=250`. (Or, if you're comfortable with PowerShell: `Invoke-WebRequest -Method Post "http://localhost:8000/acceptance/run?target=250"`.) With AI turned on this may take a little while and cost a small amount, since it's classifying 250 real emails; you can add `&use_ai=false` to run rules-only first.
3. **Open the report.** The response includes a `dashboard_url` — open it (you'll need to be signed into the Command Center). You'll see the category breakdown, the pass/fail banner, and the full Review list.
4. **Read the Review list yourself.** Look for anything that seems like it shouldn't be there — a bill, a real security notice, mail from someone who matters to you. The banner only tells you what the app already caught; you're checking for what it might have missed entirely.
5. **If it failed:** don't proceed to connecting live writes (that's Phase 11, not built yet anyway). Note which message(s) were affected and why — the report shows the specific protection reason and Review reason for each — and that becomes the starting point for improving a rule or pattern before rerunning.
6. **If it passed:** great — that's the launch gate cleared for this run. It's worth rerunning occasionally as your mailbox and rules change, not just once.

## What could go wrong

- **The run takes a while or costs a bit.** 250 real emails, especially with AI turned on, is more work than the app usually does in one request. Use `&use_ai=false` for a fast, free first pass.
- **A category comes up short.** Gmail search can't perfectly target every category — there's no search for "an email a human would call personal," for instance. The report's category table shows what was actually pulled versus the target; a shortfall in one bucket isn't a bug, it just means your mailbox (or Gmail's search) didn't have a clean match, and the "catch-all" bucket tops up the total either way.
- **The report page says "No acceptance run yet."** The report only lives in the running server's memory — restart the server (or wait long enough that an older run pushes it out of the last-5 cache) and you'll need to run it again. The permanent record of *that* a run happened lives in your control workbook's `System_Runs` and `Audit_Log` tabs, even after the report itself is gone.
- **It says FAILED.** Stop — this is the gate doing its job. Don't move on to enabling any Gmail writes. Look at the specific messages named in the report and work out why the app didn't recognize them as protected.

## How to undo it

Nothing to undo — the run only reads your mail and writes to your control workbook (a `System_Runs` row, some `Audit_Log` rows, three `Settings` values recording whether the last run passed). No Gmail change ever happens. If you want to remove the record, you can delete those rows from the spreadsheet directly; nothing in the app depends on them existing.

## What success looks like

- `pytest` reports **748 passed** — including the 25-example golden dataset, checked automatically every time.
- You've run `POST /acceptance/run?target=250` against your real mailbox at least once.
- The report shows **PASSED** — zero protected or important emails wrongly routed to Review.
- You've personally read through the Review list from that run and nothing looks wrong.

## What it does *not* do

- **It doesn't run the real test for you.** Building the tool is this phase's job; actually running it against your mailbox is yours (Claude Code can walk you through the exact steps above, any time).
- **No Gmail changes**, still — that's Phase 11.
- **No automatic gate enforcement yet.** The app records whether the last run passed, but nothing currently *reads* that flag to block anything — because there's nothing yet that could act on Gmail to block. Phase 11 is what will actually check it.
- **No perfect stratification.** A few categories (especially "personal" vs. "work," and "active conversations") can't be cleanly targeted by Gmail search and are documented as best-effort.

## Next phase

**Phase 11 — Gmail write actions.** The first phase that can actually change anything in your Gmail: applying labels, archiving, marking Important, restoring a message to your Inbox, and — always with an explicit confirmation, never automatic — moving something to Trash. It won't start until the acceptance gate from this phase has actually passed on your real mailbox.
