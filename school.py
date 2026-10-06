"""
Fetch and parse school class pages for weekly highlights.

The class landing page (Google Sites) is just a directory: it links out to two Google Docs that
hold the actual content, both exported as HTML tables (works anonymously for public docs):

- "Provschema": a term-long test schedule shared across a whole "lag" (e.g. 6B/7B/8B/9B all read
  the same doc). One table per ISO week: weekday rows x class-label columns.
- "Veckoplanering <class>": the class-specific weekly plan, one doc reused every week. One block
  per week: an "Aktuell information" note + a subject table (Ämne | Veckans planering | Övrigt/Läxor).

Both docs are teacher-edited and error prone (typos, forgotten renumbering, copy-paste). The
parser is tolerant of that (see parse_veckoplanering) but never silently shows a different week's
content under the target week's heading — see SchoolInfo.status.
"""

import re
import sys
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup, NavigableString

import config

# How many weeks beyond target_week to look ahead in Provschema for SchoolInfo.upcoming_tests.
UPCOMING_TEST_WEEKS_AHEAD = 2

_WEEKDAYS_SV = ("Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag")

_LINK_HREF_RE = re.compile(
    r"docs\.google\.com/document/|drive\.google\.com/open|docs\.google\.com/spreadsheets/",
    re.IGNORECASE,
)
_DOC_ID_RE = re.compile(r"/document/d/([\w-]+)")
_OPEN_ID_RE = re.compile(r"[?&]id=([\w-]+)")
_VECKA_RE = re.compile(r"^vecka:?\s*(\S+)", re.IGNORECASE)
_PROVSCHEMA_WEEK_RE = re.compile(r"vecka\s*(\d+)", re.IGNORECASE)
_INFO_PREFIX_RE = re.compile(r"^aktuell information:?\s*", re.IGNORECASE)
_HEADER_KEYWORDS = ("ämne", "veckans planering", "övrigt")
# Fallback for an un-hyperlinked Classroom join code left as plain text after the subject name
# (e.g. "NO 6ehmkgbq") - lowercase alnum, 4-12 chars, containing at least one digit.
_TRAILING_CLASSROOM_CODE_RE = re.compile(r"\s+(?=[a-z0-9]*\d)[a-z0-9]{4,12}$")
# Separators in a Provschema subject list, e.g. "Prov spanska, franska och tyska".
_TEST_LIST_SEP_RE = re.compile(r"\s*,\s*|\s+och\s+", re.IGNORECASE)


@dataclass
class SchoolInfo:
    """Parsed school info for one person's class, for a target week."""

    person_name: str
    class_label: Optional[str]  # e.g. "7B"; also selects the Provschema column
    url: str
    week: Optional[int]  # week actually matched in Veckoplanering (None if not found)
    highlights: list[str]  # "**Ämne:** ..." / "**Ämne (läxa):** ..." lines (no tests - see below)
    tests: list[tuple[str, str]] = field(default_factory=list)  # (weekday, description) for target_week
    upcoming_tests: list[tuple[int, str, str]] = field(default_factory=list)  # (week, weekday, desc)
    info_note: Optional[str] = None  # "Aktuell information" free text
    status: str = "ok"  # "ok" | "week_not_published" | "fetch_error"
    latest_week_available: Optional[int] = None  # set when status == "week_not_published"
    warnings: list[str] = field(default_factory=list)  # soft issues; info still (partially) shown
    error: Optional[str] = None  # hard failure; nothing could be fetched


@dataclass
class _DiscoveredLinks:
    provschema_id: Optional[str]
    veckoplanering_id: Optional[str]
    unclassified_labels: list[str]


@dataclass
class _WeekBlock:
    week: Optional[int]
    raw_token: str
    info: str
    subjects: dict[str, dict[str, str]]  # subject -> {"plan": ..., "homework": ...}
    nonempty_cells: int


