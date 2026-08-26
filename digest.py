"""Build the weekly digest message from school info and calendar events."""

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config
from school import SchoolInfo
from cal_fetcher import CalendarEvent

# Swedish weekday and month names for day-by-day calendar
_WEEKDAY_SV = ("Måndag", "Tisdag", "Onsdag", "Torsdag", "Fredag", "Lördag", "Söndag")
_MONTH_SV = (
    "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december",
)


def _week_dates(target_week: int, reference_date: date | None = None) -> list[date]:
    """Return [Monday, ..., Sunday] for the given ISO week (year from reference_date + 7 days)."""
    if reference_date is None:
        reference_date = date.today()
    next_week_date = reference_date + timedelta(days=7)
    iso_year, _, _ = next_week_date.isocalendar()
    return [date.fromisocalendar(iso_year, target_week, d) for d in range(1, 8)]


def _events_by_day_and_person(
    events_by_person: list[tuple[str, list[CalendarEvent]]],
) -> dict[date, dict[str, list[CalendarEvent]]]:
    """Group events by (day, person). Day is in CALENDAR_TIMEZONE."""
    try:
        tz = ZoneInfo(config.CALENDAR_TIMEZONE)
    except Exception:
        tz = None  # fallback: use event's own tz for date
    by_day: dict[date, dict[str, list[CalendarEvent]]] = defaultdict(lambda: defaultdict(list))
    for person_name, events in events_by_person:
        name = person_name or "Övrigt"
        for e in events:
            if tz is not None and e.start.tzinfo is not None:
                local_start = e.start.astimezone(tz)
            elif tz is not None:
                local_start = e.start.replace(tzinfo=tz)
            else:
                local_start = e.start
            day = local_start.date()
            by_day[day][name].append(e)
    for day in by_day:
        for name in by_day[day]:
            by_day[day][name].sort(key=lambda x: x.start)
    return dict(by_day)


def _dedupe_events_same_day(
    events: list[CalendarEvent],
    tz: ZoneInfo | None,
) -> list[CalendarEvent]:
    """Keep one event per (summary, location) per list; prefer timed over all-day."""
    if not events or len(events) <= 1:
        return events
    key_to_events: dict[tuple[str, str], list[CalendarEvent]] = defaultdict(list)
    for e in events:
        loc = (e.location or "").strip()
        key_to_events[(e.summary.strip(), loc)].append(e)
    result: list[CalendarEvent] = []
    for key, group in key_to_events.items():
        # Prefer timed event over all-day. all_day is set at ICS-parse time from whether the
        # source value was a DATE vs a DATETIME - not inferred from local hour==0, which breaks
        # for any timezone ahead of UTC (an all-day event stored as UTC midnight is never local
        # midnight there, e.g. 02:00 in Europe/Stockholm during summer).
        group_sorted = sorted(group, key=lambda ev: ev.all_day)
        result.append(group_sorted[0])
    result.sort(key=lambda x: x.start)
    return result


def _format_event_short(e: CalendarEvent, tz: ZoneInfo | None) -> str:
    """One event as 'HH:MM – Summary (location)' or 'Heldag – Summary'."""
    if e.all_day:
        time_str = "Heldag"
    else:
        local = e.start.astimezone(tz) if (tz is not None and e.start.tzinfo is not None) else e.start
        time_str = local.strftime("%H:%M")
    part = f"{time_str} – {e.summary}"
    if e.location:
        part += f" ({e.location})"
    return part


def _format_event(e: CalendarEvent) -> str:
    """Format a single calendar event for the digest (legacy list style)."""
    start_str = e.start.strftime("%a %d/%m %H:%M")
    line = f"• {start_str} – {e.summary}"
    if e.location:
        line += f" ({e.location})"
    return line


def _school_heading_from_info(info: SchoolInfo) -> str:
    """Display heading for one person's school section (Name or Name (ClassLabel))."""
    if info.class_label:
        return f"{info.person_name} ({info.class_label})"
    return info.person_name


def _format_test_short(description: str) -> str:
    """One Provschema test entry, e.g. '**PROV** – spanska, franska'."""
    return f"**PROV** – {description}"


_WEEKDAY_SV_INDEX = {name.lower(): i for i, name in enumerate(_WEEKDAY_SV)}


def _target_week_tests_by_date(
    school_infos: list[SchoolInfo] | None,
    week_dates: list[date],
) -> dict[date, list[tuple[str, str]]]:
    """Map date -> [(person_name, description)] for this-week tests (SchoolInfo.tests).

    Tests are dated by weekday name (from Provschema), not by datetime, so they're placed onto
    week_dates by weekday index rather than going through the calendar-event grouping machinery.
    """
    out: dict[date, list[tuple[str, str]]] = defaultdict(list)
    for info in school_infos or []:
        for weekday_name, description in getattr(info, "tests", None) or []:
            idx = _WEEKDAY_SV_INDEX.get(weekday_name.strip().lower())
            if idx is None or idx >= len(week_dates):
                continue
            out[week_dates[idx]].append((info.person_name, description))
    return dict(out)


