"""Fetch ICS calendar URLs and return events for the coming week or for a target ISO week."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Union
from zoneinfo import ZoneInfo

import httpx
import icalendar

import config


@dataclass
class CalendarEvent:
    """A single calendar event."""

    summary: str
    start: datetime
    end: Optional[datetime]
    location: Optional[str] = None


def _to_datetime(val) -> Optional[datetime]:
    """Convert icalendar date or datetime to timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val
    if isinstance(val, date) and not isinstance(val, datetime):
        return datetime.combine(val, datetime.min.time(), tzinfo=timezone.utc)
    return None


def _parse_dt(component, key: str) -> Optional[datetime]:
    """Get a datetime from an icalendar component (DTSTART/DTEND)."""
    val = component.get(key)
    if val is None:
        return None
    if isinstance(val, icalendar.vDDDTypes):
        return _to_datetime(val.dt)
    return None


def _get_events_from_ics(ics_text: str) -> list[CalendarEvent]:
    """Parse ICS string and return list of CalendarEvent (no recurrence expansion)."""
    cal = icalendar.Calendar.from_ical(ics_text)
    events: list[CalendarEvent] = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        summary = str(component.get("SUMMARY", ""))
        start = _parse_dt(component, "DTSTART")
        if start is None:
            continue
        end = _parse_dt(component, "DTEND")
        location = component.get("LOCATION")
        location = str(location) if location else None
        events.append(
            CalendarEvent(summary=summary, start=start, end=end, location=location)
        )
    return events


def _get_events_from_ics_between(
    ics_text: str,
    from_date: datetime,
    end_date: datetime,
) -> list[CalendarEvent]:
    """
    Parse ICS and return events in the given range, with recurring events expanded to instances.
    Falls back to _get_events_from_ics + date filter if recurring_ical_events is not available.
    """
    try:
        import recurring_ical_events
    except ImportError:
        raw = _get_events_from_ics(ics_text)
        return [e for e in raw if from_date <= e.start <= end_date]

    cal = icalendar.Calendar.from_ical(ics_text)
    events: list[CalendarEvent] = []
    try:
        for component in recurring_ical_events.of(cal, skip_bad_series=True).between(
            from_date, end_date
        ):
            start = _parse_dt(component, "DTSTART")
            if start is None:
                continue
            summary = str(component.get("SUMMARY", ""))
            end = _parse_dt(component, "DTEND")
            location = component.get("LOCATION")
            location = str(location) if location else None
            events.append(
                CalendarEvent(summary=summary, start=start, end=end, location=location)
            )
    except Exception:
        raw = _get_events_from_ics(ics_text)
        return [e for e in raw if from_date <= e.start <= end_date]
    return events


def _normalize_calendar_url(url: str) -> str:
    """Allow webcal: links (same as https: for fetching)."""
    u = (url or "").strip()
    if u.lower().startswith("webcal://"):
        return "https://" + u[9:]
    return u


def _fetch_events_from_urls(
    urls: list[str],
    from_date: datetime,
    end_date: datetime,
) -> list[CalendarEvent]:
    """Fetch and filter events from a list of ICS URLs."""
    events: list[CalendarEvent] = []
    for url in urls:
        url = _normalize_calendar_url(url)
        if not url:
            continue
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=15.0)
            resp.raise_for_status()
            for e in _get_events_from_ics_between(resp.text, from_date, end_date):
                events.append(e)
        except Exception:
            continue
    events.sort(key=lambda e: e.start)
    return events


def _week_range_in_tz(
    target_week: int,
    reference_date: date | None = None,
) -> tuple[datetime, datetime]:
    """Return (Monday 00:00, Sunday 23:59.999) for target_week in CALENDAR_TIMEZONE."""
    if reference_date is None:
        reference_date = date.today()
    next_week_date = reference_date + timedelta(days=7)
    iso_year, _, _ = next_week_date.isocalendar()
    try:
        tz = ZoneInfo(config.CALENDAR_TIMEZONE)
    except Exception:
        tz = timezone.utc
    monday = date.fromisocalendar(iso_year, target_week, 1)
    sunday = date.fromisocalendar(iso_year, target_week, 7)
    start = datetime.combine(monday, datetime.min.time(), tzinfo=tz)
    end = datetime.combine(sunday, datetime.max.time().replace(microsecond=999999), tzinfo=tz)
    return start, end


def _event_date_in_tz(e: CalendarEvent, tz: Union[ZoneInfo, timezone]) -> date:
    """Event start date in the given timezone (for week filtering)."""
    if e.start.tzinfo is not None:
        return e.start.astimezone(tz).date()
    return e.start.replace(tzinfo=tz).date()


