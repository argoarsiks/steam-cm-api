"""Unit tests for the asset/description merge in inventory.py.

These use plain SimpleNamespace stand-ins for CEcon_Asset/CEconItem_Description
(the merge function only reads attributes by name, never anything
protobuf-specific) so they run without needing the generated pb2 code.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from steam_api.inventory import _merge_assets_and_descriptions


def _asset(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = dict(
        appid=730,
        contextid=2,
        assetid=1,
        classid=100,
        instanceid=0,
        amount=1,
        missing=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _description(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = dict(
        classid=100,
        instanceid=0,
        name="AK-47 | Redline",
        market_name="AK-47 | Redline (Field-Tested)",
        market_hash_name="AK-47 | Redline (Field-Tested)",
        name_color="D2D2D2",
        background_color="",
        icon_url="icon",
        icon_url_large="icon_large",
        type="Rifle",
        tradable=True,
        marketable=True,
        commodity=False,
        market_tradable_restriction=7,
        market_marketable_restriction=7,
        descriptions=[],
        owner_descriptions=[],
        tags=[],
        actions=[],
        fraudwarnings=[],
        item_expiration="",
        sealed=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_merge_joins_asset_with_its_matching_description() -> None:
    items = _merge_assets_and_descriptions([_asset()], [_description()])

    assert len(items) == 1
    item = items[0]
    assert item.assetid == 1
    assert item.name == "AK-47 | Redline"
    assert item.tradable is True
    assert item.marketable is True


def test_merge_matches_by_classid_and_instanceid_not_position() -> None:
    asset = _asset(classid=200, instanceid=5)
    other_description = _description(classid=100, instanceid=0, name="wrong item")
    matching_description = _description(classid=200, instanceid=5, name="right item")

    items = _merge_assets_and_descriptions([asset], [other_description, matching_description])

    assert items[0].name == "right item"


def test_merge_keeps_assets_with_no_matching_description() -> None:
    asset = _asset(classid=999, instanceid=999)

    items = _merge_assets_and_descriptions([asset], [_description()])

    assert len(items) == 1
    assert items[0].name is None
    assert items[0].tradable is False


def test_merge_skips_missing_assets() -> None:
    items = _merge_assets_and_descriptions([_asset(missing=True)], [_description()])

    assert items == []


def test_merge_handles_multiple_copies_of_the_same_item() -> None:
    assets = [_asset(assetid=1), _asset(assetid=2), _asset(assetid=3)]

    items = _merge_assets_and_descriptions(assets, [_description()])

    assert [item.assetid for item in items] == [1, 2, 3]
    assert all(item.name == "AK-47 | Redline" for item in items)


def test_merge_carries_over_tags_and_description_lines() -> None:
    description = _description(
        tags=[
            SimpleNamespace(
                category="Rarity",
                internal_name="Rarity_Legendary",
                localized_category_name="Quality",
                localized_tag_name="Covert",
                color="eb4b4b",
            )
        ],
        descriptions=[
            SimpleNamespace(
                type="html", value="Exterior: Field-Tested", color="", label="", name=""
            )
        ],
    )

    items = _merge_assets_and_descriptions([_asset()], [description])

    assert items[0].tags[0].localized_tag_name == "Covert"
    assert items[0].descriptions[0].value == "Exterior: Field-Tested"


def test_merge_picks_up_trade_hold_from_owner_descriptions() -> None:
    description = _description(
        owner_descriptions=[
            SimpleNamespace(
                type="html",
                value="Tradable/Marketable After 3 Feb, 2099 (18:00:00) GMT",
                color="",
                label="",
                name="",
            )
        ],
    )

    items = _merge_assets_and_descriptions([_asset()], [description])

    assert items[0].tradable_after == datetime(2099, 2, 3, 18, 0, 0, tzinfo=timezone.utc)
    assert items[0].trade_hold_note == "Tradable/Marketable After 3 Feb, 2099 (18:00:00) GMT"
    assert items[0].in_trade_hold(datetime(2099, 1, 1, tzinfo=timezone.utc)) is True
    assert items[0].in_trade_hold(datetime(2099, 3, 1, tzinfo=timezone.utc)) is False


def test_merge_falls_back_to_public_descriptions_for_trade_hold() -> None:
    description = _description(
        descriptions=[
            SimpleNamespace(
                type="html",
                value="Tradable/Marketable After 3 Feb, 2099 (18:00:00) GMT",
                color="",
                label="",
                name="",
            )
        ],
    )

    items = _merge_assets_and_descriptions([_asset()], [description])

    assert items[0].tradable_after == datetime(2099, 2, 3, 18, 0, 0, tzinfo=timezone.utc)


def test_merge_falls_back_to_item_expiration_for_trade_hold() -> None:
    description = _description(item_expiration="2099-02-03T18:00:00Z")

    items = _merge_assets_and_descriptions([_asset()], [description])

    assert items[0].tradable_after == datetime(2099, 2, 3, 18, 0, 0, tzinfo=timezone.utc)


def test_merge_carries_over_sealed_and_falls_back_to_it_for_trade_hold() -> None:
    description = _description(sealed=True)

    items = _merge_assets_and_descriptions([_asset()], [description])

    assert items[0].sealed is True
    assert items[0].tradable_after is None
    assert items[0].trade_hold_note == "TRADE HOLD"
    assert items[0].in_trade_hold() is True


def test_merge_leaves_trade_hold_fields_unset_when_no_hold_notice() -> None:
    items = _merge_assets_and_descriptions([_asset()], [_description()])

    assert items[0].sealed is False
    assert items[0].tradable_after is None
    assert items[0].trade_hold_note is None
    assert items[0].in_trade_hold() is False
