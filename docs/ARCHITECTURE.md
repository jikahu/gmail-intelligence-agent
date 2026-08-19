# Architecture

Working notes on how the app is put together. The product spec is `CLAUDE.md`; this file describes the code shape.

## Layering (target)

```
FastAPI (app/main.py)
   │
   ├── Dashboard views          (app/dashboard)
   ├── OAuth + Gmail I/O        (app/gmail)
   ├── Classification pipeline  (app/classification) ─┐
   │                                                   ├─► Deterministic rules first,
   ├── AI provider layer        (app/ai)              ─┘   AI as supporting classifier
   ├── Attachment extractors    (app/attachments)
   ├── Intelligence extractors  (app/intelligence)    ← pure; imports no Gmail/Sheets
   ├── Follow-up timers         (app/followup)        ← business-day state; pure
   ├── Sheets repositories      (app/sheets)          ← storage of record (V1)
   ├── Digest builder           (app/digest)
   ├── Historical cleanup       (app/historical)     ← 12-month sweep, background task
   ├── Scheduling / real-time   (app/scheduling)
   ├── Learning + feedback      (app/learning)
   ├── Audit log                (app/audit)
   └── Security helpers         (app/security)
```

## Cross-cutting

- `app/config/settings.py` — single source of environment truth. All modules read config through `get_settings()`.
- `app/logging_config.py` — stdlib-only JSON logging with secret + email-body redaction. Note: keys passed as `extra={...}` must not collide with reserved `LogRecord` attributes (`created`, `module`, `name`, `args`, `filename`, …) — logging raises if they do.
- `app/oauth_scopes.py` — aggregates each phase's scope module into `ACTIVE_SCOPES`. Adding a scope anywhere must go through here so the consent screen and the reconnect check stay in sync.
- `app/google_api.py` — builds authenticated Google service objects and re-persists a refreshed access token. Every API client goes through it.

## Data of record

V1 uses **Google Sheets** as the control workbook (see `CLAUDE.md` §12). All Sheets access goes through a repository interface in `app/sheets/` so that Postgres/Supabase migration later requires only swapping the implementation.

```
app/sheets/
  scopes.py      Phase 2 OAuth scopes (spreadsheets + narrow drive.file)
  schema.py      Tab + column declarations. Append-only; never reorder.
  client.py      Sheets v4 / Drive v3 service objects
  workbook.py    ensure_workbook(): find-or-create, tab + column drift, seeding
  repository.py  ControlWorkbook → SheetTable → typed repositories
```

Two invariants hold this together:

- **Columns are addressed by name, never by coordinate.** The user can reorder or add columns without breaking the app.
- **Schema changes are additive.** New columns append to the end of an existing header row; nothing is renamed, reordered, or deleted.

## Classification order

Enforced in code by `app/classification/`. See `CLAUDE.md` §11 for the ten-step order; the AI provider layer supplies suggestions but never executes Gmail actions directly.

```
app/classification/
  labels.py      18 AI/* labels + per-label placement policy + P1/P2/P3
  message.py     EmailMessage; the only place that parses Gmail JSON
  patterns.py    keyword sets (protection generous, Review conservative)
  signals.py     bulk / newsletter / Substack / promotional / suspicion
  context.py     rules, VIPs, contacts — everything not in the email itself
  protection.py  §8 protection, and the security override
  engine.py      the §11 pipeline; returns Classification, applies nothing
  pipeline.py    the only module that touches Gmail + Sheets + Contacts
```

`engine.classify(message, context)` is a pure function. It never performs I/O, which is why the rules are testable with hand-written fixtures and no mocking.

### Why the Review veto is structural

Protection is evaluated **before** the Review branch and vetoes it, rather than competing with it as another weighted signal. A wrong keyword can therefore only fail in the safe direction — an email stays visible when it might have been tidied away — never the unsafe one. Two vetoes exist:

1. `protected` → no Review (CLAUDE.md §8, §15)
2. priority P1 or P2 → no Review (§21)

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
  validator.py   §11 step 9 — what the AI is allowed to change
  costs.py       price table, per-call cost, CostTracker
  assist.py      the consult-or-not gate and the merge