def _calendar_person_names() -> set[str]:
    """All person names that appear in PERSON_CALENDARS (for name-in-summary filtering)."""
    names: set[str] = set()
    for name_list, _ in config.PERSON_CALENDARS:
        names.update(name_list)
    return names


def _event_belongs_to_person(event: CalendarEvent, person_name: str, all_names: set[str]) -> bool:
    """
    True if this event should be shown for this person.
    If the event summary contains a person's name, show only for that person; otherwise show for everyone with that calendar.
    """
    summary = (event.summary or "").strip()
    if not all_names:
        return True
    names_in_summary = [n for n in all_names if n and n in summary]
    if not names_in_summary:
        return True  # no name in summary -> show for all
    return person_name in names_in_summary


def fetch_events_for_week(
    target_week: int,
    reference_date: date | None = None,
) -> list[tuple[str, list[CalendarEvent]]]:
    """
    Fetch events for Monday–Sunday of the given ISO week (in CALENDAR_TIMEZONE), grouped by person.

    Returns same shape as fetch_events_next_week: list of (person_name, events).
    reference_date is used to resolve the ISO year (default: today); the week is the one containing reference_date + 7 days.
    Events are filtered by: (1) start date in calendar tz falls in the week, (2) if summary contains a person name, only that person sees it.
    """
    start, end = _week_range_in_tz(target_week, reference_date)
    try:
        tz = ZoneInfo(config.CALENDAR_TIMEZONE)
    except Exception:
        tz = timezone.utc
    week_dates = {start.date() + timedelta(days=i) for i in range(7)}
    all_names = _calendar_person_names()
    result: list[tuple[str, list[CalendarEvent]]] = []

    if config.PERSON_CALENDARS:
        normalized_to_original: dict[str, str] = {}
        for _, url in config.PERSON_CALENDARS:
            n = _normalize_calendar_url(url)
            if n and n not in normalized_to_original:
                normalized_to_original[n] = url
        url_to_events: dict[str, list[CalendarEvent]] = {}
        for norm_url in normalized_to_original:
            raw = _fetch_events_from_urls([norm_url], start, end)
            url_to_events[norm_url] = [
                e for e in raw
                if _event_date_in_tz(e, tz) in week_dates
            ]
        by_person_events: dict[str, list[CalendarEvent]] = defaultdict(list)
        for names, url in config.PERSON_CALENDARS:
            key = _normalize_calendar_url(url) or url
            events = url_to_events.get(key, [])
            for name in names:
                for e in events:
                    if _event_belongs_to_person(e, name, all_names):
                        by_person_events[name].append(e)
        for name in sorted(by_person_events.keys()):
            events = by_person_events[name]
            events.sort(key=lambda e: e.start)
            result.append((name, events))
    elif config.ICS_URLS:
        raw = _fetch_events_from_urls(config.ICS_URLS, start, end)
        events = [e for e in raw if _event_date_in_tz(e, tz) in week_dates]
        result.append(("", events))
    return result


def fetch_events_next_week(
    from_date: datetime | None = None,
) -> list[tuple[str, list[CalendarEvent]]]:
    """
    Fetch events for the next 7 days, grouped by person.

    Returns list of (person_name, events). person_name is "" for global ICS_URLS.
    If PERSON_CALENDARS is set, each entry is (name, that person's events).
    Otherwise uses ICS_URLS as a single global calendar with person_name "".
    """
    if from_date is None:
        from_date = datetime.now(timezone.utc)
    end_date = from_date + timedelta(days=7)
    result: list[tuple[str, list[CalendarEvent]]] = []

    if config.PERSON_CALENDARS:
        # Fetch each URL once (shared calendars may appear in multiple entries).
        # Normalize webcal:// to https:// so the same calendar isn't fetched twice.
        normalized_to_original: dict[str, str] = {}
        for _, url in config.PERSON_CALENDARS:
            n = _normalize_calendar_url(url)
            if n and n not in normalized_to_original:
                normalized_to_original[n] = url
        url_to_events: dict[str, list[CalendarEvent]] = {}
        for norm_url in normalized_to_original:
            url_to_events[norm_url] = _fetch_events_from_urls([norm_url], from_date, end_date)
        by_person_events: dict[str, list[CalendarEvent]] = defaultdict(list)
        for names, url in config.PERSON_CALENDARS:
            key = _normalize_calendar_url(url) or url
            events = url_to_events.get(key, [])
            for name in names:
                by_person_events[name].extend(events)
        for name in sorted(by_person_events.keys()):
            events = by_person_events[name]
            events.sort(key=lambda e: e.start)
            result.append((name, events))
    elif config.ICS_URLS:
        events = _fetch_events_from_urls(config.ICS_URLS, from_date, end_date)
        result.append(("", events))
    return result
