# Phase 3 — The rules engine (plain English)

## What was built

This is the phase where the app starts **making decisions**. Until now it could read your email and store your settings. Now it can look at a message and work out what it is, how urgent it is, and what should happen to it.

What it produces for every email:

- **A category** — one or more of the 18 labels, like `Financial`, `Personal`, `Review`.
- **A priority** — P1 urgent, P2 important, or P3 normal.
- **A proposed action** — keep it in the Inbox, archive it, or move it to the Review area.
- **A reason, in plain words** — e.g. *"Kept because financial content ('account statement')."*
- **A confidence score**, and a flag saying whether the rules were sure enough or whether this one should wait for AI help in Phase 4.

There's a new page, `/classify/preview`, that runs all of this over your recent mail and shows you exactly what it *would* do.

> **Nothing is applied. Your Gmail is not touched.** There is no code in the app that can archive, label, or delete a message. That arrives in Phase 11, after the safety gate.

## Why the rules come before the AI

The app checks things in a fixed order, and AI isn't in it yet:

1. What Gmail already tells us (headers, categories)
2. **Rules you wrote yourself** (sender rules, domain rules)
3. **Your VIP list**
4. **Your contacts** and people you've emailed before
5. **Protected topics** — money, health, legal, travel, security, attachments…
6. The general classification rules
7. What you've done before (e.g. you starred it)
8. *AI — Phase 4*
9. *Final safety check*
10. *Actually changing Gmail — Phase 11*

Anything you decided explicitly beats anything the app guessed. That ordering is the whole design, and it's why AI can never quietly overrule you later.

## The one rule that matters most

> **An email that is protected can never be moved to Review.**

This isn't a keyword competing with other keywords. Protection is worked out first, and the Review decision simply isn't allowed to overrule it. In practice that means if the rules ever get a keyword wrong, the mistake can only go one way — an email stays visible when it could have been tidied away. It can't go the other way and hide something.

There's one deliberate exception: if a message has strong phishing signals, it gets flagged even if it came from someone you know. Someone impersonating your bank shouldn't be safe just because they spoofed a familiar name. Even then, the message is archived into Review — never deleted.

Two more safety rules, added after testing turned up cases where they were needed:

- **Anything rated P1 or P2 is never routed to Review.** If the app itself decided a message is important, it isn't allowed to then hide it.
- **Anything rated P1 is never archived.** A cancelled flight is technically a travel booking, which would normally be filed away — urgency wins.

## Real examples

These are **actual outputs** from the engine, not hand-written illustrations:

| Example email | Priority | Labels applied | Where it ends up | Why |
|---|---|---|---|---|
| Bank statement | P2 | Financial | **Inbox** | Kept because financial content ('account statement'). |
| Payment failed | P1 | Action-Required, Financial | **Inbox** | Kept because financial content ('payment failed'). |
| Fraud alert | P1 | Action-Required, Critical, Financial, Security | **Inbox** | Kept because fraud content ('fraud alert'). |
| Order receipt | P3 | Purchases-Receipts | **Archive** | Kept because purchase content ('your order'). |
| Flight cancelled | P1 | Purchases-Receipts | **Inbox** | Kept because travel content ('flight'). |
| Substack essay | P3 | Newsletter | **Inbox** | Kept because Substack newsletter, which you keep by default. |
| Other newsletter | P3 | Newsletter, Review | **Review** | Moved to Review: newsletter from a sender you haven't approved yet. |
| Flash sale | P3 | Low-Value, Review | **Review** | Moved to Review: promotional wording (% off, flash sale). |
| Cold sales pitch | P3 | Newsletter, Review | **Review** | Moved to Review: cold sales outreach ('quick question'). |
| Phishing attempt | P3 | Review, Suspicious | **Review** | Moved to Review: this message has phishing indicators. |
| Friend's email | P3 | Personal | **Inbox** | Kept because sender is in your Google Contacts. |
| Course deadline | P2 | Action-Required, Education | **Inbox** | Kept because education content ('assignment'). |
| Course material | P3 | Education | **Archive** | Kept because education content ('module'). |
| Interview invite | P2 | Action-Required, Career | **Inbox** | Kept because career content ('interview invitation'). |
| Advert with a PDF | P3 | none yet | **Left alone** | Kept because has an attachment (offer.pdf). |
| Price increase notice | P2 | Newsletter | **Left alone** | Classified as Newsletter at P2. |

A few of these are worth pointing out:

- **"Archive" is not "delete".** A receipt gets filed into `Purchases-Receipts` and leaves your Inbox, but it's still in your account and still searchable. Protection means it can't go to *Review* — it doesn't mean it stays in the Inbox.
- **"Left alone" means exactly that** — the app has an opinion about what the email *is*, but no opinion about moving it, so it doesn't move.
- **The advert with a PDF got no category.** Attachments are protected, so it can't be swept to Review, but the rules genuinely couldn't tell what it was. That's the honest answer, and it's flagged for Phase 4's AI to look at. Notice it *wasn't* labelled an important document just because a PDF was attached.
- **Substack stays; other newsletters go to Review.** You can approve any other newsletter sender by adding a whitelist rule to the workbook, and it'll behave like Substack from then on.

## How to change its mind

Everything is driven by the Google Sheet from Phase 2. Add a row and the behaviour changes on the next run — no code change:

