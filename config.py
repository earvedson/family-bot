"""Configuration loaded from environment variables."""

import os
from pathlib import Path

# Load .env file if present (simple parse, no extra dependency)
_env_path = Path(__file__).resolve().parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

# School classes: one entry per class. Format: label|url,label|url
# Example: ClassA|https://example.com/school/a,ClassB|https://example.com/school/b
# Labels are shown in the digest; update when classes or school year change.
_school_raw = os.environ.get("SCHOOL_CLASSES", "")
SCHOOL_CLASSES: list[tuple[str, str]] = []
for entry in _school_raw.split(","):
    entry = entry.strip()
    if not entry:
        continue
    if "|" in entry:
        label, _, url = entry.partition("|")
        label, url = label.strip(), url.strip()
        if label and url:
            SCHOOL_CLASSES.append((label, url))

# ICS calendar URLs: comma-separated list
_ics = os.environ.get("ICS_URLS", "")
ICS_URLS = [u.strip() for u in _ics.split(",") if u.strip()]

# Discord webhook URL (required for sending)
DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
