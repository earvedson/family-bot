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


def _format_event_short(e: CalendarEvent, tz: ZoneInfo | None) -> str:
    """One event as 'HH:MM – Summary (location)' or 'Heldag – Summary'."""
    if tz is not None and e.start.tzinfo is not None:
        local = e.start.astimezone(tz)
    else:
        local = e.start
    if local.hour == 0 and local.minute == 0:
        time_str = "Heldag"
    else:
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


def _school_heading(info: SchoolInfo) -> str:
    """Display heading for one person's school section (Name or Name (ClassLabel))."""
    if info.class_label:
        return f"{info.person_name} ({info.class_label})"
    return info.person_name


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
        heading = _school_heading(info)
        if info.error:
            lines.append(f"{heading}: Fel – {info.error}")
        elif info.highlights:
            lines.append(f"{heading}:")
            for h in info.highlights:
                lines.append(f"- {h}")
        else:
            lines.append(f"{heading}: Inga prov/läxor/förhör denna vecka.")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"KALENDER (vecka {target_week})")
    lines.append("---")
    if calendar_error:
        lines.append(f"Kalenderfel: {calendar_error}")
        lines.append("")
    if events_by_person and target_week is not None:
        try:
            tz = ZoneInfo(config.CALENDAR_TIMEZONE) if config.CALENDAR_TIMEZONE else None
        except Exception:
            tz = None
        week_dates = _week_dates(target_week, reference_date)
        by_day = _events_by_day_and_person(events_by_person)
        for d in week_dates:
            weekday_sv = _WEEKDAY_SV[d.weekday()]
            month_sv = _MONTH_SV[d.month - 1]
            lines.append(f"{weekday_sv} {d.day} {month_sv}:")
            persons_events = by_day.get(d, {})
            if not persons_events:
                lines.append("  Inga händelser.")
            else:
                for person_name in sorted(persons_events.keys()):
                    event_strs = [_format_event_short(e, tz) for e in persons_events[person_name]]
                    lines.append(f"  {person_name}: " + ". ".join(event_strs))
            lines.append("")
    else:
        lines.append("Inga kalenderhändelser.")
    lines.append("---")
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
        heading = _school_heading(info)
        if info.error:
            parts.append(f"**{heading}:** Kunde inte hämta sidan – {info.error}")
            any_school_error = True
        elif info.highlights:
            parts.append(f"**{heading}:**")
            for h in info.highlights:
                parts.append(h)
            parts.append("")
        else:
            parts.append(f"**{heading}:** Inga prov/läxor/förhör hittade denna vecka.")
            parts.append("")
    if any_school_error:
        parts.append("*(Kontrollera att skolsidorna är tillgängliga.)*")
        parts.append("")

    # Calendar section: day-by-day when target_week is set, else flat per-person
    try:
        tz = ZoneInfo(config.CALENDAR_TIMEZONE) if config.CALENDAR_TIMEZONE else None
    except Exception:
        tz = None

    parts.append("## Kalender" + (f" (vecka {target_week})" if target_week is not None else ""))
    if calendar_error:
        parts.append(f"*Kunde inte hämta kalender: {calendar_error}*")
    elif events_by_person and target_week is not None:
        week_dates = _week_dates(target_week, reference_date)
        by_day = _events_by_day_and_person(events_by_person)
        for d in week_dates:
            weekday_sv = _WEEKDAY_SV[d.weekday()]
            month_sv = _MONTH_SV[d.month - 1]
            parts.append(f"### {weekday_sv} {d.day} {month_sv}")
            persons_events = by_day.get(d, {})
            if not persons_events:
                parts.append("Inga händelser.")
            else:
                for person_name in sorted(persons_events.keys()):
                    event_strs = [_format_event_short(e, tz) for e in persons_events[person_name]]
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