# ---------------------------------------------------------------------------
# Google Doc URL / HTML helpers
# ---------------------------------------------------------------------------


def _unwrap_google_redirect(href: str) -> str:
    """Google Docs wraps outbound links as https://www.google.com/url?q=<real>&... - unwrap that."""
    try:
        parsed = urlparse(href)
        if parsed.netloc.endswith("google.com") and parsed.path == "/url":
            qs = parse_qs(parsed.query)
            if qs.get("q"):
                return qs["q"][0]
    except Exception:
        pass
    return href


def _google_doc_id(href: str) -> Optional[str]:
    href = _unwrap_google_redirect(href)
    m = _DOC_ID_RE.search(href)
    if m:
        return m.group(1)
    m = _OPEN_ID_RE.search(href)
    if m:
        return m.group(1)
    return None


def _link_label(a_tag) -> str:
    """Visible label for a link: its own text, or the nearest non-empty ancestor's text.

    Google Sites often renders these as button components where the <a> itself has no text and
    the label lives on a parent <div> (verified on the real page).
    """
    text = a_tag.get_text(" ", strip=True)
    if text:
        return text
    node = a_tag
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        if text and len(text) < 150:
            return text
    return ""


def _classify_link_label(label: str) -> Optional[str]:
    low = label.lower()
    if "prov" in low:
        return "provschema"
    if "veckoplanering" in low or "planering" in low:
        return "veckoplanering"
    return None


