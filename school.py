"""Fetch and parse school class pages for weekly highlights."""

import re
from dataclasses import dataclass
from typing import Optional

import httpx
from bs4 import BeautifulSoup

import config

# Subject headers we split on (order matters for splitting)
SUBJECT_HEADERS = [
    "Svenska",
    "Matematik",
    "Engelska",
    "NO",
    "SO",
    "Idrott och hälsa",
    "Musik",
    "Bild",
    "Slöjd",
    "Franska",
    "Spanska",
    "Tyska",
    "Español",
]

# Keywords that mark important items (prov, läxa, förhör, etc.)
IMPORTANT_KEYWORDS = re.compile(
    r"\b(prov|läxa|förhör|diagnos|inlämning|deadline|tenta|hemuppgift)\b",
    re.IGNORECASE,
)
# Week reference: v. 6, v.7, V.8, vecka 6
WEEK_REF = re.compile(r"\b[vV]\.?\s*\d+|vecka\s*\d+", re.IGNORECASE)


@dataclass
class SchoolInfo:
    """Parsed school page info for one class."""

    child_name: str
    url: str
    week: Optional[int]
    highlights: list[str]
    error: Optional[str] = None


def _get_page_text(url: str, timeout: float = 15.0) -> str:
    """Fetch URL and return main text content."""
    resp = httpx.get(url, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    # Remove script/style
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _extract_week(text: str) -> Optional[int]:
    """Extract current week number from text (e.g. 'Vecka 6')."""
    m = re.search(r"Vecka\s+(\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _relevant_line(line: str) -> bool:
    """True if line contains something we want in the digest (prov, läxa, förhör, week ref)."""
    line = line.strip()
    if not line or len(line) > 500:
        return False
    return bool(IMPORTANT_KEYWORDS.search(line) or WEEK_REF.search(line))


def _parse_page_text(text: str, url: str, child_name: str) -> SchoolInfo:
    """Parse full page text into structured highlights."""
    week = _extract_week(text)
    highlights: list[str] = []

    # Find all subject header positions (start index, header name)
    positions: list[tuple[int, str]] = []
    for header in SUBJECT_HEADERS:
        pattern = re.compile(re.escape(header) + r"\s*:?\s*", re.IGNORECASE)
        for m in pattern.finditer(text):
            positions.append((m.start(), header))
            break  # first occurrence per header
    positions.sort(key=lambda x: x[0])

    for i, (start, header) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        segment = text[start:end]
        # First line is the header; rest is content
        for line in segment.splitlines():
            line = line.strip()
            if not line or line.lower().startswith(header.lower()):
                continue
            if _relevant_line(line):
                highlights.append(f"**{header}:** {line}")
            elif header in ("Idrott och hälsa", "Musik", "Bild") and (
                "ta med" in line.lower() or "dusch" in line.lower() or "ombyte" in line.lower()
            ):
                highlights.append(f"**{header}:** {line}")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for h in highlights:
        if h not in seen:
            seen.add(h)
            unique.append(h)

    return SchoolInfo(
        child_name=child_name,
        url=url,
        week=week,
        highlights=unique,
    )


def fetch_school_info_for_class(label: str, url: str) -> SchoolInfo:
    """Fetch and parse one school class page."""
    try:
        text = _get_page_text(url)
        return _parse_page_text(text, url, label)
    except Exception as e:
        return SchoolInfo(
            child_name=label,
            url=url,
            week=None,
            highlights=[],
            error=str(e),
        )


def fetch_all_school_info() -> list[SchoolInfo]:
    """Fetch and parse all configured school class pages (from SCHOOL_CLASSES)."""
    if not config.SCHOOL_CLASSES:
        return [
            SchoolInfo(
                child_name="(configure SCHOOL_CLASSES)",
                url="",
                week=None,
                highlights=[],
                error="SCHOOL_CLASSES not set in .env (format: label|url,label|url)",
            )
        ]
    return [
        fetch_school_info_for_class(label, url)
        for label, url in config.SCHOOL_CLASSES
    ]