```

**AI suggests; the rules engine decides.** Three properties make that structural rather than aspirational:

1. **No vocabulary for acting.** `AISuggestion` has no field meaning archive, trash, delete, send, or apply. A successful prompt injection has nothing to ask for. Tested.
2. **One-directional influence.** The validator lets the AI make a message *more* visible or *more* urgent, never less. Priority merges via `most_urgent()`; Review is subject to the same protection and P1/P2 vetoes the engine applies.
3. **Re-checked output.** `validator.validate()` re-runs `engine._assert_safety_invariants()` on the merged result and discards the AI's contribution entirely if it fails.

Providers never raise — failures become `AIResult`s carrying an error, so a broken or unconfigured AI degrades classification to deterministic-only rather than breaking the run. `assist()` consults a provider only when `Classification.needs_ai` is set, which is the cost gate from §3.

## Attachments

```
app/attachments/
  types.py    kinds, dangerous-extension blocklist, magic bytes, all limits
  models.py   ExtractionStatus, ExtractedAttachment, AttachmentReport
  extract.py  text / CSV / PDF / DOCX extractors; never raises
  service.py  read-only Gmail download + per-message orchestration
```

**Attachments are read as information, never executed.** Three properties:

1. **Parsers, not runtimes.** pypdf and python-docx read format structure; neither can execute embedded JavaScript or VBA. Macro-capable formats (`.docm`, `.xlsm`, …) are refused before any parser sees them.
2. **The most dangerous interpretation wins.** `classify_attachment()` weighs the extension, the declared MIME type and the actual magic bytes; a file named `invoice.pdf` whose contents begin with `MZ` is an executable.
3. **Verified structurally.** An `ast`-based test fails the build on any execution primitive appearing anywhere in the package.

**A processing failure is inert.** Attachment-bearing emails are hard-protected (§8), and extracted text can only ever *add* a label — nothing reads an extraction status and decides to hide something. That's what makes the §11 guarantee ("a failure must never by itself route an email to Review") structural rather than a convention.

Limits live in one place (`types.py`) and are all hard caps. The decompression-bomb guard reads the ZIP central directory rather than expanding the payload.

## Intelligence

```
app/intelligence/
  dates.py         stdlib-only date extraction + normalization (confidence, flags)
  money.py         currency/amount extraction; account masking (last-4 only)
  deadlines.py     date + action-cue → Deadline
  financial.py     §7 minimum: kind, amount, currency, due date, safe ref
  subscriptions.py service, amount, frequency, renewal; suggested_review
  material.py      price/fee/terms change: kind, old→new, effective date
  travel.py        union-find trip grouping (thread / ref / destination+dates)
  orders.py        order grouping + delivery lifecycle + problem flag
  duplicates.py    same-sender Jaccard union-find; reports only
  senders.py       shared brand_name() helper
  models.py        frozen records + Message/Batch/IntelligenceReport
  service.py       analyze_message / analyze_batch / analyze
  persistence.py   report → Deadlines/Subscriptions/Trips upserts (idempotent)
```

**The package is pure and decoupled.** It imports neither `app.gmail` nor `app.sheets` nor `googleapiclient` — an `ast`-based test enforces this. `persistence.persist()` receives a workbook *handle* as an argument; the pipeline/route wires Gmail and Sheets around it. So the layer can extract facts but cannot, by construction, act on the mailbox.

**It observes; it never decides.** `analyze` reads a `Classification` only as a hint (is it actionable? expired?) and never mutates it. Duplicate detection *reports* groups rather than feeding the Review decision, keeping the §15 launch-gate guarantee intact. A test asserts the classification object is unchanged after an intelligence pass.

**Account numbers are masked at the source.** `money.AccountRef` has only `last4` + `original_text`; there is no code path that returns a full number (§7).

Workbook writes go through keyed repositories (`DeadlinesRepository`, `SubscriptionsRepository`, `TripsRepository`) whose `upsert` finds-then-updates-or-appends, so re-running a scan updates rows instead of duplicating them (§13 idempotency). Writing to Sheets is not a Gmail write — dry-run is about the mailbox.

## Follow-up

```
app/followup/
  businessdays.py  US + Kenya holidays (computed), is_business_day, add/between
  models.py        FollowUpKind / FollowUpItem / FollowUpReport
  deadlines.py     business-day status refinement (due_soon/overdue/upcoming)
  threads.py       expects_reply heuristic + Waiting/Overdue-Action evaluation
  service.py       evaluate / evaluate_from_results / refine_report
