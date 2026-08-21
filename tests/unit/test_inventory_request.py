"""Unit tests for the request _fetch_page builds.

Regression coverage for the bug where trade-held items were silently missing
from fetched inventories: Steam drops them from the response entirely unless
``for_trade_offer_verification`` is set on the request, so this locks in that
it's set by default (and can still be turned off).
"""

from steam_api.inventory import TARGET_JOB_GET_INVENTORY, _fetch_page
from steam_api.proto import (
    CEcon_GetInventoryItemsWithDescriptions_Request,
    CEcon_GetInventoryItemsWithDescriptions_Response,
)


class _FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, bytes]] = []

    async def call_service_method(
        self, target_job_name: str, steamid: int, request_body: bytes
    ) -> bytes:
        self.calls.append((target_job_name, steamid, request_body))
        return CEcon_GetInventoryItemsWithDescriptions_Response().SerializeToString()


def _sent_request(client: _FakeClient) -> CEcon_GetInventoryItemsWithDescriptions_Request:
    request = CEcon_GetInventoryItemsWithDescriptions_Request()
    request.ParseFromString(client.calls[0][2])
    return request


async def test_fetch_page_sets_for_trade_offer_verification_by_default() -> None:
    client = _FakeClient()

    await _fetch_page(
        client,
        steamid=1,
        appid=730,
        contextid=2,
        language="english",
        tradable_only=False,
        marketable_only=False,
        include_trade_locked=True,
        start_assetid=0,
        count=1000,
    )

    target_job_name, steamid, _body = client.calls[0]
    assert target_job_name == TARGET_JOB_GET_INVENTORY
    assert steamid == 1
    assert _sent_request(client).for_trade_offer_verification is True


async def test_fetch_page_can_exclude_trade_locked_items() -> None:
    client = _FakeClient()

    await _fetch_page(
        client,
        steamid=1,
        appid=730,
        contextid=2,
        language="english",
        tradable_only=False,
        marketable_only=False,
        include_trade_locked=False,
        start_assetid=0,
        count=1000,
    )

    assert _sent_request(client).for_trade_offer_verification is False


async def test_fetch_page_sets_filters_only_when_requested() -> None:
    client = _FakeClient()

    await _fetch_page(
        client,
        steamid=1,
        appid=730,
        contextid=2,
        language="english",
        tradable_only=True,
        marketable_only=False,
        include_trade_locked=True,
        start_assetid=42,
        count=500,
    )

    request = _sent_request(client)
    assert request.filters.tradable_only is True
    assert request.filters.marketable_only is False
    assert request.start_assetid == 42
    assert request.count == 500
