# Phase 16 — Render deployment (plain English)

## The one sentence that matters

> **The app can now run on Render instead of your own laptop, for $0/month, and it stays connected to your Gmail account across every redeploy without needing a paid always-on disk.**

## What was built

This phase doesn't change how the app classifies mail, what it's allowed to touch in Gmail, or any safety rule — it's purely about running the exact same app somewhere else. Three things needed solving to make that work well:

1. **A finished `render.yaml`.** This file tells Render everything it needs: which Python version to use, how to install the app, how to start it, and where to check that it's healthy (`/health`). It's set to Render's **free** plan — this is a personal, single-account project, so the $7/month "Starter" plan isn't needed.
2. **A fix for Render's disposable disk.** Render rebuilds your app's container from scratch on every redeploy — anything the app saved to its own local disk while running gets thrown away. That's a real problem for the small encrypted file that keeps you signed into Gmail (`oauth_tokens/token.json.enc`): without a fix, you'd have to reconnect Gmail by hand after every single deploy. See "Key terms" below for how this got solved without paying for a disk.
3. **A safety check for a real deploy.** The app refuses to start in production if you forgot to set a real `SESSION_SECRET` (the value that both signs your dashboard login and encrypts the Gmail token) — better a failed deploy with a clear error than a live app quietly running with an insecure default.

## Key terms, explained

> *Ephemeral filesystem* — "ephemeral" means temporary. Render tears down and rebuilds the container your app runs in on every redeploy, like striking a stage set after a show. Any file the app wrote to its own local disk while running doesn't survive that — only what's in your actual GitHub repo, and Render's own separate environment-variable settings, come back.

> *Environment variables* (Render's, not the app's) — this is the *other* storage place, and it's not disposable. It's more like a filing cabinet Render keeps in its own office, separate from the stage. Every fresh container gets handed a copy of whatever's in it. This is why secrets like `GOOGLE_CLIENT_SECRET` already worked fine before this phase — nothing about them depended on the disposable disk.

> *Refresh token* — the specific part of "being signed into Gmail" that actually needs to survive a redeploy. It's issued once, the first time you connect, and normally never changes again after that (unlike the access token, which is short-lived and automatically renewed behind the scenes). Because it's so stable, it's the one thing worth durably seeding from Render's filing cabinet instead of the disposable stage.
>
> `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` is that seed: an environment variable you set once, by hand, after connecting Gmail on the live site. Every time the app boots and finds no local token file, it checks this variable and rebuilds the file from it automatically — no reconnect needed.

## What it can and cannot change in your Gmail

Nothing changed here. Every rule from Phases 1–15 still applies exactly as before: `DRY_RUN=true` and `GMAIL_PROCESSING_ENABLED=false` by default, the same three-switch write gate for any real change, no automatic Trash, ever. This phase is about *where* the app runs, not what it's allowed to do once it's running.

## What happens when it runs

Render builds the app from your GitHub repo, starts it with `uvicorn`, and pings `/health` to confirm it's alive. On the free plan, if nobody's used the app for about 15 minutes, Render puts it to sleep — the next visitor waits roughly a minute for it to wake back up, and while it's asleep, background jobs (the mail poller, the midnight digest) are asleep too. The first time you connect Gmail on the live site, its confirmation page shows you the refresh token once, with instructions to paste it into Render's dashboard. From then on, every fresh deploy quietly rebuilds the local token file from that value before anything else needs it.

## Deploying it — the actual steps

