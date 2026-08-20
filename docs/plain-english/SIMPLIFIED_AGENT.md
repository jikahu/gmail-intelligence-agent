# The project got a lot smaller — here's what changed

## What was built before

This started as a big project: a web dashboard you'd log into, a Google Sheet the app managed for you (settings, VIP list, rules, a full history log of every decision), a daily summary email, an "undo my last batch of changes" button, reading the text inside PDF/Word attachments, and a whole layer that tried to track deadlines, money amounts, subscriptions, and trips mentioned in your email.

## What it is now

Just the core: the app reads your inbox, decides what each email is (a bill, a receipt, spam, something urgent), and applies a label in Gmail — the same kind of label you could apply by hand, just automatically. That's it. No dashboard to check, no spreadsheet to manage, no daily email to read.

## Why

You asked for this to shrink down to "an agent that has access to my inbox, does classification" — the dashboard and everything that existed only to feed it (the audit trail, the learning/approval workflow, the digest) weren't needed for that.

## What changed about the labels

Every label used to start with `AI/` — `AI/Critical`, `AI/Review`, `AI/Financial`, and so on. That prefix is gone now; the labels are just `Critical`, `Review`, `Financial`. If you'd already been using this app and have old `AI/*` labels sitting on messages in Gmail, those aren't touched automatically — they'll just sit there next to the new ones until you decide what to do with them (Gmail lets you rename or delete a label yourself, or ask Claude Code to do a one-time rename that preserves which messages had it).

## The new thing: it recognizes folders you already made

If you already have a Gmail label called something like "Uber" from years of filing your own mail, the app now notices that and files matching mail into it too — automatically, alongside its own label. It does this by checking whether the sender's company name matches an existing label you made. It never creates a new label for this and never touches a label you made in a way that would remove it from anything.

## Where your settings live now

There used to be a Google Sheet the app created and managed. Now there's a plain text file in the project, `config/rules.toml`, where you (or Claude Code, on your behalf) list any senders or domains you want to always trust or always send to Review. If you never had any of those set up, there's nothing to migrate — it starts empty.

## What still can't happen

Nothing here can permanently delete an email. There isn't even a "move to Trash" button anymore (that was a dashboard feature, and the dashboard is gone) — the most this app ever does is apply a label, archive a message out of your inbox, or mark it Important. Everything is reversible in Gmail itself.

## What to test

1. `GET /classify/preview?limit=25` in a browser or with `curl` — shows you what the app *would* do to your last 25 emails, without changing anything.
2. Check that a label you already use (like a vendor name) shows up correctly matched on a relevant email in that preview output.
3. If you want to actually turn on live changes, that's the `DRY_RUN` / `GMAIL_PROCESSING_ENABLED` switches described in the README — nothing changes until you flip both.

## How to undo this

The code change itself is a normal commit — `git log` shows it, and reverting is a normal git operation if you ever want the old dashboard-and-Sheets version back. Nothing about this change touched your actual Gmail account; everything so far is local code changes only.
