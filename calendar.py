"""Fetch ICS calendar URLs and return events for the coming week."""

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


def fetch_events(
    days_ahead: int = 7,
    from_date: datetime | None = None,
) -> list[CalendarEvent]:
    """
    Fetch all configured ICS URLs and return events within the next days_ahead days.

    If from_date is given, use it as the start of the window; otherwise use now (UTC).
    """
    if from_date is None:
        from_date = datetime.now(timezone.utc)
    end_date = from_date + timedelta(days=days_ahead)
    all_events: list[CalendarEvent] = []

    for url in config.ICS_URLS:
        if not url:
            continue
        try:
            resp = httpx.get(url, follow_redirects=True, timeout=15.0)
            resp.raise_for_status()
            events = _get_events_from_ics(resp.text)
            for e in events:
                if from_date <= e.start <= end_date:
                    all_events.append(e)
        except Exception:
            # Skip failed calendars; digest can still show the rest
            continue

    all_events.sort(key=lambda e: e.start)
    return all_events


def fetch_events_next_week(from_date: datetime | None = None) -> list[CalendarEvent]:
    """
    Fetch events for the next 7 days (e.g. for a weekly digest sent on Sunday).
    """
    return fetch_events(days_ahead=7, from_date=from_date)
