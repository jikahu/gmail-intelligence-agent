# Phase 5 — Attachment analysis (plain English)

## The one sentence that matters

> **Attachments are read as information, never executed.**

Nothing in this phase runs a file, opens a macro, or evaluates a script hidden inside a document. The code reads bytes and returns text. That's the whole job.

## What was built

The app can now read what's *inside* an attachment, not just note that one exists.

- **Text is extracted** from PDF, Word (`.docx`), CSV and plain text files.
- **Program files are refused** without being opened — and that includes ones disguised as documents.
- **Images and archives are recognised but not opened.** There's no OCR in V1, so an image has no text to read, and unpacking a stranger's zip file buys nothing.
- **Every attempt is recorded** with a plain-English outcome, whether it worked or not.
- The contents now help classification: a Word document containing *"Form W-2 Wage and Tax Statement"* is recognised as a real record, where before the app could only guess from the filename.

Still no Gmail modifications.

## The promise about failure

The project spec has a specific requirement, and it's worth quoting:

> An attachment-processing failure must never by itself route an email to Review.

Emails with attachments are already protected from Review (Phase 3). So a file the app can't read simply contributes nothing — it can't take anything away, because the only thing extracted text is able to do is *add* a label. A corrupt PDF leaves an email in exactly the state it would have been in if the attachment had never existed.

There's a test for this across every failure mode: corrupt, encrypted, oversized, a decompression bomb, an executable, an image. None of them cause a Review.

## Real examples

Actual output, from genuinely generated files:

| Attachment | What happened | Text read |
|---|---|---|
| `statement.pdf` | Read successfully. | 35 characters |
| `scan.pdf` | Opened, but there was no readable text in it. | none |
| `w2.docx` | Read successfully. | 31 characters |
| `expenses.csv` | Read successfully. | 45 characters |
| `notes.txt` | Read successfully. | 38 characters |
| `photo.jpg` | This kind of file isn't read for text. | none |
| `setup.exe` | This is a program file. It was not opened, and it was not run. | none |
| `invoice.pdf` *(actually an .exe)* | This is a program file. It was not opened, and it was not run. **Note: the name 'invoice.pdf' does not look like a program, but the contents are one.** | none |
| `report.docm` | This is a program file. It was not opened, and it was not run. | none |
| `bundle.zip` | This kind of file isn't read for text. | none |
| `bomb.docx` | Too large to open safely. | none |
| `broken.pdf` | The file appears to be damaged or incomplete. | none |

Three of those rows are the interesting ones:

**`invoice.pdf` that's actually a program.** This is the classic malware delivery trick — name it like a document and hope the recipient double-clicks. The app checks three things: the file extension, the type the email *claims* it is, and the actual first few bytes of the file. Where they disagree, **the most dangerous answer wins**. This file starts with the Windows program signature, so it's a program, whatever it's called.

**`report.docm`.** A `.docm` is a Word document that can contain macros — small programs that run when you open it. It's refused before any software touches it. Same for `.xlsm` and `.pptm`.

**`bomb.docx`.** A Word file is really a zip archive. A "decompression bomb" is a tiny file that claims to expand into gigabytes, designed to exhaust a machine's memory. The archive's index states each file's real size *before* anything is unpacked, so the app reads the claim, sees it's absurd, and declines — without ever expanding it.

## What "never executed" actually means

Not a promise, three facts:

1. **The libraries used are parsers, not runtimes.** `pypdf` reads a PDF's text objects; `python-docx` reads the XML inside the archive. Neither has the ability to run the JavaScript a PDF may embed or the VBA a Word file may contain.
2. **Macro-capable formats never reach a parser at all.** They're refused at the door.
3. **There is no code path that could execute anything.** An automated test parses every file in the attachment code and fails if it finds `subprocess`, `eval`, `exec`, `os.system`, `os.popen`, `startfile`, or an import of `ctypes` or `pty`. It reads the actual code structure rather than searching the text, so a mention in a comment doesn't fool it and a real call can't hide behind one.

If a PDF *does* contain an embedded script, that gets noted as a warning on the dashboard. Noted — the script is never run.

## Other limits, and why each exists

| Limit | Value | Why |
|---|---|---|
| Largest file opened | 10 MB | A larger one is refused, and never even downloaded |
| Text kept per file | 50,000 characters | Enough to classify; bounded memory |
| PDF pages read | 100 | A 5,000-page PDF doesn't need exhausting |
| CSV rows read | 1,000 | Same |
| Attachments per email | 10 | An email with 40 files doesn't need all of them read |
| Uncompressed size | 100 MB | Decompression-bomb guard |
| Compression ratio | 200:1 | Same |

Filenames get special treatment because they're written by the sender. Anything that looks like a path — `../../../etc/passwd` — has the path stripped out. Nothing here writes attachments to disk at all, so this is belt-and-braces, but the name does end up in your logs and your spreadsheet.

## A note on CSV files

