"""``python -m app.scheduling`` — run exactly one real-time poll cycle and exit.

This is :func:`app.scheduling.poller.run_poll_cycle`, the same function
``POST /realtime/poll`` calls, invoked directly with no FastAPI process and no
HTTP hop. It's what ``.github/workflows/realtime-poll.yml`` runs on its cron
schedule: checkout the repo, install dependencies, run this module, done.
There is nothing to keep warm and nothing that can go to sleep, because
nothing stays running between ticks — a fresh GitHub Actions runner does the
one cycle and throws itself away, the same way the old Render process did the
one cycle and went back to sleep.
"""

from __future__ import annotations

import json
import sys

_PLACEHOLDER_SESSION_SECRET = "change-me-to-a-long-random-string"


def main() -> int:
    from app.config import get_settings
    from app.logging_config import configure_logging, get_logger

    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("app.scheduling.__main__")

    if settings.session_secret == _PLACEHOLDER_SESSION_SECRET:
        print(
            "SESSION_SECRET is unset or still the placeholder value — refusing "
            "to run. Generate a real one with: "
            'python -c "import secrets; print(secrets.token_urlsafe(48))" '
            "and set it as a repo secret.",
            file=sys.stderr,
        )
        return 2

    from app.google_api import NotConnectedError
    from app.scheduling.poller import run_poll_cycle

    try:
        report = run_poll_cycle(use_ai=True)
    except NotConnectedError as exc:
        print(f"Gmail not connected: {exc}", file=sys.stderr)
        return 3

    print(json.dumps(report.as_dict(), indent=2))
    log.info(
        "realtime_poll_cli_completed",
        extra={
            "bootstrapped": report.bootstrapped,
            "messages_processed": report.messages_processed,
            "changed_count": report.changed_count,
            "error_count": report.error_count,
        },
    )
    return 0 if report.error_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