def _discover_source_links(landing_url: str, timeout: float = 15.0) -> _DiscoveredLinks:
    """Fetch the class landing page and find the Provschema / Veckoplanering doc links."""
    resp = httpx.get(landing_url, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    provschema_id: Optional[str] = None
    veckoplanering_id: Optional[str] = None
    unclassified: list[str] = []
    seen_hrefs: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _LINK_HREF_RE.search(href) or href in seen_hrefs:
            continue
        seen_hrefs.add(href)
        doc_id = _google_doc_id(href)
        if not doc_id:
            continue
        label = _link_label(a)
        kind = _classify_link_label(label)
        if kind == "provschema" and provschema_id is None:
            provschema_id = doc_id
        elif kind == "veckoplanering" and veckoplanering_id is None:
            veckoplanering_id = doc_id
        elif kind is None:
            unclassified.append(label or href)
    return _DiscoveredLinks(provschema_id, veckoplanering_id, unclassified)


def _fetch_doc_html(doc_id: str, cache: dict[str, str], timeout: float = 20.0) -> str:
    """Fetch a Google Doc's HTML export, memoized by doc_id (docs are shared across siblings)."""
    if doc_id in cache:
        return cache[doc_id]
    url = f"https://docs.google.com/document/d/{doc_id}/export?format=html"
    resp = httpx.get(url, follow_redirects=True, timeout=timeout)
    resp.raise_for_status()
    cache[doc_id] = resp.text
    return resp.text


# ---------------------------------------------------------------------------
# Rich-text cell extraction
# ---------------------------------------------------------------------------


def _rich_text(node) -> str:
    """Render a node's text as plain text - never as a markdown/HTML link.

    Google Classroom links are dropped entirely (their visible text is usually a meaningless
    join code). Any other hyperlink (Docs, Drive, Sheets) keeps its visible label but not the
    URL: posting a raw Google URL in a Discord message auto-unfurls into a link-preview embed,
    and since these docs are typically not publicly accessible, that embed is a "Sign in -
    Google Accounts" card with a Sign In button - noise at best, confusing at worst. Never
    include the href in digest output.
    """
    if isinstance(node, NavigableString):
        return str(node)
    if getattr(node, "name", None) == "br":
        return " "  # a <br> inside one <p> separates lines that must not run together
    if getattr(node, "name", None) == "a" and node.get("href"):
        href = _unwrap_google_redirect(node["href"])
        inner = "".join(_rich_text(c) for c in node.children).strip()
        if "classroom.google.com" in href:
            return ""
        return inner
    return "".join(_rich_text(c) for c in getattr(node, "children", []))


def _cell_paragraph_texts(cell) -> list[str]:
    paragraphs = cell.find_all("p") or [cell]
    out = []
    for p in paragraphs:
        text = " ".join(_rich_text(p).split())
        if text:
            out.append(text)
    return out


def _subject_name_from_cell(cell) -> str:
    """
    First paragraph of the Ämne/Classroom-kod cell, with any Classroom join-code dropped.

    Usually the code is hyperlinked to classroom.google.com, which _rich_text already strips.
    But it isn't always - verified on a live page where the same site left one join code as
    plain text next to the subject name (e.g. "NO 6ehmkgbq"). Fall back to stripping a trailing
    lowercase-alnum token containing a digit (subject names here are capitalized Swedish words;
    join codes are lowercase and, in every code observed so far, contain at least one digit).
    """
    paras = _cell_paragraph_texts(cell)
    if not paras:
        return ""
    text = paras[0].strip().rstrip(":").strip()
    text = _TRAILING_CLASSROOM_CODE_RE.sub("", text).strip()
    return text


def _cell_text(cell) -> str:
    return " ".join(_cell_paragraph_texts(cell))


# ---------------------------------------------------------------------------
# Veckoplanering parsing
# ---------------------------------------------------------------------------


def _looks_like_subject_header(row) -> bool:
    cells = row.find_all(["td", "th"])
    text = " ".join(c.get_text(" ", strip=True) for c in cells).lower()
    return all(k in text for k in _HEADER_KEYWORDS)


def _parse_info_table(table) -> str:
    rows = table.find_all("tr")
    if not rows:
        return ""
    cells = rows[0].find_all(["td", "th"])
    if not cells:
        return ""
    return _INFO_PREFIX_RE.sub("", _cell_text(cells[0])).strip()


def _parse_subject_table(table) -> tuple[dict[str, dict[str, str]], int]:
    subjects: dict[str, dict[str, str]] = {}
    nonempty = 0
    for row in table.find_all("tr")[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) < 3:
            continue
        subject = _subject_name_from_cell(cells[0])
        if not subject:
            continue
        plan = _cell_text(cells[1])
        homework = _cell_text(cells[2])
        if plan:
            nonempty += 1
        if homework:
            nonempty += 1
        if not plan and not homework:
            continue  # nothing to show for this subject this week
        subjects[subject] = {"plan": plan, "homework": homework}
    return subjects, nonempty


def parse_veckoplanering(html: str) -> tuple[dict[int, _WeekBlock], list[str], Optional[_WeekBlock]]:
    """
    Parse a Veckoplanering doc export into week blocks.

    Returns (blocks_by_week, warnings, fallback_block). fallback_block is set only when exactly
    one block has real content but no valid week number (e.g. the MALL template was filled in but
    never renumbered) - it's informational only (logged to stderr, not put in `warnings`, since on
    the real document this is steady-state teacher scaffolding, not a per-run anomaly) and never
    used as a substitute for a missing target week: on the real page this template turned out to
    hold stale leftovers from a previous week, not pre-filled upcoming content, so treating it as
    "close enough" would misinform rather than help.
    """
    soup = BeautifulSoup(html, "html.parser")
    body = soup.body or soup

    blocks: dict[int, _WeekBlock] = {}
    unlabeled_filled: list[_WeekBlock] = []
    warnings: list[str] = []

    pending_token: Optional[str] = None
    pending_week: Optional[int] = None
    stage: Optional[str] = None  # "await_info" | "await_subjects" | None
    current_info = ""

    for child in body.children:
        name = getattr(child, "name", None)
        if name is None:
            continue
        if name == "p":
            m = _VECKA_RE.match(child.get_text(" ", strip=True))
            if m:
                pending_token = m.group(1)
                pending_week = int(pending_token) if pending_token.isdigit() else None
                current_info = ""
                stage = "await_info"
            continue
        if name != "table" or stage is None:
            continue
        rows = child.find_all("tr")
        if not rows:
            continue
        if stage == "await_info":
            if len(rows) == 1:
                current_info = _parse_info_table(child)
                stage = "await_subjects"
                continue
            stage = "await_subjects"  # no dedicated info table; treat this as the subject table
        if not _looks_like_subject_header(rows[0]):
            warnings.append(
                "Kunde inte tolka en veckotabell i veckoplaneringen "
                "(kolumnrubrikerna matchade inte förväntat format)."
            )
            stage = None
            continue
        subjects, nonempty = _parse_subject_table(child)
        block = _WeekBlock(
            week=pending_week,
            raw_token=pending_token or "",
            info=current_info,
            subjects=subjects,
            nonempty_cells=nonempty,
        )
        if pending_week is None:
            if nonempty > 0:
                unlabeled_filled.append(block)
        elif pending_week in blocks:
            existing = blocks[pending_week]
            if block.nonempty_cells > existing.nonempty_cells:
                warnings.append(
                    f"Flera block för vecka {pending_week} hittades i veckoplaneringen; "
                    "använder det med mest innehåll."
                )
                blocks[pending_week] = block
            else:
                warnings.append(
                    f"Flera block för vecka {pending_week} hittades i veckoplaneringen; "
                    "ignorerar ett block utan mer innehåll."
                )
        else:
            blocks[pending_week] = block
        stage = None

    # Note: an unlabeled-but-filled block (typically "MALL" used as reusable scaffolding) is
    # steady-state on the real document, not a per-run anomaly - the teacher keeps stale leftover
    # content in the template between rewrites. Log it for the maintainer rather than surfacing it
    # in every digest the family gets, where a permanent technical parenthetical would just teach
    # readers to ignore the warnings line (including the ones that are actually actionable).
    fallback_block: Optional[_WeekBlock] = None
    if len(unlabeled_filled) == 1:
        fallback_block = unlabeled_filled[0]
        print(
            f"[school] Veckoplanering: block märkt “{fallback_block.raw_token}” (inte ett "
            "veckonummer) innehåller planeringsinnehåll - kontrollera att sidan uppdaterats korrekt.",
            file=sys.stderr,
        )
    elif len(unlabeled_filled) > 1:
        print(
            f"[school] Veckoplanering: {len(unlabeled_filled)} block utan giltigt veckonummer "
            "innehåller innehåll och kunde inte tilldelas en vecka.",
            file=sys.stderr,
        )

    return blocks, warnings, fallback_block


# ---------------------------------------------------------------------------
# Provschema parsing
# ---------------------------------------------------------------------------


def parse_provschema(html: str) -> tuple[dict[int, dict[str, dict[str, str]]], set[str]]:
    """
    Parse a Provschema doc export.

    Returns (weeks, class_labels): weeks maps {week: {weekday: {class_label: text}}} (only weeks
    and cells with actual content); class_labels is every column header seen across the doc's
    tables - a *complete* list of valid classes, unlike inspecting `weeks`, which only contains
    classes that happen to have a test scheduled somewhere (validating a class_label against
    `weeks` alone would misreport it as "not found" in a term with no tests yet for that class).
    """
    soup = BeautifulSoup(html, "html.parser")
    result: dict[int, dict[str, dict[str, str]]] = {}
    class_labels: set[str] = set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header_cells = rows[0].find_all(["td", "th"])
        if not header_cells:
            continue
        m = _PROVSCHEMA_WEEK_RE.search(header_cells[0].get_text(" ", strip=True))
        if not m:
            continue
        week = int(m.group(1))
        row_class_labels = [c.get_text(" ", strip=True).strip().upper() for c in header_cells[1:]]
        class_labels.update(c for c in row_class_labels if c)
        by_weekday: dict[str, dict[str, str]] = {}
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            weekday = cells[0].get_text(" ", strip=True).strip()
            by_class = {
                label: text
                for label, cell in zip(row_class_labels, cells[1:])
                if (text := cell.get_text(" ", strip=True).strip())
            }
            if by_class:
                by_weekday[weekday] = by_class
        if by_weekday:
            result[week] = by_weekday
    return result, class_labels


def _collect_tests_for_week(
    provschema_weeks: dict[int, dict[str, dict[str, str]]],
    week: int,
    class_label_norm: str,
) -> list[tuple[str, str]]:
    by_weekday = provschema_weeks.get(week) or {}
    out = []
    for weekday in _WEEKDAYS_SV:
        text = (by_weekday.get(weekday) or {}).get(class_label_norm)
        if text:
            out.append((weekday, text))
    return out


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, re.IGNORECASE) is not None


