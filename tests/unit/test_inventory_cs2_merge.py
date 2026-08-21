"""Unit tests for fetch_inventory()'s CS2 "Trade Protected" (context 16)
merge - the fix for trade-held CS2 items being silently absent from the
normal context 2 response no matter what request flags are set.
"""

from steam_api.inventory import fetch_inventory
from steam_api.proto import (
    CEcon_GetInventoryItemsWithDescriptions_Request,
    CEcon_GetInventoryItemsWithDescriptions_Response,
)


def _response(items: list[tuple[dict, dict]]) -> CEcon_GetInventoryItemsWithDescriptions_Response:
    response = CEcon_GetInventoryItemsWithDescriptions_Response()
    for asset_kwargs, desc_kwargs in items:
        asset = response.assets.add()
        for key, value in asset_kwargs.items():
            setattr(asset, key, value)
        desc = response.descriptions.add()
        for key, value in desc_kwargs.items():
            setattr(desc, key, value)
    return response


class _FakeClient:
    def __init__(
        self, responses_by_contextid: dict[int, CEcon_GetInventoryItemsWithDescriptions_Response]
    ) -> None:
        self._responses = responses_by_contextid
        self.contextids_called: list[int] = []

    async def call_service_method(
        self, target_job_name: str, steamid: int, request_body: bytes
    ) -> bytes:
        request = CEcon_GetInventoryItemsWithDescriptions_Request()
        request.ParseFromString(request_body)
        self.contextids_called.append(request.contextid)
        response = self._responses.get(
            request.contextid, CEcon_GetInventoryItemsWithDescriptions_Response()
        )
        return response.SerializeToString()


_NORMAL_ITEM = (
    dict(appid=730, contextid=2, assetid=1, classid=10, instanceid=0, amount=1),
    dict(classid=10, instanceid=0, name="AK-47 | Redline", tradable=True, marketable=True),
)
_PROTECTED_ITEM = (
    dict(appid=730, contextid=16, assetid=2, classid=20, instanceid=0, amount=1),
    dict(
        classid=20,
        instanceid=0,
        name="AWP | Dragon Lore",
        sealed=True,
        tradable=False,
        marketable=False,
    ),
)


async def test_fetch_inventory_merges_cs2_context_16_by_default() -> None:
    client = _FakeClient({2: _response([_NORMAL_ITEM]), 16: _response([_PROTECTED_ITEM])})

    inventory = await fetch_inventory(client, steamid=1, appid=730, contextid=2)

    assert sorted(client.contextids_called) == [2, 16]
    names = {item.assetid: item.name for item in inventory.items}
    assert names == {1: "AK-47 | Redline", 2: "AWP | Dragon Lore"}
    protected = next(item for item in inventory.items if item.assetid == 2)
    assert protected.sealed is True
    assert protected.in_trade_hold() is True


async def test_fetch_inventory_dedupes_assetids_shared_between_contexts() -> None:
    duplicate_in_both = (
        dict(appid=730, contextid=2, assetid=1, classid=10, instanceid=0, amount=1),
        dict(classid=10, instanceid=0, name="AK-47 | Redline", tradable=True, marketable=True),
    )
    client = _FakeClient(
        {2: _response([duplicate_in_both]), 16: _response([duplicate_in_both, _PROTECTED_ITEM])}
    )

    inventory = await fetch_inventory(client, steamid=1, appid=730, contextid=2)

    assert sorted(item.assetid for item in inventory.items) == [1, 2]


async def test_fetch_inventory_skips_context_16_for_non_cs2_apps() -> None:
    client = _FakeClient({2: _response([_NORMAL_ITEM])})

    await fetch_inventory(client, steamid=1, appid=570, contextid=2)

    assert client.contextids_called == [2]


async def test_fetch_inventory_skips_context_16_when_trade_locked_excluded() -> None:
    client = _FakeClient({2: _response([_NORMAL_ITEM]), 16: _response([_PROTECTED_ITEM])})

    inventory = await fetch_inventory(
        client, steamid=1, appid=730, contextid=2, include_trade_locked=False
    )

    assert client.contextids_called == [2]
    assert len(inventory.items) == 1


async def test_fetch_inventory_skips_context_16_when_filtering_by_tradable_only() -> None:
    client = _FakeClient({2: _response([_NORMAL_ITEM])})

    await fetch_inventory(client, steamid=1, appid=730, contextid=2, tradable_only=True)

    assert client.contextids_called == [2]


async def test_fetch_inventory_does_not_recurse_into_context_16_itself() -> None:
    client = _FakeClient({16: _response([_PROTECTED_ITEM])})

    await fetch_inventory(client, steamid=1, appid=730, contextid=16)

    assert client.contextids_called == [16]


async def test_fetch_inventory_tolerates_context_16_fetch_failure() -> None:
    class _FailingContext16Client(_FakeClient):
        async def call_service_method(
            self, target_job_name: str, steamid: int, request_body: bytes
        ) -> bytes:
            request = CEcon_GetInventoryItemsWithDescriptions_Request()
            request.ParseFromString(request_body)
            if request.contextid == 16:
                raise ConnectionResetError("boom")
            return await super().call_service_method(target_job_name, steamid, request_body)

    client = _FailingContext16Client({2: _response([_NORMAL_ITEM])})

    inventory = await fetch_inventory(client, steamid=1, appid=730, contextid=2)

    assert len(inventory.items) == 1
