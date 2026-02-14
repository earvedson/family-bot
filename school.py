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
# Week reference: v. 6, v.7, V.8, vecka 6, Week 8 (English)
WEEK_REF = re.compile(
    r"\b(?:[vV]\.?\s*\d+|vecka\s*\d+|week\s*\d+)",
    re.IGNORECASE,
)
# Week range: v7-11, v.3 - 6, Week 3 - 8
WEEK_RANGE = re.compile(r"(\d+)\s*-\s*(\d+)")
# Extract week number from a week ref (e.g. "v. 6" -> 6)
WEEK_NUM = re.compile(r"\d+")

# Lines with no week ref that are too generic (likely from past-week blocks) – skip
GENERIC_NO_WEEK_PHRASES = frozenset(
    s.strip().lower()
    for s in (
        "Prov",
        "Prov.",
        "Ingen läxa",
        "Ingen läxa.",
    )
)
# Start of line that is generic Classroom promo (not week-specific)
CLASSROOM_PROMO_PATTERN = re.compile(
    r"^[\s\-]*här finns planering för (kapitlet|kapitel)",
    re.IGNORECASE,
)
# Short week-range line (e.g. "Week 3 - 8") where we want to pull in the next line as context
WEEK_RANGE_ONLY_LINE = re.compile(
    r"^(Week\s+\d+\s*-\s*\d+|v\d+\s*-\s*\d+)\s*$",
    re.IGNORECASE,
)
MAX_FOLLOW_LINE_LEN = 220  # cap context for Engelska follow-line(s)
MAX_FOLLOW_LINES = 5  # max lines of context after week-range line (then truncate)


@dataclass
class SchoolInfo:
    """Parsed school page info for one person's class."""

    person_name: str
    class_label: Optional[str]  # e.g. 6B; shown as "Name (6B)" in digest when set
    url: str
    week: Optional[int]
    highlights: list[str]
    error: Optional[str] = None


