# Phase 1 — Gmail read-only (plain English)

## What was built

Phase 1 gives the app the ability to **read** your Gmail — nothing else. Concretely:

- A "**Connect Gmail**" button on the homepage that sends you to Google's own consent screen.
- The code that receives Google's response, exchanges it for a long-lived permission (a "refresh token"), and stores that permission on your computer in an **encrypted file**.
- A read-only Gmail client that can list your recent messages (headers only — sender, subject, date, snippet), see your labels, and read a full thread's metadata.
- A read-only Contacts client that can list your Google Contacts and "Other Contacts" (people you email but haven't formally saved). This is used later for the "prior correspondent" protection rule so the app doesn't hide email from people you actually talk to.
- A **Disconnect** button that deletes the stored permission.
- A `/gmail/preview` page that proves the read connection works.

## Why

The whole point of the agent is to look at your Gmail and organize it. Before any of that can happen, the app needs your permission to see your inbox. That permission is granted through **OAuth**, which is Google's standard "let this app do a specific set of things on your account" system. Phase 1 sets that up in the safest possible way:

1. It asks for the **smallest** set of permissions that still lets later phases do their work.
2. It **never** asks for permission to send, modify, archive, or delete email.
3. The permission Google gives us back is stored **encrypted** on disk. If someone got the raw file, they couldn't use it without also knowing your `SESSION_SECRET`.
4. Google's response is verified with a signed "state" value so a malicious website can't trick your browser into completing the connection on their behalf.

## What happens when it runs

1. You start the app locally.
2. You visit http://localhost:8000/ in your browser.
3. You see "Not connected yet" and a list of the exact permissions the app is about to request. You click **Connect Gmail**.
4. Your browser goes to Google. Google shows its own consent screen listing the same permissions. You approve (or cancel — you can back out any time).
5. Google sends your browser back to the app at `/oauth/callback` with a temporary code.
6. The app trades the code for a refresh token, writes it to `oauth_tokens/token.json.enc` (encrypted), and shows you "Connected as your.email@gmail.com".
7. You can click **Preview last 10 messages** to prove the read works, or **Disconnect & delete stored token** to fully undo.

## Exact Gmail permissions the app requests

These are the **only** permissions the app asks for. Every one is listed in `app/gmail/scopes.py` with a description, and a test asserts that no write permissions are in the list.

| Scope | What it lets the app do |
|---|---|
| `openid` | Sign you in. |
| `https://www.googleapis.com/auth/userinfo.email` | See your Google account email address. |
| `https://www.googleapis.com/auth/gmail.readonly` | Read your Gmail messages and settings. **Does NOT allow sending, modifying, or deleting.** |
| `https://www.googleapis.com/auth/contacts.readonly` | Read your Google Contacts. |
| `https://www.googleapis.com/auth/contacts.other.readonly` | Read your "Other contacts" (people you email but haven't added to Contacts). |

## What it can and can't change in Gmail

- **Can:** read message headers, message bodies, labels, threads. Read your contacts.
- **Cannot:** send email, reply, forward, archive, delete, trash, mark as read/unread, mark as spam, add labels, remove labels, change settings, or make **any** modification whatsoever. That's a hard limit of the `gmail.readonly` scope — Google itself will reject any write attempt.

## What you should test

You need three things ready before you start:

1. **A Google Cloud OAuth client** — you said you already have this. Make sure:
   - Application type is **Web application**.
   - Authorized redirect URI is exactly `http://localhost:8000/oauth/callback`.
   - Your Gmail address is added as a **Test user** in the OAuth consent screen (unless the app is Published/Verified).

2. **The Client ID and Client Secret** — you'll paste them into `.env`.

3. **A fresh `SESSION_SECRET`** — this is what encrypts the token file. Generate one with:

```powershell
py -3.13 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Then walk through this checklist in PowerShell, from the project folder:

```powershell
# 1. If you haven't already, copy the example env file.
Copy-Item .env.example .env -ErrorAction SilentlyContinue

# 2. Open .env in Notepad and set THREE values:
#      GOOGLE_CLIENT_ID=<your client id>
#      GOOGLE_CLIENT_SECRET=<your client secret>
#      SESSION_SECRET=<paste the value from the command above>
notepad .env

# 3. Activate the virtual environment.
.\.venv\Scripts\Activate.ps1

# 4. Run the automated tests (should say "44 passed").
pytest

# 5. Start the app.
uvicorn app.main:app --reload --port 8000
```

Now, in your browser:

- Open http://localhost:8000/ — you should see the "Not connected" page and the exact scope list.
- Click **Connect Gmail**.
- On Google's consent screen, verify the app name matches what you set in Google Cloud and that the permissions shown match the list above. Click **Continue** / **Allow**.
- You should land back on the app at "Connected as your.email@gmail.com".
- Click **Preview last 10 messages** — you should see a JSON block with your ten most recent messages: sender, subject, date, one-line snippet, labels.
- Visit http://localhost:8000/oauth/status — should show `connected: true`, your email, and the granted scopes.
- Click **Disconnect & delete stored token** — visit `/oauth/status` again; should show `connected: false`.
- Visit `/gmail/preview` while disconnected — should return a 409 error saying "No Gmail token found."

If all of that happens, Phase 1 is verified.

## What could go wrong

- **"GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET must be set"** — you didn't fill in `.env`, or the app was started before `.env` was saved. Save `.env`, then restart uvicorn.
- **"redirect_uri_mismatch"** on Google's screen — the redirect URI in Google Cloud doesn't exactly match `http://localhost:8000/oauth/callback`. Fix it in the Google Cloud Console → Credentials → your OAuth client → Authorized redirect URIs. It must be exact, including the trailing path.
- **"This app isn't verified" / "Access blocked: has not completed the Google verification process"** — normal for a personal project. Two options:
  - In the Google Cloud Console → OAuth consent screen → add your Gmail address as a **Test user**. Retry.
  - Or click "Advanced" → "Go to [App name] (unsafe)" on the warning screen. This is safe because *you* built the app; the warning exists to protect people from unknown developers.
- **"Google did not return a refresh_token"** — Google only issues a refresh token on your first consent. If you had previously granted this app permission (even in a different session) it may not issue one again. Fix: go to https://myaccount.google.com/permissions, revoke the app, then run through the flow again.
- **"OAuth state validation failed"** — the callback URL was opened more than ~10 minutes after starting the flow, or the app restarted between clicks, or someone tampered with the URL. Just click **Connect Gmail** again.
- **"SESSION_SECRET is unset or still the placeholder value"** — you left the example value in `.env`. Generate a real one (see command above) and paste it in.
- **Port 8000 in use** — start on a different port (`--port 8001`) and update the redirect URI in Google Cloud + the `GOOGLE_OAUTH_REDIRECT_URI` in `.env` to match.

## How to undo it

Phase 1 changes nothing on Google's side except granting a permission that *you* can revoke at any time. To fully undo:

1. In the app, click **Disconnect & delete stored token** (or `Remove-Item .\oauth_tokens\token.json.enc`).
2. Optionally, delete the `oauth_tokens` folder itself.
3. Go to https://myaccount.google.com/permissions and revoke the "Gmail Intelligence Agent" grant. Google will then invalidate the refresh token on its side too.
4. Nothing in your Gmail changed — the app can't have changed anything even if it wanted to.

## What success looks like

- `pytest` reports **44 passed**.
- Homepage shows the connect button and the scope list before you connect.
- Google's consent screen lists the same five scopes and nothing else.
- After consent, homepage shows "Connected as \<your email>".
- `/gmail/preview` shows real message headers from your inbox.
- The file `oauth_tokens/token.json.enc` exists but is unreadable in a text editor (it's encrypted).
- Disconnect wipes the file and `/oauth/status` returns `connected: false`.

## Short definitions

- **OAuth** — Google's system for granting an app a specific, limited permission on your account without ever sharing your password.
- **Scope** — the technical name for one permission (e.g. "read Gmail"). Every scope is spelled out on the consent screen.
- **Refresh token** — the long-lived permission Google gives back after you consent. It's what lets the app stay connected without asking you to log in every hour. Kept encrypted on disk.
- **CSRF (state value)** — a random signed value that gets sent to Google and echoed back, so a malicious third party can't trick your browser into completing the OAuth flow on their behalf.
- **PKCE** — an extra safety layer that the Google client library adds automatically. Prevents another app on the same machine from stealing an in-flight OAuth code.

## Next phase

**Phase 2 — Sheets control store.** The app will auto-create a Google Sheets workbook that becomes the editable "control panel": rules, VIPs, learned suggestions, audit log, deadlines, etc. Still no Gmail modifications. I'll write a Phase 2 explainer the same way as this one when it's ready.
