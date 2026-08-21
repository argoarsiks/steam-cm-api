"""Fetch a Steam account's inventory (and, incidentally, web session cookies)
from a desktop/mobile refresh token, over a real CM (Connection Manager)
session - the same protocol path the official Steam client itself uses.

Desktop/mobile refresh tokens are only valid there - not against the public,
cookie-oriented web endpoints (``finalizelogin``, ``/inventory/...``), and the
``/inventory/<steamid>/<appid>/<contextid>`` web endpoint is itself deprecated
and heavily rate-limited - hence a full CM client rather than a cookie jar:
TCP connect, RSA/AES channel handshake, log on with the refresh token, then
call ``Econ.GetInventoryItemsWithDescriptions`` over the authenticated,
encrypted channel::

    from steam_api import get_inventory_via_cm

    inventory = await get_inventory_via_cm(
        refresh_token, account_name, appid=730, contextid=2,
    )

    for item in inventory:
        print(item.amount, item.display_name)

    for item in inventory.held_items:
        print(item.display_name, "tradable after", item.tradable_after)

:class:`Inventory`/:class:`InventoryItem` carry the merged, typed result -
iterate an :class:`Inventory` directly, index it, look items up by
``assetid`` (:meth:`Inventory.get`), or use :attr:`Inventory.tradable_items`,
:attr:`Inventory.marketable_items`, :attr:`Inventory.held_items`,
:meth:`Inventory.group_by_name`/:meth:`Inventory.counts_by_name`. Items
currently on a trade hold (including CS2's separate "Trade Protected"
context) are included by default and resolved into
:attr:`InventoryItem.tradable_after`/:meth:`InventoryItem.in_trade_hold`/
:meth:`InventoryItem.time_until_tradable`.

:class:`SteamCMClient` stays available directly for callers that want a
session they control themselves - e.g. multiple inventory fetches, or
combining them with :meth:`SteamCMClient.get_web_cookies`, without
reconnecting in between.
"""

from steam_api.cm_client import SteamCMClient, get_web_cookies_via_cm
from steam_api.exceptions import (
    InvalidTokenError,
    SteamApiError,
    SteamConnectionError,
    SteamLogonError,
    SteamProtocolError,
)
from steam_api.inventory import fetch_inventory, get_inventory_via_cm
from steam_api.schemas import (
    GenerateAccessTokenForAppResponse,
    Inventory,
    InventoryAction,
    InventoryDescriptionLine,
    InventoryItem,
    InventoryTag,
    SteamWebCookies,
)

__all__ = [
    "GenerateAccessTokenForAppResponse",
    "InvalidTokenError",
    "Inventory",
    "InventoryAction",
    "InventoryDescriptionLine",
    "InventoryItem",
    "InventoryTag",
    "SteamApiError",
    "SteamCMClient",
    "SteamConnectionError",
    "SteamLogonError",
    "SteamProtocolError",
    "SteamWebCookies",
    "fetch_inventory",
    "get_inventory_via_cm",
    "get_web_cookies_via_cm",
]
