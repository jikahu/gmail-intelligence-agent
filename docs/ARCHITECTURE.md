# Architecture

Working notes on how the app is put together. The product spec is `CLAUDE.md`; this file describes the code shape.

## Layering

```
FastAPI (app/main.py)
   │
   ├── OAuth + Gmail I/O        (app/gmail)
   ├── Classification pipeline  (app/classification) ─┐
   │                                                   ├─► Deterministic rules first,
   ├── AI provider layer        (app/ai)              ─┘   AI as supporting classifier
   ├── Local rules file         (app/rules)          ← VIPs, sender/domain rules
   ├── Scheduling / real-time   (app/scheduling)
   └── Security helpers         (app/security)        ← reserved; empty for now
```

There is no dashboard, no Sheets layer, no digest, no audit log, no attachment/intelligence/follow-up packages — see `CLAUDE.md` §14 for what used to be here and why it's gone.

## Cross-cutting

- `app/config/settings.py` — single source of environment truth. All modules read config through `get_settings()`.
- `app/logging_config.py` — stdlib-only JSON logging with secret + email-body redaction. Note: keys passed as `extra={...}` must not collide with reserved `LogRecord` attributes (`created`, `module`, `name`, `args`, `filename`, …) — logging raises if they do.
- `app/oauth_scopes.py` — aggregates `app/gmail/scopes.py` into `ACTIVE_SCOPES`. Adding a scope must go through here so the consent screen and the reconnect check stay in sync.
- `app/google_api.py` — builds authenticated Google service objects and re-persists a refreshed access token. Every Gmail API client goes through it.

## Data of record

There is no database and no control-plane service. Two small local files, both gitignored:

- `oauth_tokens/token.json.enc` — the Fernet-encrypted OAuth refresh token (`app/gmail/tokens.py`).
- `oauth_tokens/realtime_cursor.json` — the real-time poller's last-seen Gmail history id (`app/scheduling/state.py`).

One checked-in, user-edited config file:

- `config/rules.toml` — VIPs, sender rules, domain rules. Parsed read-only by `app/rules/store.py`; nothing in the app ever writes to it. A missing or unparsable file degrades to an empty ruleset rather than failing.

## Classification order

Enforced in code by `app/classification/`. See `CLAUDE.md` §13 for the order; the AI provider layer supplies suggestions but never executes Gmail actions directly.

```
app/classification/
  labels.py      17 taxonomy labels + per-label placement policy + P1/P2/P3
  message.py     EmailMessage; the only place that parses Gmail JSON
  patterns.py    keyword sets (protection generous, Review conservative)
  signals.py     bulk / newsletter / Substack / promotional / suspicion
  context.py     rules, VIPs, contacts — everything not in the email itself
  protection.py  §8 protection, and the security override
  engine.py      the classification pipeline; returns Classification, applies nothing
  pipeline.py    the only module that touches Gmail + Contacts + the rules file
  golden.py      scoring logic for the golden-dataset regression test
```

`engine.classify(message, context)` is a pure function. It never performs I/O, which is why the rules are testable with hand-written fixtures and no mocking.

### Why the Review veto is structural

Protection is evaluated **before** the Review branch and vetoes it, rather than competing with it as another weighted signal. A wrong keyword can therefore only fail in the safe direction — an email stays visible when it might have been tidied away — never the unsafe one. Two vetoes exist:

1. `protected` → no Review (CLAUDE.md §8)
2. priority P1 or P2 → no Review (§18)

Both carve out `signals.is_suspicious`, so phishing is still caught even from a known sender (§7). `engine._assert_safety_invariants()` re-checks the engine's own output and raises rather than emitting a decision that breaks these rules.

Keyword lists are deliberately unbalanced: sets that *protect* are generous, sets that route to *Review* are conservative. See the module docstring in `patterns.py`.

## AI layer

