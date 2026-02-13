#!/usr/bin/env python3
"""
Run the weekly digest: fetch school info, calendar events, build message, send to Discord.

Run once per week via cron, e.g.:
  Sunday 18:00:  0 18 * * 0  cd /path/to/family-bot && python run_weekly.py
  Monday 07:00:  0 7 * * 1   cd /path/to/family-bot && python run_weekly.py
"""

import sys

import config
from school import fetch_all_school_info
from calendar import fetch_events_next_week
from digest import build_digest
from discord_notify import send_digest


def main() -> int:
    school_infos = fetch_all_school_info()
    events = []
    calendar_error = None
    try:
        events = fetch_events_next_week()
    except Exception as e:
        calendar_error = str(e)

    body = build_digest(school_infos, events, calendar_error=calendar_error)

    if not config.DISCORD_WEBHOOK_URL:
        print("DISCORD_WEBHOOK_URL is not set. Digest (not sent):", file=sys.stderr)
        print(body)
        return 1

    try:
        send_digest(body)
        print("Digest sent to Discord.")
        return 0
    except Exception as e:
        print(f"Failed to send to Discord: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
