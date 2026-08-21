"""Best-effort parsing of an item's trade-hold end time.

Steam doesn't expose this as one clean structured field anywhere in
``Econ.GetInventoryItemsWithDescriptions`` - it has to be reconstructed from
several different, inconsistent signals on ``CEconItem_Description``:

1. Free-text notices in ``owner_descriptions`` (only present when it's your
   own inventory), phrased differently depending on the kind of hold:

   - Plain trade/market holds: ``"Tradable/Marketable After 27 Aug, 2026
     (14:00:00) GMT"`` - note the **day month, year** order, not month-day.
   - CS2's "Trade Protected" items (context 16 - see below): ``"This item is
     trade-protected and cannot be consumed, modified, or transferred until
     [date]1787569200[/date]"`` - a raw Unix timestamp wrapped in a
     ``[date]...[/date]`` BBCode-style tag, not a human-readable date at all.
     Unambiguous once spotted, so it's checked first.

2. ``item_expiration`` - occasionally carries the unlock time instead of (1)
   ever mentioning it in text at all.
3. ``sealed`` - CS2 Trade Protected items are ``sealed=True``; worth flagging
   even when neither (1) nor (2) yields a parseable date.

The human-readable-date patterns are a port of the (already battle-tested
against real accounts) parsing logic in a companion Node implementation -
same regexes, same year-inference fix for yearless dates (if it parses to
>180 days in the past, Steam meant next year's occurrence). The
``[date]<unix>[/date]`` tag is the wording actually observed from CS2's
current "Trade Protected" notices and takes priority over all of them.
"""

import re
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip

_UNIX_TIMESTAMP_TAG = re.compile(r"\[date\]\s*(\d+)\s*\[/date\]", re.IGNORECASE)

_HOLD_NOTICE_PATTERNS = [
    re.compile(r"tradable\s*/\s*marketable\s+after[:,\s]*(.+?gmt)", re.IGNORECASE),
    re.compile(r"tradable(?:\s*/\s*marketable)?\s+after[:,\s]*(.+?gmt)", re.IGNORECASE),
    re.compile(
        r"not\s+tradable(?:\s*/\s*marketable)?\s+(?:until|after)[:,\s]*(.+?gmt)", re.IGNORECASE
    ),
    re.compile(r"marketable\s+after[:,\s]*(.+?gmt)", re.IGNORECASE),
    re.compile(r"trade[\s-]*protected\b.*?\buntil\s+(.+?)(?:\.|$)", re.IGNORECASE),
]  # fmt: skip

_DATE_COMPONENTS = re.compile(
    r"(\d{1,2})\s+([A-Za-z]+),?\s*(\d{4})?\s*\(?\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*\)?\s*([ap]\.?m\.?)?",
    re.IGNORECASE,
)

_GENERIC_HOLD_KEYWORDS = re.compile(r"tradable|marketable|trade[\s-]*protected", re.IGNORECASE)


def _normalize_html_text(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _parse_date_components(raw_date: str, year_hint: int) -> datetime | None:
    text = re.sub(r"\s*gmt\s*$", "", raw_date.strip(), flags=re.IGNORECASE)
    text = text.replace("@", " ")

    match = _DATE_COMPONENTS.search(text)
    if not match:
        return None

    day = int(match.group(1))
    month = _MONTHS.get(match.group(2).lower()[:3])
    if not month:
        return None
    year = int(match.group(3)) if match.group(3) else year_hint
    hour = int(match.group(4))
    minute = int(match.group(5))
    second = int(match.group(6)) if match.group(6) else 0
    ampm = (match.group(7) or "").lower().replace(".", "")
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0

    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_hold_notice(text: str, *, now: datetime | None = None) -> datetime | None:
    """Parse one owner-description line for a trade-hold end date: first the
    unambiguous ``[date]<unix>[/date]`` tag, then each of
    :data:`_HOLD_NOTICE_PATTERNS` in turn.

    :param now: reference time for year-inference on yearless dates (e.g. the
        CS2 "21 Jul @ 1:00pm" style). Defaults to the current UTC time.
    :return: ``None`` if nothing matches or the matched text isn't a
        parseable date.
    """
    plain = _normalize_html_text(text)

    timestamp_match = _UNIX_TIMESTAMP_TAG.search(plain)
    if timestamp_match:
        try:
            return datetime.fromtimestamp(int(timestamp_match.group(1)), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass

    current = now if now is not None else datetime.now(timezone.utc)

    for pattern in _HOLD_NOTICE_PATTERNS:
        match = pattern.search(plain)
        if not match:
            continue
        parsed = _parse_date_components(match.group(1).strip(), current.year)
        if parsed is None:
            continue
        if parsed < current - timedelta(days=180):
            parsed = parsed.replace(year=parsed.year + 1)
        return parsed

    return None


def parse_item_expiration(value: str, *, now: datetime | None = None) -> datetime | None:
    """Parse ``CEconItem_Description.item_expiration`` - an ISO-8601-ish
    timestamp Steam sometimes sets instead of ever mentioning the hold in
    ``owner_descriptions`` text at all.

    :return: ``None`` if unset, unparseable, or already in the past (an
        expired ``item_expiration`` isn't a hold worth reporting).
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    current = now if now is not None else datetime.now(timezone.utc)
    return parsed if parsed > current else None


def find_trade_hold(
    description_lines: Iterable[Any], *, now: datetime | None = None
) -> tuple[str | None, datetime | None]:
    """Scan an item's ``owner_descriptions`` (or ``descriptions``) lines for a
    trade-hold notice.

    ``description_lines`` is any iterable of objects with a ``.value``
    attribute (``CEconItem_DescriptionLine`` or a duck-typed stand-in).

    :return: ``(raw_text, parsed_end_utc)``. ``(None, None)`` if no line
        mentions a hold; ``(raw_text, None)`` if one was found but its date
        couldn't be parsed - still worth surfacing to the caller.
    """
    for line in description_lines:
        value = getattr(line, "value", None) or ""
        if not value:
            continue
        parsed = parse_hold_notice(value, now=now)
        if parsed is not None:
            return _normalize_html_text(value), parsed
        if _GENERIC_HOLD_KEYWORDS.search(value):
            return _normalize_html_text(value), None
    return None, None


def resolve_trade_hold(
    owner_descriptions: Iterable[Any],
    descriptions: Iterable[Any] = (),
    *,
    item_expiration: str | None = None,
    sealed: bool = False,
    now: datetime | None = None,
) -> tuple[datetime | None, str | None]:
    """Combine every signal Steam might carry a trade hold in, in the same
    priority order as the reference implementation this was ported from:

    1. ``owner_descriptions`` text (owner-only - present when it's your own
       inventory).
    2. ``descriptions`` text, as a fallback for contexts where Steam put it
       in the public field instead.
    3. ``item_expiration``, if still in the future.
    4. ``sealed`` alone, if nothing above yielded a date - CS2 Trade
       Protected items (context 16) are reliably ``sealed`` even when their
       date text doesn't parse.

    :return: ``(tradable_after, trade_hold_note)`` - see
        :class:`~steam_api.schemas.InventoryItem`.
    """
    raw_text, parsed = find_trade_hold(owner_descriptions, now=now)
    if parsed is None and raw_text is None:
        raw_text, parsed = find_trade_hold(descriptions, now=now)

    if parsed is None:
        parsed = parse_item_expiration(item_expiration or "", now=now)

    if parsed is None and raw_text is None and sealed:
        raw_text = "TRADE HOLD"

    return parsed, raw_text
