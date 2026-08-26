"""
Week snapshot for Sunday capture and weekday diff notifications.

Snapshot format (JSON): format_version, iso_year, target_week, captured_at,
school_highlights, school_tests, calendar (events).
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import config

# events_by_person: list of (person_name, list[CalendarEvent])
# CalendarEvent has summary, start (datetime), end (optional), location (optional)

# Bump when the snapshot dict shape changes. load_snapshot() treats a missing/mismatched
# version as "no prior snapshot" (same as a missing file) rather than crashing on unfamiliar
# keys or reporting an entire week as new against a snapshot built by old code.
SNAPSHOT_FORMAT_VERSION = 2


def parse_school_section_from_digest(digest_body: str, person_names: list[str]) -> dict[str, list[str]]:
    """
    Parse the ## Skola section from a digest and return per-person highlight lines.
    person_names: list of names (e.g. from config.PERSON_SCHOOL first element).
    """
    out: dict[str, list[str]] = {p: [] for p in person_names}
    if not digest_body or not person_names:
        return out
    # Find ## Skola ... ## Kalender (or end); allow optional space after ##
    skola_match = re.search(r"##\s*Skola\b", digest_body)
    if not skola_match:
        return out
    rest = digest_body[skola_match.start() :]
    kal_match = re.search(r"##\s*Kalender\b", rest)
    section = rest[: kal_match.start()] if kal_match else rest
    lines = section.splitlines()
    current_person: str | None = None
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip leading "- " from list items
        if line.startswith("- "):
            line = line[2:].strip()
        # Person heading: **Name (Class):** or **Name:** or **Name (Class)** (no colon)
        if line.startswith("**"):
            if ":**" in line:
                head, _, tail = line[2:].partition(":**")
                before_colon = head.strip()
            elif line.rstrip().endswith("**"):
                before_colon = line[2:-2].strip()
                tail = ""
            else:
                before_colon = ""
                tail = ""
            matched = None
            for p in person_names:
                if before_colon == p or before_colon.startswith(p + " ") or before_colon.startswith(p + "("):
                    current_person = p
                    matched = p
                    break
            if matched is not None:
                # Only clear current_person when we see a real person heading; don't clear on **Svenska:** etc.
                # Same-line content after person heading, e.g. "**Olle (8B):** **Svenska:** ..."
                if tail.strip() and "**" in tail and ":" in tail:
                    out.setdefault(matched, []).append(tail.strip())
                continue
            # Not a person heading (e.g. **Svenska:** ...) – treat as content line below, don't clear current_person
        # Content line: **Ämne:** description (highlight)
        if current_person and "**" in line and ":" in line:
            out.setdefault(current_person, []).append(line)
    return out


def _event_key(ev: dict) -> tuple[str, str, str]:
    return (ev["person"], ev["start"], ev["summary"])


def build_snapshot(
    school_infos: list | None,
    events_by_person: list[tuple[str, list]],
    target_week: int,
    iso_year: int,
    digest_body: str | None = None,
) -> dict:
    """
    Build a snapshot dict for the given week.
    school_infos: list of SchoolInfo.
    events_by_person: list of (person_name, list[CalendarEvent]).
    digest_body: if provided, parse ## Skola and store school_digest_highlights (what we sent).
    """
    snapshot: dict[str, Any] = {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "iso_year": iso_year,
        "target_week": target_week,
        "captured_at": datetime.now().isoformat(),
        "calendar": [],
    }
    if digest_body:
        person_names = [p[0] for p in config.PERSON_SCHOOL]
        snapshot["school_digest_highlights"] = parse_school_section_from_digest(digest_body, person_names)
    if school_infos is not None:
        snapshot["school_highlights"] = {}
        snapshot["school_tests"] = {}
        for info in school_infos:
            snapshot["school_highlights"][info.person_name] = list(info.highlights or [])
            # "weekday|description" strings so a newly-scheduled test is diffable regardless of
            # where/how the digest chooses to render it (see SCHOOL_SCRAPING_PLAN.md §4).
            snapshot["school_tests"][info.person_name] = [
                f"{weekday}|{desc}" for weekday, desc in (getattr(info, "tests", None) or [])
            ]
    for person_name, events in events_by_person:
        person = person_name or "Övrigt"
        for e in events:
            ev = {
                "person": person,
                "summary": e.summary or "",
                "start": e.start.isoformat(),
                "end": e.end.isoformat() if e.end else None,
                "location": e.location or None,
            }
            snapshot["calendar"].append(ev)
    return snapshot


def snapshot_path(iso_year: int, target_week: int) -> Path:
    """Path to the snapshot file for the given week."""
    base = Path(config.DIGEST_SNAPSHOT_DIR)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"week_{iso_year}_{target_week}.json"


def save_snapshot(snapshot: dict, path: Path | None = None) -> None:
    """Write snapshot to path (default: from snapshot iso_year/target_week)."""
    if path is None:
        path = snapshot_path(snapshot["iso_year"], snapshot["target_week"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshot(iso_year: int, target_week: int) -> dict | None:
    """Load snapshot for the given week; None if missing or from an incompatible older format."""
    path = snapshot_path(iso_year, target_week)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format_version") != SNAPSHOT_FORMAT_VERSION:
        return None
    return data


def diff_snapshots(
    stored: dict,
    current: dict,
) -> tuple[list[str], list[dict]]:
    """
    Compare stored and current snapshots.
    Returns (school_changed_persons, new_calendar_events).
    School: list of person names with new highlight lines and/or new tests (any addition counts
    as "changed" for that person - highlights and tests are diffed independently so a newly
    scheduled test is caught even though it isn't rendered as a highlight).
    Calendar: list of event dicts that are in current but not in stored (by person+start+summary).
    """
    school_changed: list[str] = []
    if "school_highlights" in stored and "school_highlights" in current:
        for person, cur_highlights in current["school_highlights"].items():
            stored_set = set(stored["school_highlights"].get(person) or [])
            cur_set = set(cur_highlights or [])
            if cur_set - stored_set:
                school_changed.append(person)
    if "school_tests" in stored and "school_tests" in current:
        for person, cur_tests in current["school_tests"].items():
            stored_set = set(stored["school_tests"].get(person) or [])
            cur_set = set(cur_tests or [])
            if (cur_set - stored_set) and person not in school_changed:
                school_changed.append(person)
    stored_keys = {_event_key(e) for e in stored.get("calendar") or []}
    new_events: list[dict] = []
    for e in current.get("calendar") or []:
        if _event_key(e) not in stored_keys:
            new_events.append(e)
    return school_changed, new_events


def format_notification(
    target_week: int,
    iso_year: int,
    school_changed: list[str],
    new_events: list[dict],
    school_updates: dict[str, list[str]] | None = None,
) -> str:
    """Build a short Discord message for the diff. school_updates: optional per-person new highlight lines."""
    parts = [f"**Vecka {target_week} ({iso_year}) – uppdateringar**", ""]
    if school_changed:
        parts.append("## Skola")
        parts.append("")
        for p in school_changed:
            new_lines = school_updates.get(p) if school_updates else None
            if new_lines:
                parts.append(f"**{p}:**")
                for line in new_lines:
                    parts.append(line)
                parts.append("")
            else:
                parts.append(f"**{p}:** (sida ändrad)")
                parts.append("")
    if new_events:
        parts.append("## Kalender")
        parts.append("")
        shown = new_events[:15]
        by_person: dict[str, list[dict]] = {}
        for e in shown:
            person = e.get("person", "?")
            by_person.setdefault(person, []).append(e)
        for person in by_person:
            parts.append(f"**{person}:**")
            for e in by_person[person]:
                start = e.get("start", "")
                summary = e.get("summary", "")
                try:
                    dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    time_str = dt.strftime("%a %d/%m %H:%M")
                except Exception:
                    time_str = start[:16] if len(start) >= 16 else start
                parts.append(f"• {time_str} – {summary}")
            parts.append("")
        if len(new_events) > 15:
            parts.append(f"... och {len(new_events) - 15} till.")
    return "\n".join(parts)