def _render_school_person_block(info: SchoolInfo, target_week: int | None) -> list[str]:
    """
    Lines describing one person's Skola block: heading, current-info note, publish status,
    highlights (weekly plan/homework - no tests, those are dated and shown in the calendar
    section), special-info note, upcoming-tests lookahead, and any parse warnings.

    Shared between the template digest and the LLM payload so both see the same picture.
    """
    heading = _school_heading_from_info(info)
    if info.error:
        return [f"**{heading}:** Kunde inte hämta sidan – {info.error}"]

    lines: list[str] = [f"**{heading}:**"]
    if info.info_note:
        lines.append(f"*Aktuellt: {info.info_note}*")
    if info.status == "week_not_published":
        if info.latest_week_available is not None:
            lines.append(
                f"*Veckoplaneringen är inte uppdaterad för vecka {target_week} än "
                f"(senast uppdaterad: vecka {info.latest_week_available}).*"
            )
        else:
            lines.append("*Veckoplaneringen är inte uppdaterad ännu.*")
    if info.highlights:
        lines.extend(info.highlights)
    elif info.status == "ok":
        lines.append("Inga prov/läxor/förhör hittade denna vecka.")
    special = config.get_special_info(info.person_name)
    if special:
        lines.append(f"*({info.person_name} har {special} denna termin.)*")
    if info.upcoming_tests:
        lines.append("**Kommande prov:**")
        for week, weekday, desc in info.upcoming_tests:
            lines.append(f"- v{week} {weekday}: {desc}")
    for w in info.warnings:
        lines.append(f"*(Obs: {w})*")
    return lines


def _serialize_calendar_for_llm(
    events_by_person: list[tuple[str, list[CalendarEvent]]],
    target_week: int,
    calendar_error: str | None = None,
    reference_date: date | None = None,
    school_infos: list[SchoolInfo] | None = None,
) -> str:
    """Serialize the calendar section for the LLM (day-by-day, person/events), tests merged in."""
    lines: list[str] = []
    lines.append(f"KALENDER (vecka {target_week})")
    lines.append("---")
    if calendar_error:
        lines.append(f"Kalenderfel: {calendar_error}")
        lines.append("")
    week_dates = _week_dates(target_week, reference_date) if target_week is not None else []
    tests_by_date = _target_week_tests_by_date(school_infos, week_dates) if target_week is not None else {}
    if target_week is not None and (events_by_person or tests_by_date):
        try:
            tz = ZoneInfo(config.CALENDAR_TIMEZONE) if config.CALENDAR_TIMEZONE else None
        except Exception:
            tz = None
        by_day = _events_by_day_and_person(events_by_person)
        for d in week_dates:
            weekday_sv = _WEEKDAY_SV[d.weekday()]
            month_sv = _MONTH_SV[d.month - 1]
            lines.append(f"{weekday_sv} {d.day} {month_sv}:")
            persons_events = by_day.get(d, {})
            day_tests = tests_by_date.get(d, [])
            if not persons_events and not day_tests:
                lines.append("  Inga händelser.")
            else:
                all_persons = sorted(set(persons_events.keys()) | {p for p, _ in day_tests})
                for person_name in all_persons:
                    deduped = _dedupe_events_same_day(persons_events.get(person_name, []), tz)
                    event_strs = [_format_event_short(e, tz) for e in deduped]
                    existing_summaries = {
                        (e.summary or "").strip().lower() for e in persons_events.get(person_name, [])
                    }
                    for p_name, desc in day_tests:
                        if p_name != person_name or desc.strip().lower() in existing_summaries:
                            continue
                        event_strs.append(_format_test_short(desc))
                    lines.append(f"  {person_name}: " + ". ".join(event_strs))
            lines.append("")
    else:
        lines.append("Inga kalenderhändelser.")
    lines.append("---")
    return "\n".join(lines).strip()


def serialize_school_and_calendar_for_llm(
    school_infos: list[SchoolInfo],
    events_by_person: list[tuple[str, list[CalendarEvent]]],
    target_week: int,
    calendar_error: str | None = None,
    reference_date: date | None = None,
) -> str:
    """
    Serialize school and calendar data into a single text block for the LLM.
    The LLM will use this to produce the final weekly overview (title, intro, ## Skola, ## Kalender).
    """
    lines: list[str] = []
    lines.append(f"VECKA: {target_week}")
    lines.append("")
    lines.append("SKOLA")
    lines.append("---")
    for info in school_infos:
        lines.extend(_render_school_person_block(info, target_week))
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(_serialize_calendar_for_llm(
        events_by_person, target_week, calendar_error, reference_date, school_infos=school_infos
    ))
    return "\n".join(lines).strip()


