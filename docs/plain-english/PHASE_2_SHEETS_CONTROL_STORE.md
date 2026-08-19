# Phase 2 — Sheets control store (plain English)

## What was built

Phase 2 gives the app a **control panel you can edit yourself**: a Google Sheet that the app creates for you automatically.

Concretely:

- The app now asks Google for permission to use Google Sheets, and for a *narrow* Drive permission that only covers files the app itself creates.
- On request, it creates a spreadsheet in your Google Drive called **"Gmail Agent Control Workbook"**, with **11 tabs** already set up and labelled.
- It fills the `Settings` tab with safe starting values — most importantly `dry_run = true` and `gmail_processing_enabled = false`.
- It can find that workbook again later without you telling it where it is.
- A "**Create / update my control workbook**" button on the homepage.
- Three new pages: `/sheets/status` (is the workbook set up?), `/sheets/init` (create or update it), `/sheets/settings` (read back your settings).
- An internal "repository" layer — the part of the code that reads and writes rows — so the rest of the app never has to know it's talking to a spreadsheet.

## Why

Everything the agent does needs to be **something you can see and change**: which senders are VIPs, which domains are trusted, what counts as low-confidence, whether dry-run is on. Burying those in code would mean asking me to make a change every time you changed your mind.

A Google Sheet is the simplest thing that works. You already know how to use it, it's on your account, it syncs everywhere, and you can edit it from your phone. No database to run, nothing to log into.

The tabs are also where the agent will *write* its own records later — the audit log of every action it took, deadlines it spotted, rules it wants to suggest to you. So the workbook is both the settings panel and the paper trail.

## The 11 tabs

| Tab | What it's for |
|---|---|
| `Settings` | The switches. AI provider, dry-run on/off, digest hour, confidence threshold. |
| `VIPs` | People whose email always stays in your inbox. Nothing lands here without your approval. |
| `Sender_Rules` | "Always treat mail from this exact address this way." |
| `Domain_Rules` | Same, but for a whole domain (e.g. `substack.com`). |
| `Learned_Rule_Suggestions` | Rules the agent *thinks* you'd want. Every one waits for your yes or no. |
| `Review_Feedback` | A record of every correction you make in the dashboard. |
| `Audit_Log` | Every action taken, with the before/after state. This is what makes "Undo Last Run" possible later. |
| `Deadlines` | Payment due dates, respond-by dates, interviews, renewals. |
| `Subscriptions` | Recurring charges and memberships the agent notices. |
| `Trips` | Flights, hotels and reservations grouped into one trip. |
| `System_Runs` | One row per processing run. |

Most of these will sit empty until the phases that fill them (6, 7, 9, 12) are built. They exist now so nothing has to be restructured later.

## New Google permissions — you will need to reconnect

Phase 2 adds two permissions to the five you already granted in Phase 1. Google does not add permissions to an existing connection silently, so **you must reconnect once**. The app detects this and shows you a "Reconnect required" message rather than failing with a confusing error.

| New permission | What it lets the app do |
|---|---|
| `https://www.googleapis.com/auth/spreadsheets` | Read and write Google Sheets. Used only for this app's control workbook. |
| `https://www.googleapis.com/auth/drive.file` | Create the workbook, and access **only files this app itself creates**. |

That second one deserves a plain statement, because "Drive access" sounds alarming:

> `drive.file` does **not** give the app access to your Google Drive. It can only see and touch files it created itself. Your existing documents, photos and folders are invisible to it. The broad permissions (`drive`, `drive.readonly`) are never requested — there's an automated test that fails the build if anyone ever adds them.

## What it can and can't change in Gmail

**Nothing changed here. Phase 2 adds zero Gmail abilities.**

- **Can:** create and edit one spreadsheet — the one it made.
- **Cannot:** send, reply, archive, label, delete, trash, mark read/unread, or make any modification to your email whatsoever. Gmail access is still read-only, and Google itself would reject a write attempt.

## What happens when it runs

1. You open the app and click **Create / update my control workbook**.
2. The app looks for an existing workbook in three places, in order: the `SHEETS_WORKBOOK_ID` setting in your `.env` (usually blank), then a Drive search for a file it made earlier, and if neither turns one up, it creates a new one.
3. It checks every tab against the expected layout. Missing tabs get created. If a later phase adds a new column, that column is added **at the end** of the existing header row.
4. If the `Settings` tab has no rows yet, it fills in the defaults.
5. It gives you back the workbook's link.

Running it a second time is harmless — it will find the same workbook, see everything is already correct, and change nothing. That "safe to run repeatedly" property is called **idempotent**, and there are tests specifically for it.

Two things it deliberately will **not** do:

- **It never overwrites your edits.** If you change `dry_run` to `false`, running init again leaves it at `false`. Defaults are only written into a completely empty Settings tab.
- **It never reorders or deletes your columns.** If you drag a column somewhere else, or add one of your own, the app still works — it looks columns up by their name, never by their position. There's a test for that too.

## What you should test

First, run the tests and start the app:

```powershell
# From the project folder
.\.venv\Scripts\Activate.ps1

# Should say "121 passed"
pytest

# Start the app
uvicorn app.main:app --reload --port 8000
```

Then in your browser:

1. **Open** http://localhost:8000/

   If you connected during Phase 1, you'll see a yellow **"Reconnect required"** box listing the two new permissions. That's correct — it means the check works.

