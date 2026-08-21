from datetime import datetime, timezone

from steam_api.trade_hold import (
    find_trade_hold,
    parse_hold_notice,
    parse_item_expiration,
    resolve_trade_hold,
)


def test_parse_hold_notice_reads_day_month_year_time_gmt() -> None:
    parsed = parse_hold_notice("Tradable/Marketable After 27 Aug, 2026 (14:00:00) GMT")

    assert parsed == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_handles_tradable_after_alone() -> None:
    parsed = parse_hold_notice("Tradable After 27 Aug, 2026 (14:00:00) GMT")

    assert parsed == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_is_case_insensitive() -> None:
    parsed = parse_hold_notice("tradable/marketable after 27 aug, 2026 (14:00:00) gmt")

    assert parsed == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_handles_not_tradable_until_phrasing() -> None:
    parsed = parse_hold_notice("Not Tradable/Marketable until 27 Aug, 2026 (14:00:00) GMT")

    assert parsed == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_handles_cs2_trade_protected_phrasing_without_year() -> None:
    now = datetime(2026, 7, 1, tzinfo=timezone.utc)

    parsed = parse_hold_notice(
        "This item is trade-protected and cannot be traded until 21 Jul @ 1:00pm.", now=now
    )

    assert parsed == datetime(2026, 7, 21, 13, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_rolls_yearless_date_forward_across_year_boundary() -> None:
    now = datetime(2026, 12, 20, tzinfo=timezone.utc)

    parsed = parse_hold_notice(
        "This item is trade-protected and cannot be traded until 5 Jan @ 1:00pm.", now=now
    )

    assert parsed == datetime(2027, 1, 5, 13, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_returns_none_for_unrelated_text() -> None:
    assert parse_hold_notice("Exterior: Field-Tested") is None


def test_parse_hold_notice_returns_none_when_date_portion_is_unparsable() -> None:
    assert parse_hold_notice("Tradable After sometime next week GMT") is None


def test_parse_hold_notice_reads_the_unix_timestamp_tag() -> None:
    parsed = parse_hold_notice(
        "⇆ This item is trade-protected and cannot be consumed,"
        "modified, or transferred until [date]1787569200[/date]"
    )

    assert parsed == datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_reads_the_unix_timestamp_tag_with_a_typo_gap() -> None:
    parsed = parse_hold_notice(
        "⇆ This item is trade-protected and cannot be consumed, modified, "
        "ortransferred until [date]1787295600[/date]"
    )

    assert parsed == datetime(2026, 8, 21, 7, 0, 0, tzinfo=timezone.utc)


def test_parse_hold_notice_prefers_the_unix_timestamp_tag_over_text_patterns() -> None:
    parsed = parse_hold_notice(
        "Tradable After [date]1787569200[/date] some unrelated trailing text GMT"
    )

    assert parsed == datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc)


def test_parse_item_expiration_parses_a_future_iso_timestamp() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    parsed = parse_item_expiration("2026-08-27T14:00:00Z", now=now)

    assert parsed == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_item_expiration_ignores_a_past_timestamp() -> None:
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)

    assert parse_item_expiration("2026-08-27T14:00:00Z", now=now) is None


def test_parse_item_expiration_returns_none_for_empty_or_garbage() -> None:
    assert parse_item_expiration("") is None
    assert parse_item_expiration("not a date") is None


def test_find_trade_hold_returns_none_none_when_no_line_matches() -> None:
    lines = [_line("Exterior: Field-Tested"), _line("Souvenir")]

    raw, parsed = find_trade_hold(lines)

    assert raw is None
    assert parsed is None


def test_find_trade_hold_finds_the_matching_line_and_parses_it() -> None:
    lines = [
        _line("Exterior: Field-Tested"),
        _line("Tradable/Marketable After 27 Aug, 2026 (14:00:00) GMT"),
    ]

    raw, parsed = find_trade_hold(lines)

    assert raw == "Tradable/Marketable After 27 Aug, 2026 (14:00:00) GMT"
    assert parsed == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_resolve_trade_hold_prefers_owner_descriptions() -> None:
    tradable_after, note = resolve_trade_hold(
        owner_descriptions=[_line("Tradable/Marketable After 27 Aug, 2026 (14:00:00) GMT")],
        descriptions=[],
        item_expiration="",
        sealed=False,
    )

    assert tradable_after == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)
    assert note == "Tradable/Marketable After 27 Aug, 2026 (14:00:00) GMT"


def test_resolve_trade_hold_falls_back_to_descriptions() -> None:
    tradable_after, _note = resolve_trade_hold(
        owner_descriptions=[],
        descriptions=[_line("Tradable/Marketable After 27 Aug, 2026 (14:00:00) GMT")],
        item_expiration="",
        sealed=False,
    )

    assert tradable_after == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_resolve_trade_hold_falls_back_to_item_expiration() -> None:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)

    tradable_after, _note = resolve_trade_hold(
        owner_descriptions=[],
        descriptions=[],
        item_expiration="2026-08-27T14:00:00Z",
        sealed=False,
        now=now,
    )

    assert tradable_after == datetime(2026, 8, 27, 14, 0, 0, tzinfo=timezone.utc)


def test_resolve_trade_hold_falls_back_to_sealed_flag_with_no_date() -> None:
    tradable_after, note = resolve_trade_hold(
        owner_descriptions=[],
        descriptions=[],
        item_expiration="",
        sealed=True,
    )

    assert tradable_after is None
    assert note == "TRADE HOLD"


def test_resolve_trade_hold_returns_none_none_with_no_signal_at_all() -> None:
    tradable_after, note = resolve_trade_hold(
        owner_descriptions=[],
        descriptions=[],
        item_expiration="",
        sealed=False,
    )

    assert tradable_after is None
    assert note is None


def _line(value: str):  # noqa: ANN202
    from types import SimpleNamespace

    return SimpleNamespace(value=value)
