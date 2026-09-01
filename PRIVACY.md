# Privacy Policy

This is a personal, single-user Gmail automation tool. It is not a public service,
does not have other users, and is not offered to the public.

## What it accesses

The app connects, via Google OAuth, to one Gmail account belonging to the
developer (the repository owner) only. It reads message metadata and content
to classify mail, and applies/removes Gmail labels and archive state on that
same account. See `app/gmail/scopes.py` in this repository for the exact
OAuth scopes requested and why.

## What it does not do

- It does not send email on the user's behalf.
- It does not permanently delete email (see `CLAUDE.md` §5 in this repo).
- It does not share, sell, or transmit Gmail data to any third party.
- Email content sent to an AI provider (Anthropic or OpenAI, see
  `app/ai/`) is used only to help classify that specific message, per each
  provider's own API terms, and is not used to train the developer's own models.

## Data storage

No Gmail data is stored outside of Google's own systems. The only local/
repository state is an encrypted OAuth token and a Gmail history cursor
(a non-sensitive sync position). See `CLAUDE.md` §3 ("Storage") for details.

## Contact

jikahu@gmail.com
