# Phase 8 — Command Center dashboard (plain English)

## The one sentence that matters

> **Everything the app has been figuring out for the last five phases finally has a screen: a private dashboard, locked to your Google account, with nine cards you can click — and it still changes nothing in your Gmail.**

Phases 3–7 did the thinking. Phase 8 gives it a face.

## What was built

Three things:

1. **A private web dashboard** — the "Command Center" — at `/dashboard`.
2. **A lock on the door** — Google Sign-In, so only *you* can open it.
3. **Nine cards**, each a count you can click to see the emails behind it:

   | Card | What's in it |
   |---|---|
   | **P1 Urgent** | Needs you today — a payment failure, a security alert, a deadline that's now. |
   | **P2 Important** | Deal with soon — a fee change, a career opportunity, an action item. |
   | **Action Required** | Emails that need a reply or an action from you. |
   | **Waiting for Reply** | You asked something and 3 business days have passed with no answer. |
   | **Due Soon** | A deadline within the next 3 business days. |
   | **Overdue** | Past a deadline, or an action that's been sitting 3 business days. |
   | **AI Review** | Low-value or uncertain mail set aside — **never deleted**. |
   | **VIP Suggestions** | People you email a lot, proposed for you to approve (usually empty until Phase 9). |
   | **Subscription Review** | Recurring charges worth a look. The app **never** cancels them. |

Click a card and you get the list: for each email, **who it's from, the subject, when it arrived, a one-line summary, why it was flagged, a confidence score, its labels, and a 📎 if it has attachments** — exactly the row the spec asks for.

## "Google Sign-In" — what that means and why it's here

The dashboard shows your email. If the app is ever put on the internet (Render, later), that page has to be locked, or anyone with the link could read your mail. So:

- The dashboard asks **"Sign in with Google"** — Google's own login, the same one you use everywhere. *It does not give the app any new access to your mailbox.* It only tells the app **which account you are**.
- Only **one account** is allowed in: the Google account that connected Gmail. That's you. A stranger who signs in with a different Google account gets a polite "not authorized" page.
- **Connecting Gmail also signs you into the dashboard automatically**, so in normal use you won't see a second Google screen — you connect once and you're in.
- Your sign-in lasts **12 hours** by default (a setting), then you sign in again.

> *Short definitions:* **OAuth / Sign in with Google** is Google's secure way of letting an app confirm who you are without ever seeing your password. A **session** is a small, tamper-proof cookie the app gives your browser to remember you're signed in.

There's a seam for later: a setting called `DASHBOARD_AUTHORIZED_EMAILS` lets you add more accounts if you ever want to. We deliberately did **not** build a big multi-user system — the spec says one user for V1, designed so adding accounts later isn't a rewrite. That's exactly what this is.

## What it can and cannot change in your Gmail

- **It changes nothing.** Not a label, not the Inbox, not a single message. The dashboard is a *window*, not a set of controls — yet.
- Every page says so, in a banner: **"DRY RUN — NO GMAIL CHANGES ARE BEING MADE."**
- The Review queue **shows** buttons — Keep, Restore to Inbox, Review Correct, Make Sender Rule, Make Domain Rule, Suggest VIP, Trash — so you can see what's coming. **They're greyed out and do nothing.** There's a "What do these buttons do?" explainer under each list. The buttons switch on in **Phase 11**, when the app first earns permission to touch Gmail (and even then, Trash always asks you to confirm, and nothing is ever auto-deleted).

## A note on how it's built (and one honest choice)

- **It's plain server-rendered HTML with simple CSS — no React, no app framework.** That matches the spec's "keep it simple and understandable."
- The spec also mentioned **HTMX** (a small library for updating part of a page without a full reload). I **didn't** add it yet. The reason: in Phase 8 the buttons don't do anything, so there's nothing to update-in-place — HTMX would be weight with no job. The collapsible explainers use a built-in browser feature instead, so the page needs **no JavaScript at all**. I'll bring in HTMX in **Phase 11**, where clicking "Restore" or "Trash" genuinely needs to update just that row. If you'd rather I wire HTMX in now, say so and I will — I flagged it here rather than quietly skipping it.
- **A security detail worth knowing:** email subjects and sender names are written by strangers, so a malicious one could contain web code. The dashboard **escapes** every piece of email text before showing it — a subject like `<script>…` is displayed as harmless text, never run. There's a test that proves it.