def _get_page_text(url: str, timeout: float = 15.0) -> str:
    """Fetch URL and return main text content. Strikethrough content is removed."""
    resp = httpx.get(url, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    # Remove strikethrough (old/deprecated) so it doesn't appear in highlights
    for tag in soup.find_all(["s", "strike", "del"]):
        tag.decompose()
    for tag in soup.find_all(
        lambda t: t.get("style") and "line-through" in (t.get("style") or "").lower()
    ):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _extract_week(text: str) -> Optional[int]:
    """Extract current week number from text (e.g. 'Vecka 6' or 'Vecka6')."""
    m = re.search(r"Vecka\s*(\d+)", text, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _relevant_line(line: str) -> bool:
    """True if line contains something we want in the digest (prov, läxa, förhör, week ref)."""
    line = line.strip()
    if not line or len(line) > 500:
        return False
    return bool(IMPORTANT_KEYWORDS.search(line) or WEEK_REF.search(line))


def _all_week_numbers_in_line(line: str) -> list[int]:
    """Extract all week numbers mentioned in a line (refs like v.6 and ranges like v7-11)."""
    numbers: set[int] = set()
    for m in WEEK_REF.finditer(line):
        num_match = WEEK_NUM.search(m.group(0))
        if num_match:
            numbers.add(int(num_match.group(0)))
    for m in WEEK_RANGE.finditer(line):
        numbers.add(int(m.group(1)))
        numbers.add(int(m.group(2)))
    return sorted(numbers)


def _is_generic_no_week_line(line: str) -> bool:
    """True if line has no week ref and is a known generic phrase we should skip."""
    if _all_week_numbers_in_line(line):
        return False  # Has week ref – keep/week filter decides
    normalized = " ".join(line.strip().lower().split())
    if normalized in GENERIC_NO_WEEK_PHRASES:
        return True
    if CLASSROOM_PROMO_PATTERN.search(line):
        return True
    return False


def _line_applies_to_week(line: str, target_week: int) -> bool:
    """
    True if this line should be kept for target_week (rule-based filter).

    - "denna vecka" / "nästa vecka" -> keep.
    - No week ref -> keep (ambiguous).
    - If any week in [target_week-1, target_week+1] -> keep (e.g. Week 3-8, v7-11).
    - If all mentioned weeks are strictly in the past (< target_week) -> drop.
    - Otherwise -> drop (only future weeks).
    """
    line_lower = line.lower()
    if "denna vecka" in line_lower or "nästa vecka" in line_lower:
        return True
    weeks = _all_week_numbers_in_line(line)
    if not weeks:
        return True  # No week ref -> keep
    in_window = any(w in (target_week - 1, target_week, target_week + 1) for w in weeks)
    if in_window:
        return True  # e.g. "Week 3 - 8" or "v7-11" for target 8
    if all(w < target_week for w in weeks):
        return False  # Only past weeks (e.g. V. 4 when target is 8)
    return False  # Only future weeks


def _filter_highlights_for_week(highlights: list[str], target_week: int) -> list[str]:
    """Keep only highlights that apply to target_week (or target_week+1)."""
    return [h for h in highlights if _line_applies_to_week(h, target_week)]


def _parse_page_text(text: str, url: str, person_name: str, class_label: Optional[str]) -> SchoolInfo:
    """Parse full page text into structured highlights."""
    week = _extract_week(text)
    highlights: list[str] = []

    # Find all subject header positions (start index, header name).
    # Use word boundary so "NO" doesn't match inside "diagnos".
    positions: list[tuple[int, str]] = []
    for header in SUBJECT_HEADERS:
        pattern = re.compile(
            r"\b" + re.escape(header) + r"\s*:?\s*",
            re.IGNORECASE,
        )
        for m in pattern.finditer(text):
            positions.append((m.start(), header))
            break  # first occurrence per header
    positions.sort(key=lambda x: x[0])

    for i, (start, header) in enumerate(positions):
        end = positions[i + 1][0] if i + 1 < len(positions) else len(text)
        segment = text[start:end]
        lines = segment.splitlines()
        skip_count = 0
        for idx, raw_line in enumerate(lines):
            if skip_count > 0:
                skip_count -= 1
                continue
            line = raw_line.strip().lstrip(":")
            if not line or line.lower().startswith(header.lower()):
                continue
            line = " ".join(line.split())
            if _is_generic_no_week_line(line):
                continue
            if _relevant_line(line):
                # For Engelska: if this is a short week-range line, add next line as context
                if (
                    header == "Engelska"
                    and WEEK_RANGE_ONLY_LINE.match(line)
                    and idx + 1 < len(lines)
                ):
                    # Use raw segment text after this line (robust to HTML line breaks)
                    line_start = segment.find(line) if line in segment else segment.find(raw_line.strip())
                    if line_start == -1:
                        line_start = 0
                    rest = segment[line_start + len(line):].strip()
                    # Cut at next section (line that starts with NO:, Classroom:, or subject)
                    take = []
                    for ln in rest.split("\n"):
                        ln = ln.strip()
                        if not ln:
                            continue
                        if ln.lower().startswith("classroom:") or any(
                            ln.lower().startswith(h.lower() + ":") or ln.lower().startswith(h.lower() + " ")
                            for h in SUBJECT_HEADERS
                        ):
                            break
                        take.append(ln)
                    rest = " ".join(take)
                    if rest and len(rest) > 10:
                        if len(rest) > MAX_FOLLOW_LINE_LEN:
                            rest = rest[: MAX_FOLLOW_LINE_LEN - 3].rstrip() + "..."
                        highlights.append(f"**{header}:** {line}. {rest}")
                        skip_count = sum(1 for j in range(idx + 1, len(lines)) if lines[j].strip())
                        continue
                    # Fallback: collect by lines
                    follow_parts = []
                    for j in range(idx + 1, len(lines)):
                        part = " ".join(lines[j].strip().split())
                        if not part:
                            continue
                        if part.lower().startswith("classroom:") or any(
                            part.lower().startswith(h.lower() + ":")
                            for h in SUBJECT_HEADERS
                        ):
                            break
                        follow_parts.append(part)
                    follow = " ".join(follow_parts)
                    if follow and len(follow) > 10:
                        if len(follow) > MAX_FOLLOW_LINE_LEN:
                            follow = follow[: MAX_FOLLOW_LINE_LEN - 3].rstrip() + "..."
                        highlights.append(f"**{header}:** {line}. {follow}")
                        skip_count = len(follow_parts)
                        continue
                highlights.append(f"**{header}:** {line}")
            elif header in ("Idrott och hälsa", "Musik", "Bild") and (
                "ta med" in line.lower() or "dusch" in line.lower() or "ombyte" in line.lower()
            ):
                highlights.append(f"**{header}:** {line}")

    # Deduplicate: normalize whitespace so "Prov  ->" and "Prov ->" merge
    seen: set[str] = set()
    unique: list[str] = []
    for h in highlights:
        normalized = " ".join(h.split())
        if normalized not in seen:
            seen.add(normalized)
            unique.append(h)

    return SchoolInfo(
        person_name=person_name,
        class_label=class_label,
        url=url,
        week=week,
        highlights=unique,
    )


def fetch_school_info_for_person(
    person_name: str,
    class_label: Optional[str],
    url: str,
    target_week: Optional[int] = None,
) -> SchoolInfo:
    """Fetch and parse one school class page for a person."""
    try:
        text = _get_page_text(url)
        info = _parse_page_text(text, url, person_name, class_label)
        if target_week is not None and info.highlights:
            info = SchoolInfo(
                person_name=info.person_name,
                class_label=info.class_label,
                url=info.url,
                week=info.week,
                highlights=_filter_highlights_for_week(info.highlights, target_week),
                error=info.error,
            )
        return info
    except Exception as e:
        return SchoolInfo(
            person_name=person_name,
            class_label=class_label,
            url=url,
            week=None,
            highlights=[],
            error=str(e),
        )


def fetch_all_school_info(target_week: Optional[int] = None) -> list[SchoolInfo]:
    """
    Fetch and parse all configured person school pages (from PERSON_SCHOOL).

    If target_week is set (e.g. next week's ISO week), highlights are filtered
    to lines that mention that week or target_week+1, or "denna/nästa vecka",
    or have no week reference. Use with LLM improvement for best results on
    unstructured teacher text.
    """
    if not config.PERSON_SCHOOL:
        return [
            SchoolInfo(
                person_name="(configure PERSON_SCHOOL)",
                class_label=None,
                url="",
                week=None,
                highlights=[],
                error="PERSON_SCHOOL not set in .env (format: Name|ClassLabel|URL,...)",
            )
        ]
    return [
        fetch_school_info_for_person(person_name, class_label, url, target_week=target_week)
        for person_name, class_label, url in config.PERSON_SCHOOL
    ]


def get_raw_page_text(url: str, timeout: float = 15.0) -> str:
    """Fetch URL and return full page text (strikethrough removed). For LLM extraction."""
    return _get_page_text(url, timeout=timeout)


def fetch_all_raw_school_texts() -> list[tuple[str, Optional[str], str, Optional[str], Optional[str]]]:
    """
    Fetch raw page text for all configured school pages.
    Returns list of (person_name, class_label, url, raw_text, error).
    raw_text is None if fetch failed (error set).
    """
    if not config.PERSON_SCHOOL:
        return []
    out: list[tuple[str, Optional[str], str, Optional[str], Optional[str]]] = []
    for person_name, class_label, url in config.PERSON_SCHOOL:
        try:
            text = _get_page_text(url)
            out.append((person_name, class_label, url, text, None))
        except Exception as e:
            out.append((person_name, class_label, url, None, str(e)))
    return out