```
app/ai/
  schemas.py     AISuggestion — the validated structured-output contract
  sanitize.py    prompt-injection detection + content neutralisation
  prompts.py     PROMPT_VERSION, system / policy / content separation
  base.py        AIProvider ABC, AIResult, ProviderConfig
  anthropic_provider.py   the only module importing `anthropic`
  openai_provider.py      the only module importing `openai`
  factory.py     provider resolution + NullProvider
  validator.py   what the AI is allowed to change
  costs.py       price table, per-call cost, CostTracker
  assist.py      the consult-or-not gate and the merge
```

**AI suggests; the rules engine decides.** Three properties make that structural rather than aspirational:

1. **No vocabulary for acting.** `AISuggestion` has no field meaning archive, trash, delete, send, or apply. A successful prompt injection has nothing to ask for. Tested.
2. **One-directional influence.** The validator lets the AI make a message *more* visible or *more* urgent, never less. Priority merges via `most_urgent()`; Review is subject to the same protection and P1/P2 vetoes the engine applies.
3. **Re-checked output.** `validator.validate()` re-runs `engine._assert_safety_invariants()` on the merged result and discards the AI's contribution entirely if it fails.

Providers never raise — failures become `AIResult`s carrying an error, so a broken or unconfigured AI degrades classification to deterministic-only rather than breaking the run. `assist()` consults a provider only when `Classification.needs_ai` is set, which is the cost gate from §3. Label matching tolerates a stray old-style `AI/` prefix the model might still produce out of habit — the taxonomy dropped it, but a label the AI clearly meant shouldn't be lost over a naming mismatch.

## Gmail write path

```
app/gmail/
  scopes.py         OAuth scope definitions + descriptions
  oauth.py           the OAuth code-exchange flow
  tokens.py           encrypted local token storage
  client.py           read-only Gmail client
  people.py           Contacts lookups
  write_client.py     label create/color, modify_message, trash/untrash
  apply.py             check_write_gate, plan_change / apply_to_message — the diff logic
  vendor_labels.py     matches an existing user-made label (e.g. "Uber") to a sender
  write_service.py     the manual /gmail/apply batch orchestrator
```

`apply.py:plan_change` computes the minimal label diff between a message's real current state and what the classification wants, then issues exactly one `messages.modify` call — idempotent by construction, which matters since the real-time poller re-runs this on the same mail repeatedly. `vendor_labels.py` runs deterministically before that diff is computed: it looks at the sender's registrable domain root and display-name words against every label already in the mailbox (minus the app's own taxonomy and Gmail's system labels) and, on a match, adds that label too — additive only, never removed automatically.

## Real-time processing

```
app/scheduling/
  history.py   Gmail history-feed paging + gap detection
  poller.py    one poll cycle: find new mail, classify with thread context, apply
  service.py   RealTimePoller -- tracks the outcome of each poll for GET /realtime/status
  state.py     local JSON file holding the last-seen Gmail history id
  retry.py     retry wrapper for transient Gmail API failures
```

No background loop runs inside the app process. `POST /realtime/poll` runs exactly one cycle each time it's called; something outside the process (`.github/workflows/realtime-poll.yml`, currently — a GitHub Actions schedule hitting the deployed app every 10 minutes) is what turns that into "near real-time." That external trigger doubles as what keeps a Render free-plan instance from spinning down, since a loop that tried to live *inside* the process would just stop existing the moment the process went to sleep.

The first-ever poll bootstraps by recording the current history id without processing anything — turning this on never sweeps through whatever's already in the mailbox. Every later poll fetches only what's changed, classifies new messages with full thread context (so active-conversation protection works), and applies through the same `app/gmail/apply.py` gate and diff logic `/gmail/apply` uses. One bad thread or message is logged and skipped; it never stops the cycle.

## Safety invariants

- Never auto-delete or auto-Trash email. There is currently no Trash action anywhere in this app.
- Every write is a diffed, idempotent `messages.modify` call — never a blind overwrite.
- Prompt-injection defense: email content is always **data**, never instructions. See `CLAUDE.md` §15.

## Current status

See [`TECHNICAL_STATUS.md`](TECHNICAL_STATUS.md).
