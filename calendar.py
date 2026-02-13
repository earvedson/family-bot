"""Fetch ICS calendar URLs and return events for the coming week."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

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
    """Parse ICS string and return list of CalendarEvent."""
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


def _fetch_events_from_urls(
    urls: list[str],
    from_date: datetime,
    end_date: datetime,
) -> list[CalendarEvent]:
    """Fetch and filter events from a list of ICS URLs."""
    events: list[CalendarEvent] = []
    for url in urls:
        if not url:
            continue
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=15.0)
            resp.raise_for_status()
            for e in _get_events_from_ics(resp.text):
                if from_date <= e.start <= end_date:
                    events.append(e)
        except Exception:
            continue
    events.sort(key=lambda e: e.start)
    return events


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
        # Fetch each URL once (shared calendars may appear in multiple entries)
        unique_urls = list({url for _, url in config.PERSON_CALENDARS})
        url_to_events: dict[str, list[CalendarEvent]] = {
            url: _fetch_events_from_urls([url], from_date, end_date)
            for url in unique_urls
        }
        by_person_events: dict[str, list[CalendarEvent]] = defaultdict(list)
        for names, url in config.PERSON_CALENDARS:
            events = url_to_events.get(url, [])
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