def _filter_test_description(text: str, suppressed: set[str], known_subjects: set[str]) -> Optional[str]:
    """
    Remove suppressed subjects from a free-text Provschema entry. Returns None if the entry is
    only about suppressed subjects, otherwise the (possibly shortened) text.

    known_subjects (casefolded, from the class's Veckoplanering) tells which words in the free
    text are subjects at all: an entry is dropped when every subject it mentions is suppressed
    ("Tyska – textskrivning utan hjälpmedel"). Entries are often a shared test for several
    subjects ("Prov spanska, franska och tyska"), so a mixed entry is shortened to the remaining
    subjects ("Prov spanska") when it's a plain list, and otherwise kept as-is.
    """
    if not suppressed:
        return text
    hit = {s for s in suppressed if _mentions(text, s)}
    if not hit:
        return text
    if known_subjects and not any(_mentions(text, k) for k in known_subjects - suppressed):
        return None
    items = [i.strip() for i in _TEST_LIST_SEP_RE.split(text.strip())]
    # The first item usually carries a label before the subject ("Prov spanska") - peel it off.
    prefix = ""
    first_words = items[0].split()
    if len(first_words) > 1:
        prefix, items[0] = " ".join(first_words[:-1]), first_words[-1]
    kept = [i for i in items if i.casefold() not in suppressed]
    if not kept or len(kept) == len(items):
        return text  # not a plain subject list - keep rather than guess
    joined = kept[0] if len(kept) == 1 else ", ".join(kept[:-1]) + " och " + kept[-1]
    return f"{prefix} {joined}".strip()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def fetch_school_info_for_person(
    person_name: str,
    class_label: Optional[str],
    url: str,
    target_week: Optional[int] = None,
    _doc_cache: Optional[dict[str, str]] = None,
) -> SchoolInfo:
    """Fetch and parse one person's school info for target_week."""
    if _doc_cache is None:
        _doc_cache = {}

    try:
        links = _discover_source_links(url)
    except Exception as e:
        return SchoolInfo(
            person_name=person_name,
            class_label=class_label,
            url=url,
            week=None,
            highlights=[],
            status="fetch_error",
            error=f"Kunde inte hämta sidan – {e}",
        )

    warnings: list[str] = []
    if links.unclassified_labels:
        shown = ", ".join(links.unclassified_labels[:3])
        warnings.append(f"Kunde inte känna igen {len(links.unclassified_labels)} länk(ar) på sidan: {shown}")

    suppressed = config.get_suppressed_subjects(person_name)
    known_subjects: set[str] = set()  # casefolded subject names (+ first word, "Spanska Niklas")
    highlights: list[str] = []
    info_note: Optional[str] = None
    status = "ok"
    latest_week_available: Optional[int] = None
    week_matched: Optional[int] = None

    if links.veckoplanering_id is None:
        warnings.append("Hittade ingen länk till veckoplanering på sidan.")
    else:
        blocks: dict[int, _WeekBlock] = {}
        try:
            html = _fetch_doc_html(links.veckoplanering_id, _doc_cache)
            blocks, vp_warnings, _fallback_block = parse_veckoplanering(html)
            warnings.extend(vp_warnings)
            for b in blocks.values():
                for name in b.subjects:
                    known_subjects.add(name.casefold())
                    known_subjects.add(name.split()[0].casefold())
        except Exception as e:
            warnings.append(f"Kunde inte läsa veckoplaneringen – {e}")
        if target_week is not None:
            block = blocks.get(target_week)
            if block is not None:
                week_matched = target_week
                info_note = block.info or None
                for subject, cell in block.subjects.items():
                    if subject.strip().casefold() in suppressed:
                        continue
                    if cell.get("plan"):
                        highlights.append(f"**{subject}:** {cell['plan']}")
                    if cell.get("homework"):
                        highlights.append(f"**{subject} (läxa):** {cell['homework']}")
            else:
                # Don't fall back to the unlabeled/MALL block's content here: it's observed to
                # hold stale leftovers from a previous week (not pre-filled next-week content),
                # so showing it under target_week's heading would misinform, not help. The
                # doc-level warning about it (added in parse_veckoplanering) is enough of a nudge
                # to go check the page directly.
                status = "week_not_published"
                if blocks:
                    latest_week_available = max(blocks.keys())

    tests: list[tuple[str, str]] = []
    upcoming_tests: list[tuple[int, str, str]] = []
    if links.provschema_id is None:
        warnings.append("Hittade ingen länk till provschema på sidan.")
    elif not class_label:
        warnings.append("Ingen klass angiven för denna person – kan inte slå upp provschema.")
    else:
        provschema_weeks: dict[int, dict[str, dict[str, str]]] = {}
        known_classes: set[str] = set()
        try:
            html = _fetch_doc_html(links.provschema_id, _doc_cache)
            provschema_weeks, known_classes = parse_provschema(html)
        except Exception as e:
            warnings.append(f"Kunde inte läsa provschemat – {e}")
        class_norm = class_label.strip().upper()
        if known_classes and class_norm not in known_classes:
            warnings.append(
                f"Klass '{class_label}' hittades inte i provschemat; kolumner: " + ", ".join(sorted(known_classes))
            )
        elif target_week is not None:
            for weekday, desc in _collect_tests_for_week(provschema_weeks, target_week, class_norm):
                desc = _filter_test_description(desc, suppressed, known_subjects)
                if desc:
                    tests.append((weekday, desc))
            for w in range(target_week + 1, target_week + 1 + UPCOMING_TEST_WEEKS_AHEAD):
                for weekday, desc in _collect_tests_for_week(provschema_weeks, w, class_norm):
                    desc = _filter_test_description(desc, suppressed, known_subjects)
                    if desc:
                        upcoming_tests.append((w, weekday, desc))

    return SchoolInfo(
        person_name=person_name,
        class_label=class_label,
        url=url,
        week=week_matched,
        highlights=highlights,
        tests=tests,
        upcoming_tests=upcoming_tests,
        info_note=info_note,
        status=status,
        latest_week_available=latest_week_available,
        warnings=warnings,
    )


def fetch_all_school_info(target_week: Optional[int] = None) -> list[SchoolInfo]:
    """Fetch and parse all configured person school pages (from PERSON_SCHOOL)."""
    if not config.PERSON_SCHOOL:
        return [
            SchoolInfo(
                person_name="(configure PERSON_SCHOOL)",
                class_label=None,
                url="",
                week=None,
                highlights=[],
                status="fetch_error",
                error="PERSON_SCHOOL not set in .env (format: Name|ClassLabel|URL,...)",
            )
        ]
    doc_cache: dict[str, str] = {}  # shared across siblings so a shared Provschema is fetched once
    return [
        fetch_school_info_for_person(person_name, class_label, url, target_week=target_week, _doc_cache=doc_cache)
        for person_name, class_label, url in config.PERSON_SCHOOL
    ]
