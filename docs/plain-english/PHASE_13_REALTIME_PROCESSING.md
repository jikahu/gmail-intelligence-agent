# Near-real-time processing (plain English)

## The one sentence that matters

> **The app checks your inbox for new mail every 10 minutes, automatically — but it still can't do anything to your Gmail unless both safety switches (`DRY_RUN=false` and `GMAIL_PROCESSING_ENABLED=true`) are on.**

## How it actually runs

There's no clock or timer built into the app itself. Instead, the app has one endpoint — `POST /realtime/poll` — that does a single "check for new mail, classify it, apply it" pass each time it's called. Something *outside* the app calls that endpoint on a schedule: currently, a free GitHub Actions workflow (`.github/workflows/realtime-poll.yml`) that pings the deployed app every 10 minutes.

**Why not just run a loop inside the app?** The app is hosted on Render's free plan, which shuts the whole thing down after 15 minutes with no visitors, and takes about a minute to wake back up. A timer living inside the app would just stop existing the moment the app went to sleep — it can't tick if it isn't running. By having something external do the pinging instead, two problems solve each other at once: the ping is what wakes the app up, and it's also the "check for new mail" trigger. As long as the ping happens more often than 15 minutes apart (it's set to 10), the app effectively never fully goes to sleep.

## What happens on each check

1. **Polling, not push notifications — a deliberate choice.** Gmail offers two ways to notice new mail: it can *push* a notification the instant something arrives, or the app can *poll* — periodically ask "what's new?" This app polls. Push would need its own Google Cloud setup, a verified public address, and a renewal step every 7 days — a lot of extra infrastructure for a personal, single-user tool. Polling needs none of that. The trade-off: new mail is noticed within one check interval (currently 10 minutes), not the instant it lands.
2. **Whole-thread awareness.** When new mail arrives, the app fetches that message's *entire* conversation thread in one step, so it can correctly tell whether you're actively part of an ongoing back-and-forth — one of the protections that keeps active conversations out of the Review pile.
3. **The same safety switches as everything else, no exceptions.** A scheduled check does not by itself allow the app to touch Gmail. Real writes still require `DRY_RUN=false` and `GMAIL_PROCESSING_ENABLED=true`. With those off, it still classifies new mail — it just doesn't apply anything, so you can see what it *would* do.
4. **Retries and graceful failure.** A momentary Gmail hiccup is retried automatically. Anything that isn't momentary (a message that's already gone, a permission problem) is logged and skipped without stopping the rest of that check.

## Key terms, explained

> *Polling* — periodically asking "anything new?" instead of waiting to be told. The app asks Gmail for a short list of what changed, not a full re-read of the mailbox, so a check is cheap even on a large inbox.

> *History id* — a bookmark Gmail hands out meaning "the mailbox as of this exact moment." The app remembers the last one it saw and asks for everything since. If that bookmark goes stale (Gmail only keeps a limited window), the app resets to "now" and says so plainly rather than guessing at what it might have missed.

> *Thread-aware* — knowing not just "what does this message say" but "is this part of a conversation I'm actively having."

> *Idempotent* — doing something twice has the same effect as doing it once. If the same message is ever seen again, and it's already exactly where it should be, the app makes no Gmail call for it a second time.

## What it can and cannot change in your Gmail

- **Can, when both safety switches are on:** add or remove its own labels, move a message into or out of your Inbox, add the Important flag (never remove it automatically), and add a matching label you already made by hand (like "Uber").
- **Cannot, ever:** send email, or trash/permanently delete anything. There is no Trash action anywhere in this app.
- **Will not touch older mail in a thread.** Fetching a whole thread is only to understand context; it never causes the app to go back and relabel older messages just because a new reply arrived.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                            # should all pass
uvicorn app.main:app --reload --port 8000
```

1. `GET /realtime/status` — shows `poll_count`, when the last check ran, and what it found. Starts at zero locally since nothing has called it yet.
2. `POST /realtime/poll` from `/docs` on a connected account. The first call should say it just recorded a starting point and processed nothing. Run it again — with no new mail, it should say "nothing new since the last poll."
3. Send yourself a test email, then call `POST /realtime/poll` again. It should show up as processed. If both safety switches are off (the default), the response says it was a proposal only.

On the live deployment, you can also just watch `GET /realtime/status` over a few minutes and see `poll_count` climbing on its own — that's the GitHub Actions schedule doing its job without you touching anything.

## What could go wrong

- **The scheduled trigger stops running.** If the GitHub Actions workflow is disabled, deleted, or GitHub Actions has an outage, nothing calls `/realtime/poll` automatically anymore, and there's no in-app fallback to notice this. `GET /realtime/status`'s `last_run_at` is how you'd spot it — if that timestamp stops moving forward, the automatic checks have quietly stopped.
- **A long outage.** If nothing checks for long enough that Gmail's history window expires, the app resets its bookmark to "now" and says so — anything that arrived during the gap needs a manual catch-up (`POST /gmail/apply`).
- **A single bad message.** Logged and skipped; never stops the rest of that check, never retried forever.

## What it does *not* do

- **No push notifications.** A deliberate simplicity choice — see "Polling, not push" above.
- **No retroactive reclassification.** A new reply in a thread never causes the app to relabel older messages in that thread.
- **No historical catch-up.** Each check only looks at what's new since the last one; there's no built-in way to sweep through months of old mail.
