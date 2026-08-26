"""Configuration loaded from environment variables."""

import os
import re
from pathlib import Path

# Load .env file if present (simple parse, no extra dependency). .env overrides existing env so changes take effect.
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.split("#")[0].strip().strip('"').strip("'")  # drop inline comments
            if key:
                os.environ[key] = value

# Person + school class: one entry per person who has a class page. Format: Name|ClassLabel|URL
# Example: Alice|6B|https://...,Bob|8B|https://... (names and class labels are configurable)
# Fallback: if PERSON_SCHOOL empty but SCHOOL_CLASSES set, use Label|URL as (Label, Label, URL)
_person_school_raw = os.environ.get("PERSON_SCHOOL", "")
_school_classes_raw = os.environ.get("SCHOOL_CLASSES", "")
PERSON_SCHOOL: list[tuple[str, str, str]] = []
if _person_school_raw:
    for entry in _person_school_raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = [p.strip() for p in entry.split("|")]
        if len(parts) >= 3 and all(parts[:3]):
            PERSON_SCHOOL.append((parts[0], parts[1], parts[2]))
elif _school_classes_raw:
    for entry in _school_classes_raw.split(","):
        entry = entry.strip()
        if not entry or "|" not in entry:
            continue
        label, _, url = entry.partition("|")
        label, url = label.strip(), url.strip()
        if label and url:
            PERSON_SCHOOL.append((label, label, url))

# Person(s) + calendar: Names|URL. Names = one person or "Name1;Name2" for shared calendar.
# Same name can appear in multiple entries for multiple calendars.
# Example: Alice|https://...,Bob|https://...,Alice;Bob|https://... (last = shared)
_person_cal_raw = os.environ.get("PERSON_CALENDARS", "")
PERSON_CALENDARS: list[tuple[list[str], str]] = []
for entry in _person_cal_raw.split(","):
    entry = entry.strip()
    if not entry or "|" not in entry:
        continue
    names_part, _, url = entry.partition("|")
    url = url.strip()
    names = [n.strip() for n in names_part.split(";") if n.strip()]
    if names and url:
        PERSON_CALENDARS.append((names, url))

# Fallback: global ICS URLs (no person); used when PERSON_CALENDARS is empty
_ics = os.environ.get("ICS_URLS", "")
ICS_URLS = [u.strip() for u in _ics.split(",") if u.strip()]

# Discord webhook URL (required for sending)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Timezone for calendar week and event display (e.g. Europe/Stockholm). Used for target-week range and day labels.
CALENDAR_TIMEZONE = os.environ.get("CALENDAR_TIMEZONE", "Europe/Stockholm").strip() or "Europe/Stockholm"

# Claude model for digest writing (create_weekly_overview). Must be a Messages API model ID.
ANTHROPIC_DIGEST_MODEL = (os.environ.get("ANTHROPIC_DIGEST_MODEL") or "claude-sonnet-5").strip()

# Directory for week snapshots (Sunday capture; weekday diff). Default: .digest_snapshots
DIGEST_SNAPSHOT_DIR = (
    os.environ.get("DIGEST_SNAPSHOT_DIR", "").strip()
    or str(Path(__file__).resolve().parent / ".digest_snapshots")
)


def _person_env_key(prefix: str, person_name: str) -> str:
    return prefix + re.sub(r"[^A-Za-z0-9]+", "_", person_name).upper().strip("_")


def get_special_info(person_name: str) -> str | None:
    """
    Return optional per-person special info (e.g. subject swaps) from env. Purely a human-readable
    note shown in the digest - it does not affect what's extracted; see get_suppressed_subjects
    for actually filtering a subject out.
    Key: SPECIAL_INFO_<NAME> with name uppercased and non-alphanumeric chars replaced by underscore.
    Person name must match the name used in PERSON_SCHOOL.
    """
    value = (os.environ.get(_person_env_key("SPECIAL_INFO_", person_name)) or "").strip()
    return value if value else None


def get_suppressed_subjects(person_name: str) -> set[str]:
    """
    Return the set of subject names (normalized: stripped, casefolded) to leave out of this
    person's Skola highlights entirely - e.g. a subject they don't take. Matched case-insensitively
    against the Veckoplanering "Ämne" column, so it doesn't need to match the site's exact casing.
    Key: SUPPRESS_SUBJECTS_<NAME> (same NAME normalization as SPECIAL_INFO_<NAME>),
    comma-separated subject names. Combine with SPECIAL_INFO_<NAME> for a human-readable note
    explaining why (e.g. "Franska, Tyska (har Spanska istället)").
    """
    value = (os.environ.get(_person_env_key("SUPPRESS_SUBJECTS_", person_name)) or "").strip()
    if not value:
        return set()
    return {s.strip().casefold() for s in value.split(",") if s.strip()}
