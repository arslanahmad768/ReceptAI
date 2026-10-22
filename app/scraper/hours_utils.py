"""
Pure-Python parsing of Google Maps opening-hours text into an
"after-hours coverage gap" signal — used to sharpen the cold-email pitch
("you're closed evenings/weekends, missed calls go unanswered").

Input is the raw {day_name: hours_text} dict collected by maps_dom.py,
e.g. {"Monday": "8 AM to 5 PM", "Saturday": "Closed", ...}.
"""
import re

_WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
_WEEKEND = {"Saturday", "Sunday"}
_AFTER_HOURS_CUTOFF_MIN = 18 * 60  # 6 PM — a close before this leaves an evening gap


def _parse_time_to_minutes(text: str) -> int | None:
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*([AaPp][Mm])", text.strip())
    if not m:
        return None
    hour = int(m.group(1)) % 12
    minute = int(m.group(2) or 0)
    if m.group(3).lower() == "pm":
        hour += 12
    return hour * 60 + minute


def parse_day_hours(text: str) -> list[tuple[int, int]] | None:
    """
    Parse one day's hours text into [(open_min, close_min), ...].
    Returns [] for "Closed", [(0, 1440)] for "Open 24 hours",
    None if the text couldn't be parsed (treated as unknown, not closed).
    """
    if not text:
        return None
    t = text.replace(" ", " ").strip()
    tl = t.lower()
    if "closed" in tl:
        return []
    if "24 hours" in tl:
        return [(0, 24 * 60)]

    ranges: list[tuple[int, int]] = []
    for part in t.split(","):
        m = re.match(r"(.+?)\s+to\s+(.+)", part.strip(), re.I)
        if not m:
            continue
        start = _parse_time_to_minutes(m.group(1))
        end = _parse_time_to_minutes(m.group(2))
        if start is not None and end is not None:
            ranges.append((start, end))
    return ranges or None


def derive_after_hours_gap(hours: dict[str, str] | None) -> dict:
    """
    Summarize a week's hours into a decision-maker-relevant signal.
    Returns:
      open_weekends              bool | None (None = no weekend data)
      closed_days                list[str]
      closes_before_6pm_weekday  bool | None (None = no weekday data)
      after_hours_gap            bool | None — True if the clinic is closed
                                  weekends or wraps up before 6 PM on a
                                  weekday, i.e. there's a real window where
                                  calls go unanswered. None if not enough
                                  hours data was scraped to judge.
    """
    if not hours:
        return {
            "open_weekends": None,
            "closed_days": [],
            "closes_before_6pm_weekday": None,
            "after_hours_gap": None,
        }

    closed_days: list[str] = []
    weekday_closes: list[int] = []
    saw_weekend, open_weekend = False, False

    for day, text in hours.items():
        ranges = parse_day_hours(text)
        if ranges is None:
            continue
        if not ranges:
            closed_days.append(day)
            continue
        if day in _WEEKEND:
            saw_weekend = True
            open_weekend = True
        if day in _WEEKDAYS:
            weekday_closes.append(max(end for _, end in ranges))

    if not saw_weekend and any(d in _WEEKEND for d in closed_days):
        saw_weekend = True  # closed Sat/Sun still counts as weekend data

    closes_early = (
        any(c < _AFTER_HOURS_CUTOFF_MIN for c in weekday_closes) if weekday_closes else None
    )
    open_weekends = open_weekend if saw_weekend else None
    after_hours_gap = None
    if closes_early is not None or open_weekends is not None:
        after_hours_gap = bool(closes_early) or (open_weekends is False)

    return {
        "open_weekends": open_weekends,
        "closed_days": closed_days,
        "closes_before_6pm_weekday": closes_early,
        "after_hours_gap": after_hours_gap,
    }