def build_digest(
    school_infos: list[SchoolInfo],
    events_by_person: list[tuple[str, list[CalendarEvent]]],
    week_label: str | None = None,
    calendar_error: str | None = None,
    target_week: int | None = None,
    reference_date: date | None = None,
) -> str:
    """
    Build the full digest text (markdown-style for Discord).

    events_by_person: list of (person_name, events). person_name "" = global calendar.
    If week_label is None, it is derived from the first school info that has a week number.
    target_week: if set, included as focus hint for LLM (filter school to this week).
    """
    # When we're filtering for a target week, use it in the title so title and focus match
    if target_week is not None:
        week_label = f"Vecka {target_week}"
    elif week_label is None:
        for s in school_infos:
            if s.week is not None:
                week_label = f"Vecka {s.week}"
                break
        if week_label is None:
            week_label = "Kommande vecka"

    parts: list[str] = []

    # Header
    parts.append(f"# {week_label} – Veckosammanfattning")
    parts.append("")

    # If a calendar is named "Familjen", those events = family together; mention in summary
    familjen_events: list[CalendarEvent] = []
    for name, evs in events_by_person or []:
        if (name or "").strip().lower() == "familjen" and evs:
            familjen_events.extend(evs)
            break
    if familjen_events:
        summaries = list(dict.fromkeys(e.summary.strip() for e in familjen_events if (e.summary or "").strip()))
        if len(summaries) == 1:
            parts.append(f"**Tillsammans:** Denna vecka har familjen tillsammans: {summaries[0]}.")
        elif summaries:
            parts.append("**Tillsammans:** Denna vecka har familjen tillsammans: " + ", ".join(summaries[:5]) + (" …" if len(summaries) > 5 else "") + ".")
        else:
            parts.append("**Tillsammans:** Denna vecka har familjen aktiviteter tillsammans – se kalendern.")
        parts.append("")

    # School section
    parts.append("## Skola")
    any_school_error = False
    for info in school_infos:
        parts.extend(_render_school_person_block(info, target_week))
        parts.append("")
        if info.error:
            any_school_error = True
    if any_school_error:
        parts.append("*(Kontrollera att skolsidorna är tillgängliga.)*")
        parts.append("")

    # Calendar section: day-by-day when target_week is set, else flat per-person
    try:
        tz = ZoneInfo(config.CALENDAR_TIMEZONE) if config.CALENDAR_TIMEZONE else None
    except Exception:
        tz = None

    parts.append("## Kalender" + (f" (vecka {target_week})" if target_week is not None else ""))
    week_dates = _week_dates(target_week, reference_date) if target_week is not None else []
    tests_by_date = _target_week_tests_by_date(school_infos, week_dates) if target_week is not None else {}
    if calendar_error:
        parts.append(f"*Kunde inte hämta kalender: {calendar_error}*")
    elif target_week is not None and (events_by_person or tests_by_date):
        by_day = _events_by_day_and_person(events_by_person)
        for d in week_dates:
            weekday_sv = _WEEKDAY_SV[d.weekday()]
            month_sv = _MONTH_SV[d.month - 1]
            parts.append(f"### {weekday_sv} {d.day} {month_sv}")
            persons_events = by_day.get(d, {})
            day_tests = tests_by_date.get(d, [])
            if not persons_events and not day_tests:
                parts.append("Inga händelser.")
            else:
                all_persons = sorted(set(persons_events.keys()) | {p for p, _ in day_tests})
                for person_name in all_persons:
                    deduped = _dedupe_events_same_day(persons_events.get(person_name, []), tz)
                    event_strs = [_format_event_short(e, tz) for e in deduped]
                    existing_summaries = {
                        (e.summary or "").strip().lower() for e in persons_events.get(person_name, [])
                    }
                    for p_name, desc in day_tests:
                        if p_name != person_name or desc.strip().lower() in existing_summaries:
                            continue
                        event_strs.append(_format_test_short(desc))
                    parts.append(f"**{person_name}:** " + ". ".join(event_strs))
            parts.append("")
    elif events_by_person:
        for person_name, events in events_by_person:
            subheading = person_name if person_name else "Övrigt"
            if events:
                parts.append(f"**{subheading}:**")
                for e in events:
                    parts.append(_format_event(e))
                parts.append("")
            else:
                parts.append(f"**{subheading}:** Inga händelser.")
                parts.append("")
    else:
        parts.append("Inga händelser denna vecka.")
    parts.append("")

    return "\n".join(parts).strip()
