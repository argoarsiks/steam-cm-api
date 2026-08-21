import json
from datetime import datetime, timedelta, timezone

from steam_api.schemas import (
    GenerateAccessTokenForAppResponse,
    Inventory,
    InventoryItem,
    SteamWebCookies,
)


def test_generate_access_token_response_defaults_to_none() -> None:
    response = GenerateAccessTokenForAppResponse.model_validate({})

    assert response.access_token is None
    assert response.refresh_token is None


def test_generate_access_token_response_reads_the_access_token() -> None:
    response = GenerateAccessTokenForAppResponse.model_validate({"access_token": "abc123"})

    assert response.access_token == "abc123"


def test_steam_web_cookies_as_dict_maps_to_the_cookie_names() -> None:
    cookies = SteamWebCookies(
        steamid=76561198000000000,
        steam_login_secure="76561198000000000||token",
        session_id="deadbeef",
    )

    assert cookies.as_dict() == {
        "steamLoginSecure": "76561198000000000||token",
        "sessionid": "deadbeef",
    }


def _make_item(**overrides: object) -> InventoryItem:
    defaults: dict[str, object] = dict(
        appid=730,
        contextid=2,
        assetid=1,
        classid=100,
        instanceid=0,
        amount=1,
    )
    defaults.update(overrides)
    return InventoryItem.model_validate(defaults)


def test_inventory_item_defaults_description_fields_to_falsy() -> None:
    item = _make_item()

    assert item.name is None
    assert item.tradable is False
    assert item.marketable is False
    assert item.tags == []
    assert item.descriptions == []


def test_display_name_prefers_market_hash_name_then_name_then_classid() -> None:
    assert _make_item(market_hash_name="AK-47 | Redline", name="AK-47").display_name == (
        "AK-47 | Redline"
    )
    assert _make_item(name="AK-47").display_name == "AK-47"
    assert _make_item(classid=999).display_name == "classid=999"


def test_time_until_tradable_is_none_without_a_hold() -> None:
    assert _make_item().time_until_tradable() is None


def test_time_until_tradable_returns_the_remaining_delta() -> None:
    item = _make_item(tradable_after=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    remaining = item.time_until_tradable(datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc))

    assert remaining == timedelta(hours=2)


def test_time_until_tradable_floors_at_zero_once_expired() -> None:
    item = _make_item(tradable_after=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc))

    remaining = item.time_until_tradable(datetime(2026, 1, 2, tzinfo=timezone.utc))

    assert remaining == timedelta(0)


def test_tag_value_finds_by_category() -> None:
    item = _make_item(
        tags=[
            {"category": "Rarity", "localized_tag_name": "Covert"},
            {"category": "Weapon", "localized_tag_name": "AK-47"},
        ]
    )

    assert item.tag_value("Rarity") == "Covert"
    assert item.tag_value("Exterior") is None


def test_inventory_len_reflects_item_count() -> None:
    inventory = Inventory(
        steamid=1,
        appid=730,
        contextid=2,
        items=[_make_item(assetid=1), _make_item(assetid=2)],
    )

    assert len(inventory) == 2


def test_inventory_is_iterable_and_indexable() -> None:
    item_a, item_b = _make_item(assetid=1), _make_item(assetid=2)
    inventory = Inventory(steamid=1, appid=730, contextid=2, items=[item_a, item_b])

    assert list(inventory) == [item_a, item_b]
    assert inventory[0] is item_a
    assert inventory[1] is item_b


def test_inventory_get_looks_up_by_assetid() -> None:
    item = _make_item(assetid=42)
    inventory = Inventory(steamid=1, appid=730, contextid=2, items=[item])

    assert inventory.get(42) is item
    assert inventory.get(999) is None


def test_inventory_is_complete_compares_against_total_inventory_count() -> None:
    items = [_make_item(assetid=1), _make_item(assetid=2)]

    assert Inventory(steamid=1, appid=730, contextid=2, items=items).is_complete is None
    assert (
        Inventory(
            steamid=1, appid=730, contextid=2, items=items, total_inventory_count=2
        ).is_complete
        is True
    )
    assert (
        Inventory(
            steamid=1, appid=730, contextid=2, items=items, total_inventory_count=5
        ).is_complete
        is False
    )


def test_inventory_tradable_marketable_and_held_filters() -> None:
    tradable = _make_item(assetid=1, tradable=True)
    marketable = _make_item(assetid=2, marketable=True)
    held = _make_item(assetid=3, sealed=True)
    inventory = Inventory(
        steamid=1, appid=730, contextid=2, items=[tradable, marketable, held]
    )

    assert inventory.tradable_items == [tradable]
    assert inventory.marketable_items == [marketable]
    assert inventory.held_items == [held]


def test_inventory_counts_and_groups_by_name() -> None:
    items = [
        _make_item(assetid=1, market_hash_name="Case"),
        _make_item(assetid=2, market_hash_name="Case"),
        _make_item(assetid=3, market_hash_name="Key", amount=3),
    ]
    inventory = Inventory(steamid=1, appid=730, contextid=2, items=items)

    assert inventory.counts_by_name() == {"Case": 2, "Key": 3}
    groups = inventory.group_by_name()
    assert [item.assetid for item in groups["Case"]] == [1, 2]
    assert [item.assetid for item in groups["Key"]] == [3]


def test_inventory_model_dump_json_mode_is_json_dumpable_with_a_trade_hold() -> None:
    item = _make_item(tradable_after=datetime(2026, 8, 24, 11, 0, 0, tzinfo=timezone.utc))
    inventory = Inventory(steamid=1, appid=730, contextid=2, items=[item])

    dumped = inventory.model_dump(mode="json")

    assert dumped["items"][0]["tradable_after"] == "2026-08-24T11:00:00Z"
    json.dumps(dumped)