1. **Push the code to GitHub** (done automatically as part of this phase — see the summary at the end of this session for the repo link).
2. **Create a Render account** at render.com and connect it to your GitHub account.
3. **New → Web Service**, pick this repo. Render reads `render.yaml` automatically and pre-fills almost everything.
4. **Fill in the secrets** Render leaves blank (`sync: false` in `render.yaml`): `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (whichever you'll use), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SHEETS_WORKBOOK_ID` (leave blank — the app creates it), and a real `SESSION_SECRET` (generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
5. **Deploy.** Render gives you a URL like `https://gmail-intelligence-agent.onrender.com` (or a similar name if that one's taken).
6. **Now set the two redirect URIs** using that real URL: `GOOGLE_OAUTH_REDIRECT_URI` → `https://<your-url>/oauth/callback`, and `DASHBOARD_LOGIN_REDIRECT_URI` → `https://<your-url>/dashboard/auth/callback`. Add both as authorized redirect URIs on your Google OAuth client in Google Cloud Console. Saving these env vars triggers a quick automatic redeploy.
7. **Visit the live site and click Connect Gmail.** After you approve, the confirmation page shows a refresh token in a box, with a note to copy it.
8. **Paste that value into Render's dashboard** as `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` (and, optionally, your email into `GOOGLE_OAUTH_SEED_ACCOUNT_EMAIL`). This triggers one more redeploy.
9. **Check `/health`** on the live URL — `"status": "ok"`, `"phase": 16`, `"gmail_connected": true`.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should all pass — this phase adds tests, changes no behavior
```

1. Locally, confirm nothing broke: `uvicorn app.main:app --reload --port 8000`, then `/health` still reports as before.
2. Locally, set `APP_ENV=production` and leave `SESSION_SECRET` at its placeholder — starting the app should now fail immediately with a clear error instead of booting insecurely. Undo both before going back to normal local development.
3. On Render, after finishing the steps above: use Render's dashboard to trigger a **manual redeploy**, then check `/health` and `/oauth/status` again — it should still say `"connected": true` without you doing anything, proving the reseed worked.
4. Confirm the free-tier spin-down is what you expect: leave the site untouched for 15+ minutes, then load it again — the first request should take a bit longer while it wakes up.

## What could go wrong

- **You forget to update the redirect URIs after the first deploy.** Google will reject the OAuth callback with a "redirect_uri_mismatch" error. Fix: set them to your real Render URL (step 6 above) and make sure they're also added in Google Cloud Console.
- **You disconnect Gmail on the live site but the seed variable is still set.** The next restart will silently reconnect using the old seed. The disconnect response now tells you this directly — remove `GOOGLE_OAUTH_SEED_REFRESH_TOKEN` from Render's dashboard too if you want a disconnect to actually stick.
- **The free tier feels slow.** That's the spin-down/wake-up trade-off described above, not a bug. If it becomes annoying, the fix is simply switching `plan: free` to `plan: starter` in `render.yaml` (a $7/month, always-on plan) — nothing else about this phase's design needs to change to support that later.
- **A build fails because an AI provider package is missing.** `render.yaml`'s build command installs both the `anthropic` and `openai` extras specifically so switching `AI_PROVIDER` is just an env var change, never a rebuild — if you see an import error for either package, check the build command wasn't edited down to just `pip install -e .`.

## How to undo it

Nothing about this phase touches Gmail, so there's nothing to reverse there. To undo the *deployment* itself: delete the Render service from Render's dashboard (this doesn't affect your GitHub repo, your Sheets control workbook, or your Gmail account at all — it only stops the hosted copy from running) and keep using the app locally exactly as before.

## What success looks like

- `pytest` passes (918 tests).
- `render.yaml` deploys cleanly on Render's free plan.
- `/health` on the live URL reports `"status": "ok"` and the correct phase number.
- Connecting Gmail once on the live site, then triggering a manual redeploy, leaves the app still connected — no repeat consent screen.
- Starting the app in production with a placeholder `SESSION_SECRET` fails loudly instead of running insecurely.
- Every safety default from earlier phases (`DRY_RUN=true`, `GMAIL_PROCESSING_ENABLED=false`, the write gate) is unchanged on Render.

## What it does *not* do

- **No paid infrastructure.** No persistent disk, no Starter-tier upgrade — everything here targets Render's free plan.
- **No change to what the app is allowed to do in Gmail.** Same rules, same gate, same "never auto-Trash," just running somewhere new.
- **No automatic secret rotation.** If you ever rotate `SESSION_SECRET`, the stored token becomes unreadable and you'll need to reconnect Gmail and re-copy a fresh seed value — the same trade-off that already existed for local development, just worth repeating here.
- **No keep-awake trick.** The free tier's spin-down is left as-is rather than working around it with an external ping service — see "What could go wrong" for the simple upgrade path if that ever matters.

## What's next

Phase 16 is the last item on CLAUDE.md's phase plan — the app's V1 feature set (§19) is now essentially complete: Gmail connects securely, near-real-time processing and the 12-month sweep both work, the 250-email acceptance gate passed with zero protected emails misrouted, Review messages archive instead of deleting, priorities/deadlines/money are extracted, Substack is protected, VIPs require approval, the dashboard works, Undo Last Run works, the midnight digest works, and the whole thing now runs on Render instead of a developer's own machine. From here, further work is refinement and real-world use — watching how it performs against your actual mailbox — rather than a new numbered phase.