2. **Click "Reconnect now"** (or **Connect Gmail** if you're starting fresh).

   On Google's consent screen you should now see **seven** permissions — the five from Phase 1 plus Google Sheets and the narrow Drive one. Approve.

3. **Back on the homepage**, the yellow box should be gone and a **"Create / update my control workbook"** button should appear.

4. **Click that button.** You should get back a small block of JSON containing `"created": true`, `"settings_seeded": true`, and a `workbook_url`.

5. **Open the `workbook_url` in a new tab.** You should see a real Google Sheet named "Gmail Agent Control Workbook" with 11 tabs along the bottom, each with a bold, frozen header row. The `Settings` tab should have 8 rows already filled in.

6. **Check the defaults are safe.** In the `Settings` tab, confirm `dry_run` is `true` and `gmail_processing_enabled` is `false`.

7. **Test that it doesn't duplicate.** Go back to the app and click **Create / update my control workbook** again. This time the response should say `"created": false` and `"changed": false`. Refresh the spreadsheet — still 11 tabs, still 8 settings rows, nothing doubled.

8. **Test that your edits survive.** In the spreadsheet, change `dry_run` from `true` to `false` and press Enter. Click the init button once more, then reload the sheet — it should still say `false`. The app must never quietly reset your choices. **Set it back to `true` when you're done.**

9. **Visit** http://localhost:8000/sheets/settings — you should see your settings read back as JSON, including the `dry_run` value you just set.

10. **Visit** http://localhost:8000/sheets/status — should show `initialized: true` and the workbook ID.

11. **Confirm nothing touched your Drive.** Open https://drive.google.com. The only new thing should be the one spreadsheet. Nothing else moved, renamed or disappeared.

If all eleven steps behave, Phase 2 is verified.

## What could go wrong

- **"Reconnect required" won't go away** — you approved on Google's screen but unticked one of the permission checkboxes. Google sometimes shows them as individually-optional. Go to https://myaccount.google.com/permissions, remove the app, and connect again, approving everything.
- **409 error saying "This Google account was connected before the Sheets permissions were added"** — same cause. Reconnect.
- **"Google Sheets API has not been used in project ... before or it is disabled"** — you need to switch the APIs on in Google Cloud. Go to the Google Cloud Console → **APIs & Services** → **Library**, then search for and **Enable** both *Google Sheets API* and *Google Drive API*. Wait a minute, then retry. This is a one-time setup step per project.
- **"insufficient authentication scopes"** — the stored token is stale. Disconnect in the app, then connect again.
- **You accidentally delete a tab** — no harm done. Click the init button and it will be recreated with its header row. (Any *rows* that were in it are gone, though — Google Sheets deletion isn't something the app can undo.)
- **You accidentally delete the whole spreadsheet** — it goes to your Drive trash, where you can restore it. If you empty the trash, click init again and the app will build a fresh one; historical rows would be lost.
- **Two workbooks with the same name** — if you manually copy the workbook, the app takes the first one Drive returns and logs a warning. Rename or trash the spare to remove the ambiguity, or paste the ID you want into `SHEETS_WORKBOOK_ID` in `.env`.

## How to undo it

Phase 2 is fully reversible and touches nothing outside its own file:

1. **Delete the workbook** — open it in Drive and move it to Trash. Nothing else in your account is affected.
2. **Revoke the permissions** — https://myaccount.google.com/permissions → remove "Gmail Intelligence Agent". This drops the Sheets and Drive permissions along with the Gmail ones.
3. **Delete the local token** — click **Disconnect & delete stored token** in the app.

Your Gmail is untouched by all of this, because Phase 2 never had the ability to touch it.

## What success looks like

- `pytest` reports **121 passed**.
- The homepage correctly tells you to reconnect if your token is from Phase 1.
- Google's consent screen shows seven permissions, including `drive.file` and **not** full Drive access.
- One spreadsheet appears in your Drive, with 11 correctly-named tabs and bold headers.
- `Settings` starts with `dry_run = true` and `gmail_processing_enabled = false`.
- Clicking init twice produces exactly one workbook and one set of settings rows.
- An edit you make in the sheet survives the next init.
- `/sheets/settings` reads back what's actually in the sheet.

## Short definitions

- **Control workbook** — the Google Sheet the app creates. Your settings panel and its record book, in one file.
- **Tab** — one sheet inside the spreadsheet (the labels along the bottom).
- **Idempotent** — safe to run over and over. The second run changes nothing.
- **Scope** — one specific permission, like "read Gmail" or "use Sheets". Every one is listed on Google's consent screen.
- **`drive.file` scope** — permission limited to files the app itself created. Not access to your Drive.
- **Repository layer** — the code that translates between "give me the active sender rules" and the actual spreadsheet rows. It exists so that swapping Sheets for a real database later means rewriting one file instead of the whole app.
- **Seeding** — writing the starting values into an empty tab.

## A note on what's still missing

The workbook exists and is readable and writable, but **nothing reads it yet** — no classification decisions depend on those settings, because the rules engine doesn't exist until Phase 3. Right now you can edit `dry_run` and it won't change any behaviour, because there's no behaviour to change yet. That's expected.

Also worth knowing: the OAuth token is still stored in a local file, which won't survive a restart on Render. That's noted as a known limitation and gets fixed at deployment time (Phase 16).

## Next phase

**Phase 3 — Deterministic rules engine.** This is the one that starts making actual decisions: categories, P1/P2/P3 priority, the protection rules that keep banking, medical, travel and known-contact email out of Review, Substack handling, and the strict order in which rules are applied. Still **no** Gmail modifications — the decisions will be shown to you, not acted on. The explainer for that phase will include real examples of emails and what the engine decided about them.
