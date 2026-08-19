"""Grouping travel emails into trips (CLAUDE.md §10).

Flights, hotels, car hire and itinerary changes for one journey arrive as
separate emails. This lays a *trip* view over them: a destination, a date
range, the threads involved. The individual emails stay exactly as they are.

Grouping is best-effort and conservative. Two travel emails join the same trip
only when there's real evidence they belong together — the same thread, the
same booking reference, or the same destination within a few days. When in
doubt they stay apart, because a wrongly-merged trip is more misleading than
two separate ones.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date

from app.classification import patterns
from app.classification.message import EmailMessage
from app.intelligence import dates as date_extract
from app.intelligence.models import TripContext

#: Two date ranges this many days apart (or less) count as the same trip.
_TRIP_GAP_DAYS = 3

_DEST_RE = re.compile(
    r"\b(?:to|in|for|into|toward|trip to|flight to|travel(?:ing|ling)?\s+to|"
    r"your trip to|destination:?)\s+([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2})"
)
_DEST_STOPWORDS = {
    # Verbs / filler that follow "to"/"for"/"in" but name no place.
    "confirm", "view", "you", "your", "us", "complete", "continue", "the",
    "manage", "check", "see", "book", "our", "my", "get", "download",
    "reservation", "booking", "confirmation", "hotel", "flight", "reserve",
    # Months and weekdays, so "in September" / "for Monday" aren't destinations.
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
}

_REF_RE = re.compile(
    r"(?:confirmation|booking\s+reference|booking\s+code|reference|pnr|"
    r"itinerary|record\s+locator)\s*(?:number|no\.?|code|#)?\s*[:#]?\s*"
    r"([A-Z0-9]{5,8})\b",
    re.IGNORECASE,
)


@dataclass
class _Segment:
    message: EmailMessage
    destination: str = ""
    ref: str = ""
    dates: list[date] = field(default_factory=list)

    @property
    def span(self) -> tuple[date, date] | None:
        return (min(self.dates), max(self.dates)) if self.dates else None


def _destination(message: EmailMessage) -> str:
    m = _DEST_RE.search(f"{message.subject} {message.snippet}")
    if not m:
        return ""
    candidate = m.group(1).strip()
    if candidate.split()[0].lower() in _DEST_STOPWORDS:
        return ""
    return candidate


def _ref(message: EmailMessage) -> str:
    m = _REF_RE.search(message.searchable_text)
    return m.group(1).upper() if m else ""


def _segment(message: EmailMessage, today: date) -> _Segment:
    reference = message.date.date() if message.date else today
    text = f"{message.subject}\n{message.snippet}\n{message.body_text}"
    found = date_extract.extract_dates(text, reference)
    return _Segment(
        message=message,
        destination=_destination(message),
        ref=_ref(message),
        dates=[d.value for d in found if d.confidence >= 0.7],
    )


def _ranges_close(a: _Segment, b: _Segment) -> bool:
    sa, sb = a.span, b.span
    if sa is None or sb is None:
        return False
    gap = max((sb[0] - sa[1]).days, (sa[0] - sb[1]).days, 0)
    return gap <= _TRIP_GAP_DAYS


def _same_trip(a: _Segment, b: _Segment) -> bool:
    if a.message.thread_id and a.message.thread_id == b.message.thread_id:
        return True
    if a.ref and a.ref == b.ref:
        return True
    if (
        a.destination
        and b.destination
        and a.destination.lower() == b.destination.lower()
        and _ranges_close(a, b)
    ):
        return True
    return False


class _UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)


def _is_travel(message: EmailMessage) -> bool:
    return patterns.TRAVEL.matches(message.subject_and_snippet) or patterns.TRAVEL.matches(
        message.searchable_text
    )


def group_trips(messages: list[EmailMessage], today: date) -> list[TripContext]:
    """Return one :class:`TripContext` per distinct journey found."""
    segments = [_segment(m, today) for m in messages if _is_travel(m)]
    if not segments:
        return []

    uf = _UnionFind(len(segments))
    for i in range(len(segments)):
        for j in range(i + 1, len(segments)):
            if _same_trip(segments[i], segments[j]):
                uf.union(i, j)

    components: dict[int, list[_Segment]] = {}
    for idx, seg in enumerate(segments):
        components.setdefault(uf.find(idx), []).append(seg)

    trips: list[TripContext] = []
    for members in components.values():
        trips.append(_build_trip(members, today))
    return trips


def _build_trip(members: list[_Segment], today: date) -> TripContext:
    all_dates = sorted(d for seg in members for d in seg.dates)
    start = all_dates[0] if all_dates else None
    end = all_dates[-1] if all_dates else None

    destination = next((s.destination for s in members if s.destination), "")

    if end is not None and end < today:
        status = "past"
    elif start is not None and start >= today:
        status = "upcoming"
    else:
        status = "unknown"

    key_source = (
        f"{destination.lower()}|{start.isoformat()}"
        if destination and start
        else "|".join(sorted(s.message.message_id for s in members))
    )
    trip_id = "trip-" + hashlib.sha1(key_source.encode("utf-8")).hexdigest()[:10]

    return TripContext(
        trip_id=trip_id,
        destination=destination,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        related_threads=tuple(
            dict.fromkeys(s.message.thread_id for s in members if s.message.thread_id)
        ),
        related_messages=tuple(s.message.message_id for s in members),
        status=status,
        segment_count=len(members),
    )


__all__ = ("group_trips",)