You may have heard of "CSV injection" — a cell starting with `=` that a spreadsheet program treats as a formula. That's a risk for the *program that opens the file*, not for us: nothing here evaluates anything, and the app never writes a CSV back out. A cell is just text to it, so no formula can run.

## What you should test

```powershell
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"          # pulls in pypdf and python-docx
pytest                            # should say 529 passed
uvicorn app.main:app --reload --port 8000
```

Then:

1. **Find some email with attachments.** Try `/classify/preview?limit=25&query=has:attachment` — the `query` parameter takes normal Gmail search syntax.
2. **Look at the `attachments` block** on each message. For each file you'll see its name, type, size, what happened, and how many characters were read. You'll **never** see the extracted text itself — it's used for classification and then dropped.
3. **Check the summary counts:** `with_attachments`, `attachments_unreadable`, `attachments_blocked`.
4. **Confirm the important thing:** `protected_routed_to_review` must still be **0**. Reading attachments must not change that number.
5. **Look for an email with an unreadable attachment** — a scanned PDF is the most likely. Confirm it's still sitting in your Inbox or filed in its category, and **not** in Review.
6. **If you have an email with a `.zip` or an image attached,** confirm it says the file wasn't opened rather than reporting an error. Not opening it is the correct outcome, not a failure.
7. **Try turning it off:** `/classify/preview?limit=25&attachments=false`. Everything should still classify; the app just won't know what's inside the files.

**A word about speed.** Reading attachments means downloading them, so a preview over 25 messages with attachments is noticeably slower than one without. Use `attachments=false` when you just want to poke at classification.

## What could go wrong

- **"The attachment could not be downloaded"** — a network hiccup, or Gmail declined. Harmless; the email is classified without it. Reload to retry.
- **Preview got slow** — attachments are being downloaded. Add `&attachments=false`, or lower `limit`.
- **Lots of "no readable text (it may be a scan)"** — normal. Many PDFs are photographs of paper. Reading those needs OCR, which isn't in V1.
- **A file you expected to be read says "isn't read for text"** — check the type. V1 handles PDF, DOCX, CSV and TXT. Old `.doc`, `.xlsx` and `.pptx` aren't included; tell me if you get a lot of those and it's easy to add.
- **A legitimate file was blocked as a program** — this would mean its actual contents begin with a program signature, which for a real document shouldn't happen. Worth telling me, with the filename.
- **`attachments_blocked` is above zero** — someone sent you an executable. That's information, not a bug. The app didn't open it.

## How to undo it

Nothing to undo. Nothing is written to disk, nothing is stored, and attachments in Gmail are untouched. Add `&attachments=false` to skip the whole step.

## What success looks like

- `pytest` reports **529 passed**.
- Emails with attachments show an `attachments` block listing each file and what happened.
- Word, PDF, CSV and text files report as read; images and archives report as not opened.
- Any `.exe`, `.docm` or similar reports as a program file that wasn't run.
- `protected_routed_to_review` is still **0**.
- An email with an unreadable attachment is still exactly where it was.
- The extracted text never appears in the output.

## Short definitions

- **Extraction** — pulling the readable words out of a file. Not the same as opening it in an app.
- **Parser** — code that reads a file format's structure. Unlike a program that "opens" a file, a parser has no ability to run anything the file contains.
- **Macro** — a small program stored inside a document. `.docm`, `.xlsm` and `.pptm` can contain them; those types are refused.
- **Magic bytes** — the first few bytes of a file, which identify what it really is regardless of its name.
- **Decompression bomb** — a tiny file crafted to expand into an enormous one and exhaust memory.
- **OCR** — reading text out of a picture of text. Not in V1, which is why scanned PDFs come back empty.
- **`.docx` vs `.docm`** — the `m` means macro-enabled. `.docx` cannot contain macros and is read; `.docm` can and isn't.

## A note on what's still missing

- **No OCR.** Scanned documents and images come back with no text. Adding it means a much heavier dependency; worth doing later if your mail is mostly scans.
- **No `.xlsx`, `.pptx` or old `.doc`.** V1 is the four types in the spec. Easy to extend.
- **Attachments aren't preserved anywhere separately.** The spec calls for preserving the original alongside the email — for now the original is simply left untouched in Gmail, which achieves that. A separate archive would be a later phase.
- **Nothing is persisted.** Attachment results live for one request; the audit log is Phase 9.
- **The AI doesn't see attachment text yet.** `analyze_attachment()` exists on the AI interface, and the extracted text informs the rules, but it isn't included in the AI prompt. Worth doing when there's a reason to.

## Next phase

**Phase 6 — Intelligence features.** The extraction layer: pulling actual deadlines out of "payment due 30 September", money amounts and currencies, grouping flights and hotels into a trip, tracking subscriptions and renewals, spotting when a company quietly changes its prices or terms, recognising duplicates, and noticing when something has expired. This is where the `Deadlines`, `Subscriptions` and `Trips` tabs in your workbook start filling up.