## What happens when it runs

You open `/dashboard`. The app reads your most recent messages (read-only, no AI — so opening the page is fast and free), runs the same rules and timers you've already seen, counts up the nine cards, and draws them. Click a card, it filters to that list. Sign out, and the door locks again.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should say 670 passed
uvicorn app.main:app --reload --port 8000
```

Then:

1. **Connect Gmail** at http://localhost:8000/ (if you haven't). You'll be signed into the dashboard as part of that.
2. **Open http://localhost:8000/dashboard.** You should see the nine cards with counts and the read-only banner.
3. **Click a few cards** — P1, Action Required, AI Review. Check the emails inside look right: sender, subject, time, summary, why-flagged, confidence, labels, 📎.
4. **Open the AI Review list** and expand **"What do these buttons do?"** Confirm the buttons are greyed out and each is explained.
5. **Sign out** (top-right), then reopen `/dashboard` — it should send you back to the sign-in page.
6. **Confirm nothing changed in Gmail** — no new labels, nothing archived. The banner says as much on every page.

To try the standalone "Sign in with Google" screen (the one you'd hit after a session expires), you'll need the `DASHBOARD_LOGIN_REDIRECT_URI` registered on your Google OAuth client — see below.

## One setup step for the standalone sign-in

The "Sign in with Google" button sends you to Google and back to a new address: `http://localhost:8000/dashboard/auth/callback`. Google only allows redirects to addresses you've pre-registered. So in the Google Cloud Console, on your OAuth client, **add that URL** to the list of authorized redirect URIs (right next to the `/oauth/callback` one you already have). If you skip this, the normal path still works — connecting Gmail signs you in — but the standalone sign-in screen will show a Google error until the URL is registered.

## What could go wrong

- **"Not authorized" after signing in.** You signed in with a different Google account than the one that connected Gmail. Sign in with the account you connected, or add the other address to `DASHBOARD_AUTHORIZED_EMAILS`.
- **The dashboard sends you to the sign-in page unexpectedly.** Your 12-hour session expired, or the app's `SESSION_SECRET` changed (which invalidates all sessions). Sign in again.
- **A card shows 0 when you expected more.** The dashboard reads a *window* of recent mail (the last 50 by default). Something older than the window won't appear. You can widen it with `/dashboard?limit=50` (50 is the cap for now).
- **VIP Suggestions is always empty.** That's expected — the app doesn't *generate* suggestions until Phase 9. The card is here so it has a home.
- **The Google sign-in screen shows an error.** The `/dashboard/auth/callback` URL isn't registered on your OAuth client yet (see the setup step above).
- **`/dashboard` looks unstyled.** The stylesheet is served from `/static/dashboard.css`; if you're running behind something that blocks `/static`, the page still works, just plain.

## How to undo it

Nothing to undo — no Gmail changes were made, and nothing new is written to your spreadsheet by opening the dashboard. Signing out simply forgets your session cookie. The real **Undo Last Run** (for actual Gmail actions) is Phase 12, once there are Gmail actions to undo.

## What success looks like

- `pytest` reports **670 passed**.
- `/dashboard` is locked: no sign-in → you can't see it.
- Signed in as your account → nine cards with live counts, each opening its list.
- Every list shows sender, subject, time, summary, reason, confidence, labels, and an attachment marker.
- The Review queue lays out its buttons, greyed out, each explained.
- A hostile subject line is shown as text, never run.
- Every page states, plainly, that no Gmail changes are being made.

## What it does *not* do

- **No Gmail changes.** The buttons are inert until Phase 11.
- **No AI on page load.** The dashboard renders from the deterministic rules only, to stay fast and free. AI second opinions still run in the `/classify/preview` scan.
- **No live-updating.** The counts reflect the moment you loaded the page; refresh to recompute. Real-time processing is Phase 13.
- **No multi-user accounts.** One authorized account for V1, with a config seam for more later.
- **No VIP or rule *generation*.** The dashboard reads suggestions; making them is Phase 9.

## Next phase

**Phase 9 — Audit, feedback, and learning.** The app starts writing down every decision it makes (an audit trail), and the dashboard buttons begin feeding a learning loop: when you correct the app, it proposes a rule — a sender rule, a domain rule, a VIP — for **you to approve**. It never creates a permanent rule from a single correction on its own. Still no automated Gmail changes; those wait for Phase 11.
