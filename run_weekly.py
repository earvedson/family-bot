#!/usr/bin/env python3
"""
Run the weekly digest: fetch school info, calendar events, build message, send to Discord.

Run once per week via cron, e.g.:
  Sunday 18:00:  0 18 * * 0  cd /path/to/family-bot && python run_weekly.py
  Monday 07:00:  0 7 * * 1   cd /path/to/family-bot && python run_weekly.py

When run (e.g. Sunday), the digest focuses on *next* week (ISO week number).
If OPENAI_API_KEY is set, school and calendar data are sent to the LLM, which
produces the full weekly overview. Otherwise the digest is built from templates (build_digest).

Capture mode (for reviewing and improving filtering):
  python run_weekly.py --dry-run              # Write digest to digest_preview.txt, do not send
  python run_weekly.py --dry-run -o out.txt  # Write to out.txt instead

Any week (default is next week):
  python run_weekly.py --dry-run --week 8     # Digest for ISO week 8 (current year)
  python run_weekly.py --dry-run -w 10 -y 2025  # Digest for week 10 of 2025
"""

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import config
from school import fetch_all_school_info, fetch_all_raw_school_texts
from cal_fetcher import fetch_events_for_week
from digest import build_digest
from discord_notify import send_digest
from llm_improve import (
    create_weekly_overview,
    create_weekly_overview_from_raw,
    _raw_blocks_to_school_infos,
)

DEFAULT_PREVIEW_FILE = "digest_preview.txt"


def _next_week_number() -> int:
    """ISO week number for the week after today (the week we're summarising for)."""
    next_week_date = date.today() + timedelta(days=7)
    return next_week_date.isocalendar().week


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run weekly digest (send to Discord or write to file for review)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build digest and write to file; do not send to Discord. Use to review and tune filtering.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=DEFAULT_PREVIEW_FILE,
        help=f"Output file for --dry-run (default: {DEFAULT_PREVIEW_FILE})",
    )
    parser.add_argument(
        "-w",
        "--week",
        metavar="N",
        type=int,
        default=None,
        help="ISO week number to use (default: next week). Use with --year to pick year.",
    )
    parser.add_argument(
        "-y",
        "--year",
        metavar="Y",
        type=int,
        default=None,
        help="ISO year for --week (default: current year when --week is set).",
    )
    args = parser.parse_args()

    if args.week is not None:
        target_week = args.week
        year = args.year if args.year is not None else date.today().isocalendar()[0]
        # reference_date so that (reference_date + 7) falls in target_week of year
        monday_of_week = date.fromisocalendar(year, target_week, 1)
        reference_date = monday_of_week - timedelta(days=7)
    else:
        target_week = _next_week_number()
        reference_date = date.today()

    if config.USE_LLM_EXTRACTION:
        raw_blocks = [
            (person_name, class_label, raw_text, err)
            for person_name, class_label, _url, raw_text, err in fetch_all_raw_school_texts()
        ]
        school_infos = None
    else:
        school_infos = fetch_all_school_info(target_week=target_week)
        raw_blocks = None

    events_by_person: list[tuple[str, list]] = []
    calendar_error = None
    try:
        events_by_person = fetch_events_for_week(target_week, reference_date=reference_date)
    except Exception as e:
        calendar_error = str(e)

    if config.USE_LLM_EXTRACTION:
        has_openai_key = bool(os.environ.get("OPENAI_API_KEY", "").strip())
        if has_openai_key:
            body = create_weekly_overview_from_raw(
                raw_blocks,
                events_by_person,
                target_week,
                calendar_error=calendar_error,
                reference_date=reference_date,
            )
        else:
            print("OPENAI_API_KEY not set; using template digest.", file=sys.stderr)
            school_infos = _raw_blocks_to_school_infos(raw_blocks, target_week)
            body = build_digest(
                school_infos,
                events_by_person,
                calendar_error=calendar_error,
                target_week=target_week,
                reference_date=reference_date,
            )
    elif os.environ.get("OPENAI_API_KEY", "").strip():
        body = create_weekly_overview(
            school_infos,
            events_by_person,
            target_week,
            calendar_error=calendar_error,
            reference_date=reference_date,
        )
    else:
        body = build_digest(
            school_infos,
            events_by_person,
            calendar_error=calendar_error,
            target_week=target_week,
            reference_date=reference_date,
        )

    if args.dry_run:
        out_path = Path(args.output)
        out_path.write_text(body, encoding="utf-8")
        week_year = (reference_date + timedelta(days=7)).isocalendar()[0]
        print(f"Target week: {target_week} (year {week_year})", file=sys.stderr)
        print(f"Preview written to: {out_path.absolute()}", file=sys.stderr)
        print("(Not sent to Discord. Adjust filtering in school.py / llm_improve.py and run again.)", file=sys.stderr)
        return 0

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
