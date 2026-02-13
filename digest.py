"""Build the weekly digest message from school info and calendar events."""

from school import SchoolInfo
from calendar import CalendarEvent


def _format_event(e: CalendarEvent) -> str:
    """Format a single calendar event for the digest."""
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


def build_digest(
    school_infos: list[SchoolInfo],
    events_by_person: list[tuple[str, list[CalendarEvent]]],
    week_label: str | None = None,
    calendar_error: str | None = None,
) -> str:
    """
    Build the full digest text (markdown-style for Discord).

    events_by_person: list of (person_name, events). person_name "" = global calendar.
    If week_label is None, it is derived from the first school info that has a week number.
    If calendar_error is set, the calendar section shows that instead of events.
    """
    if week_label is None:
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

    # Calendar section (per person or single global)
    parts.append("## Kalender")
    if calendar_error:
        parts.append(f"*Kunde inte hämta kalender: {calendar_error}*")
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
        parts.append("Inga händelser de närmaste 7 dagarna.")
    parts.append("")

    return "\n".join(parts).strip()