| Tab | What to write | What happens |
|---|---|---|
| `Sender_Rules` | `sender` = `news@site.com`, `rule_type` = `whitelist` | That sender is protected and stays in your Inbox |
| `Sender_Rules` | `rule_type` = `blacklist` | That sender always goes to Review |
| `Sender_Rules` | `rule_type` = `classify_as`, `action` = `Financial` | Forces that category |
| `Domain_Rules` | `domain` = `chase.com`, `rule_type` = `whitelist` | Trusts the whole domain, including `alerts.chase.com` |
| `VIPs` | `email` = `boss@work.com`, `status` = `approved` | Their mail always stays in your Inbox |

Set `status` to `active` on rule rows, or they're ignored.

One protection worth knowing about: **you cannot whitelist a whole public email provider.** If you try to add a domain rule for `gmail.com` or `outlook.com`, the app refuses it and writes a warning to the log. Approving your aunt's Gmail address must never mean trusting every Gmail address on earth.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pytest                                    # should say 332 passed
uvicorn app.main:app --reload --port 8000
```

Then, with your account connected:

1. **Open** http://localhost:8000/ — there's a new section, *"See what the rules engine would do"*.
2. **Click "Preview classification of last 25 messages"**, or go to http://localhost:8000/classify/preview?limit=25
3. **Read the `summary` block at the top.** The number that matters is:

   ```json
   "protected_routed_to_review": 0
   ```

   This is the launch-gate measurement from the project spec. **It must be 0.** If it's ever above zero, that's a bug worth stopping for — tell me and I'll fix the rules before we go further.

4. **Go through the `messages` list.** For each one check the `why` line against what the email actually is. The fields to look at:
   - `would_review` — would it be moved to the Review area?
   - `would_keep_in_inbox` / `would_archive` — where would it go?
   - `protected` and `protection_reasons` — why it can't be hidden
   - `needs_ai` — the rules weren't confident; Phase 4 will handle these

5. **Look specifically for anything important marked `would_review: true`.** That's the failure mode we care about. Promotions, newsletters you don't read, and cold sales pitches showing up there is correct and expected.

6. **Try a bigger sample:** `/classify/preview?limit=50` (50 is the cap).

7. **Test a rule.** Open your control workbook, add a row to `Sender_Rules` with a sender from your Review list, `rule_type` = `whitelist`, `status` = `active`. Reload the preview — that sender should now show `protected: true` and `would_review: false`. (Rules are cached for 30 seconds, so give it a moment.)

8. **Confirm nothing changed in Gmail.** Open Gmail. No new labels, nothing archived, nothing moved. The response even says so: `"gmail_modified": false`.

## What could go wrong

- **Preview is slow.** It fetches each message in full plus your contacts list. 25 messages takes a few seconds. Use `?contacts=false` to skip the contacts lookup if you're just poking at it.
- **409 "No Google token found"** — connect your account first at `/oauth/start`.
- **Everything shows `protected: true`.** Possible if most of your recent mail is genuinely receipts and statements — but if literally everything is protected, the keyword lists may be too broad. Send me a sample and I'll tighten them.
- **Something obviously junky isn't going to Review.** Expected sometimes, and it's the safe direction. The lists that route mail to Review are deliberately cautious. Tell me what was missed and I'll add it.
- **Something important *is* going to Review.** This is the serious one. Note the sender and subject and tell me — it means a protection rule needs work.
- **A rule you added isn't taking effect.** Check `status` is `active`, check the spelling of `rule_type`, and remember the 30-second cache. Domain rules on public providers like `gmail.com` are refused by design.

## How to undo it

There is nothing to undo. The engine only computes and displays. To remove a rule you added, delete its row in the workbook or set `status` to something other than `active`.

## What success looks like

- `pytest` reports **332 passed**.
- `/classify/preview` returns decisions for your real mail.
- `protected_routed_to_review` is **0**.
- Financial, medical, legal, travel, receipt, security, contact and attachment emails all show `protected: true`.
- Substack shows `would_review: false`; other newsletters show `would_review: true`.
- Promotions and cold outreach show `would_review: true` with a sensible `review_reason`.
- Every message has a readable `why`.
- Gmail is completely unchanged.

## Short definitions

- **Rules engine** — plain code with keyword lists and if/then logic. No AI, no learning, same answer every time for the same email.
- **Deterministic** — same input, same output, always. Easy to test and easy to explain.
- **Protected** — this email can't be moved to Review. It might still be archived into a category.
- **Review** — an archive area for low-value mail. Out of the Inbox, still in your account, never deleted.
- **P1 / P2 / P3** — urgent / important / normal. Separate from the category: a P1 receipt and a P3 receipt are both receipts.
- **Bulk mail** — a mass mailing, detected from the hidden headers mass-mail systems attach. A strong Review signal, but it never overrides Substack, money, health, travel, security, or your own rules.
- **Confidence** — how sure the rules are, 0 to 1. Below the line, the message is marked `needs_ai` and Phase 4 will take a look.

## A note on what's still missing

- **Nothing is applied to Gmail.** Decisions are computed and shown only.
- **No AI yet.** Messages the rules can't settle are flagged `needs_ai` and otherwise left alone.
- **"People you've emailed before" is approximated** by your Contacts list. Real reply history needs the audit log from Phase 9.
- **Deadlines, money amounts, travel grouping and subscription tracking aren't extracted yet** — the labels exist, the extraction is Phase 6.
- **No dashboard.** Right now the output is raw JSON. The readable version is Phase 8.

## Next phase

**Phase 4 — the AI provider layer.** Anthropic and OpenAI behind one interface, swappable from the workbook. AI gets asked only about the messages the rules couldn't settle — the ones flagged `needs_ai` — and its answers are checked by the same deterministic rules before anything is accepted.

The headline for that phase: **the AI suggests, the rules engine decides.** AI will never be able to move an email, change a setting, approve a rule, or overrule a protection rule.