```

**Holidays are computed, never listed** (§3). Floating holidays are derived (`_nth_weekday`/`_last_weekday`); Easter (for Kenya's Good Friday / Easter Monday) uses the Anonymous Gregorian algorithm; observance shifts are applied (US Sat→Fri/Sun→Mon, Kenya Sun→Mon cascading). `holidays(year)` is generated for `year-1…year+1` and filtered, so a New-Year-observed-on-31-Dec is attributed to the right year. Known gap: Kenya's lunar Islamic holidays aren't computed.

**Follow-ups are stateless recomputation** (§13). Waiting for Reply and Overdue Action both key off a thread's *most recent* message, so a reply flips which side is latest and the item stops being emitted on the next scan — nothing to store or clear. The whole layer is pure and read-only; `AI/Waiting-For-Reply` is *proposed*, never applied (writes are Phase 11).

## Digest

```
app/digest/
  models.py       DigestSection / DigestReport
  service.py      digest_timezone/digest_hour (workbook-first), build_digest, generate_if_due
  persistence.py  DigestReport -> Digest_Log row (idempotent per digest_date)
  views.py        server-rendered HTML for GET /dashboard/digest
  scheduler.py    DigestScheduler -- background asyncio loop, mirrors RealTimePoller
```

**A reordering of Phase 8's own data, not a second analysis.** `build_digest` calls `dashboard.service.build_command_center` — the same read-only pass the Command Center itself runs — and keeps only the CLAUDE.md §13 digest sections in the CLAUDE.md §13 order (P1, P2, Action Required, Overdue, Waiting for Reply, Due Soon, Review), dropping VIP Suggestions and Subscription Review. `dashboard.service.Row`/`Card` are reused directly rather than duplicated.

**Live render, receipt-only persistence.** `/dashboard/digest` always recomputes fresh, exactly like every other Command Center page — `Digest_Log` records that a digest ran and its section counts (for a history / idempotency check), never the digest's own content. `generate_if_due` is the one function that treats a digest as something that should happen at most once per calendar day; it checks `Digest_Log` for that date before doing any work, rather than tracking a separate cursor.

**Runs by default, unlike the Phase 13 poller.** `DigestScheduler` never writes to Gmail and never spends AI budget, and its own clock-check tick makes no external call unless it's actually time to build a digest — so `digest_scheduler_enabled` defaults `true`, a deliberate departure from `realtime_enabled`'s default-off (see `docs/TECHNICAL_STATUS.md`'s Phase 14 notes for the full reasoning).

## Historical cleanup

```
app/historical/
  models.py    HistoricalRunStatus -- live progress for the one active sweep
  service.py   twelve_months_ago/historical_query, run_historical_cleanup
  runner.py    HistoricalRunner -- background asyncio task, runs once per start()
```

**Background task, not a synchronous request.** Every earlier batch endpoint (`/acceptance/run`, `/gmail/apply`) is bounded (≤250 messages) and runs inside one HTTP request. A 12-month sweep isn't bounded, so `POST /historical/start` returns immediately and the sweep runs via `asyncio.create_task` + `asyncio.to_thread` — the same mechanism `RealTimePoller`/`DigestScheduler` use for their loops, except `HistoricalRunner` runs once per `start()` rather than repeating on a timer.

**Reuses the same primitives everything else does.** Paging is the only new capability (`GmailReadClient.list_message_ids`); classification (`pipeline.classify_raw_messages`), applying (`gmail_apply.plan_change`/`apply_to_message`), and the write gate (`gmail_apply.check_write_gate`) are all unchanged. Because a confirmed sweep's changes land in `Audit_Log`/`System_Runs` in exactly the shape every other real write already uses, Phase 12's Undo Last Run needed zero changes to work on a historical run.

**A safety-invariant violation aborts the whole sweep.** Every other per-message failure is caught, logged, and counted; `engine._assert_safety_invariants()` raising is deliberately let through instead, since it signals a bug in the classifier itself rather than a one-off data problem — see `docs/TECHNICAL_STATUS.md`'s Phase 15 notes.

## Safety invariants

- Never auto-delete or auto-Trash email.
- Every Gmail write is logged to the audit trail with enough state to power **Undo Last Run**.
- Prompt-injection defense: email content is always **data**, never instructions. See `CLAUDE.md` §16.

## Current phase

See [`TECHNICAL_STATUS.md`](TECHNICAL_STATUS.md).
