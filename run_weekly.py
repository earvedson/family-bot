#!/usr/bin/env python3
"""
Run the weekly digest: fetch school info, calendar events, build message, send to Discord.

Run once per week via cron, e.g.:
  Sunday 18:00:  0 18 * * 0  cd /path/to/family-bot && python run_weekly.py
  Monday 07:00:  0 7 * * 1   cd /path/to/family-bot && python run_weekly.py

Without --week: the digest targets the *current* week when run Monday–Friday,
and *next* week when run Saturday or Sunday.
If OPENAI_API_KEY is set, school and calendar data are sent to the LLM, which
produces the full weekly overview. Otherwise the digest is built from templates (build_digest).

Capture mode (for reviewing and improving filtering):
  python run_weekly.py --dry-run              # Write digest to digest_preview.txt, do not send
  python run_weekly.py --dry-run -o out.txt  # Write to out.txt instead

Any week (default: current week Mon–Fri, next week Sat–Sun):
  python run_weekly.py --dry-run --week 8     # Digest for ISO week 8 (current year)
  python run_weekly.py --dry-run -w 10 -y 2025  # Digest for week 10 of 2025

Check-updates dry-run (see what would be sent without sending or updating snapshot):
  python run_weekly.py --check-updates --dry-run
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
    get_new_school_items_only,
    _raw_blocks_to_school_infos,
)
from snapshot import (
    build_snapshot,
    diff_snapshots,
    format_notification,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)

DEFAULT_PREVIEW_FILE = "digest_preview.txt"


def _next_week_number() -> int:
    """ISO week number for the week after today."""
    next_week_date = date.today() + timedelta(days=7)
    return next_week_date.isocalendar().week


def _default_target_week_and_reference() -> tuple[int, date]:
    """Default target week and reference_date by weekday. Mon–Fri → current week; Sat–Sun → next week."""
    today = date.today()
    if today.weekday() <= 4:  # Monday=0 .. Friday=4
        iso_year, target_week, _ = today.isocalendar()
        monday_of_week = date.fromisocalendar(iso_year, target_week, 1)
        reference_date = monday_of_week - timedelta(days=7)
        return target_week, reference_date
    # Saturday=5, Sunday=6 → next week
    target_week = _next_week_number()
    return target_week, today


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run weekly digest (send to Discord or write to file for review)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not send to Discord or update snapshot. With default mode: build digest and write to file. With --check-updates: print what would be sent, do not send or update snapshot.",
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
        help="ISO week number to use (default: current week Mon–Fri, next week Sat–Sun). Use with --year to pick year.",
    )
    parser.add_argument(
        "-y",
        "--year",
        metavar="Y",
        type=int,
        default=None,
        help="ISO year for --week (default: current year when --week is set).",
    )
    parser.add_argument(
        "--check-updates",
        action="store_true",
        help="Weekday mode: fetch current week, diff with stored snapshot, notify if changes, then update snapshot.",
    )
    parser.add_argument(
        "--save-snapshot",
        action="store_true",
        help="Save a snapshot for the target week (e.g. with --dry-run to test without sending).",
    )
    args = parser.parse_args()

    # Weekday check-updates path: current week only, diff and notify
    if args.check_updates:
        today = date.today()
        iso_year, target_week, _ = today.isocalendar()
        monday_of_week = date.fromisocalendar(iso_year, target_week, 1)
        reference_date = monday_of_week - timedelta(days=7)
        if config.USE_LLM_EXTRACTION:
            raw_blocks = [
                (pn, cl, raw_text, err)
                for pn, cl, _url, raw_text, err in fetch_all_raw_school_texts()
            ]
            school_infos = None
        else:
            school_infos = fetch_all_school_info(target_week=target_week)
            raw_blocks = None
        try:
            events_by_person = fetch_events_for_week(target_week, reference_date=reference_date)
        except Exception as e:
            print(f"Calendar fetch failed: {e}", file=sys.stderr)
            events_by_person = []
        current = build_snapshot(
            school_infos,
            raw_blocks,
            events_by_person,
            target_week,
            iso_year,
            config.USE_LLM_EXTRACTION,
        )
        stored = load_snapshot(iso_year, target_week)
        if stored is None:
            print(f"No snapshot for week {target_week} ({iso_year}). Run full digest first (e.g. Sunday).", file=sys.stderr)
            return 0
        school_changed, new_events = diff_snapshots(stored, current)
        if not school_changed and not new_events:
            if args.dry_run:
                print("Dry-run: no changes; would not send.", file=sys.stderr)
            return 0
        school_updates: dict[str, list[str]] = {}
        if school_changed:
            if "school_highlights" in stored and "school_highlights" in current:
                for p in school_changed:
                    cur_set = set(current["school_highlights"].get(p) or [])
                    stored_set = set(stored["school_highlights"].get(p) or [])
                    new_lines = list(cur_set - stored_set)
                    if new_lines:
                        school_updates[p] = new_lines
            elif "school_digest_highlights" in stored and raw_blocks is not None:
                person_to_raw = {pn: (raw or "") for (pn, _, raw, _) in raw_blocks}
                for p in school_changed:
                    previous = stored["school_digest_highlights"].get(p) or []
                    raw_text = person_to_raw.get(p) or ""
                    new_lines = get_new_school_items_only(p, previous, raw_text, target_week)
                    if new_lines:
                        school_updates[p] = new_lines
        msg = format_notification(
            target_week, iso_year, school_changed, new_events, school_updates=school_updates or None
        )
        if args.dry_run:
            print("Dry-run: would send the following (Discord not called, snapshot not updated):", file=sys.stderr)
            print(msg)
            return 0
        if not config.DISCORD_WEBHOOK_URL:
            print("DISCORD_WEBHOOK_URL not set. Notification (not sent):", file=sys.stderr)
            print(msg)
            return 1
        try:
            send_digest(msg)
            # Preserve school_digest_highlights so next run has updated "previous" baseline
            if "school_digest_highlights" in stored:
                current["school_digest_highlights"] = dict(stored.get("school_digest_highlights") or {})
                for p, lines in school_updates.items():
                    current["school_digest_highlights"].setdefault(p, []).extend(lines)
            save_snapshot(current)
            print("Updates sent to Discord; snapshot updated.", file=sys.stderr)
        except Exception as e:
            print(f"Failed to send notification: {e}", file=sys.stderr)
            return 1
        return 0

    if args.week is not None:
        target_week = args.week
        year = args.year if args.year is not None else date.today().isocalendar()[0]
        # reference_date so that (reference_date + 7) falls in target_week of year
        monday_of_week = date.fromisocalendar(year, target_week, 1)
        reference_date = monday_of_week - timedelta(days=7)
    else:
        # Mon–Fri → current week; Sat–Sun → next week
        target_week, reference_date = _default_target_week_and_reference()

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

    if args.save_snapshot:
        iso_year = (reference_date + timedelta(days=7)).isocalendar()[0]
        snapshot = build_snapshot(
            school_infos,
            raw_blocks,
            events_by_person,
            target_week,
            iso_year,
            config.USE_LLM_EXTRACTION,
            digest_body=body,
        )
        save_snapshot(snapshot)
        print(f"Snapshot saved to {snapshot_path(iso_year, target_week)}", file=sys.stderr)

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
        iso_year = (reference_date + timedelta(days=7)).isocalendar()[0]
        snapshot = build_snapshot(
            school_infos,
            raw_blocks,
            events_by_person,
            target_week,
            iso_year,
            config.USE_LLM_EXTRACTION,
            digest_body=body,
        )
        save_snapshot(snapshot)
        return 0
    except Exception as e:
        print(f"Failed to send to Discord: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
