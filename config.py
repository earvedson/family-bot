"""Configuration loaded from environment variables."""

import os
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

# Use LLM to extract school highlights from raw page text (OPENAI_API_KEY required).
# When True, rule-based filtering in school.py is skipped; the LLM does week filtering and relevance.
USE_LLM_EXTRACTION = os.environ.get("USE_LLM_EXTRACTION", "").strip().lower() in ("1", "true", "yes")

# Timezone for calendar week and event display (e.g. Europe/Stockholm). Used for target-week range and day labels.
CALENDAR_TIMEZONE = os.environ.get("CALENDAR_TIMEZONE", "Europe/Stockholm").strip() or "Europe/Stockholm"

# OpenAI model for digest (create_weekly_overview) and school extraction. Must be a Chat Completions model ID.
OPENAI_DIGEST_MODEL = (os.environ.get("OPENAI_DIGEST_MODEL") or "gpt-4o-mini").strip()
