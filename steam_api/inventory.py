"""Fetch a Steam account's inventory over an authenticated CM session, via
``Econ.GetInventoryItemsWithDescriptions`` - the CM-native RPC the official
Steam client itself uses, and the one Valve points third parties at now that
the old ``/inventory/<steamid>/<appid>/<contextid>`` web endpoint is
deprecated and heavily rate-limited.

:func:`fetch_inventory` drives the paginated RPC over an already-logged-on
:class:`~steam_api.cm_client.SteamCMClient` and merges the two parallel lists
the response carries - per-copy ``CEcon_Asset`` entries and the shared,
per-``(classid, instanceid)`` ``CEconItem_Description`` records - into flat
:class:`~steam_api.schemas.InventoryItem` objects. :func:`get_inventory_via_cm`
is a one-shot convenience wrapper that also handles the connect/logon/logoff
around it, for callers that only need a single inventory fetch.

By default the request also sets ``for_trade_offer_verification`` (see
``include_trade_locked`` below) - without it, Steam silently omits items
currently on a trade hold from the response instead of flagging them, which
looks like the inventory is missing items rather than just their hold state.
For CS2 specifically, held ("Trade Protected") items live in an entirely
separate inventory context (16, not the normal 2) regardless of that flag -
see ``CS2_TRADE_PROTECTED_CONTEXTID`` - and get merged in automatically.
"""

from collections.abc import Iterable
from typing import Any

from steam_api.cm_client import SteamCMClient
from steam_api.exceptions import SteamApiError, SteamProtocolError
from steam_api.proto import (
    CEcon_Asset,
    CEcon_GetInventoryItemsWithDescriptions_Request,
    CEcon_GetInventoryItemsWithDescriptions_Response,
    CEconItem_Description,
)
from steam_api.schemas import (
    Inventory,
    InventoryAction,
    InventoryDescriptionLine,
    InventoryItem,
    InventoryTag,
)
from steam_api.token_claims import get_steamid_from_token
from steam_api.trade_hold import resolve_trade_hold

TARGET_JOB_GET_INVENTORY = "Econ.GetInventoryItemsWithDescriptions#1"

DEFAULT_PAGE_SIZE = 1000
DEFAULT_MAX_PAGES = 200

CS2_APPID = 730
CS2_MAIN_CONTEXTID = 2
CS2_TRADE_PROTECTED_CONTEXTID = 16


def _merge_assets_and_descriptions(
    assets: Iterable[Any], descriptions: Iterable[Any]
) -> list[InventoryItem]:
    """Join owned-copy assets with their shared item descriptions.

    Pure/duck-typed: ``assets``/``descriptions`` only need the same attribute
    names as ``CEcon_Asset``/``CEconItem_Description`` (real protobuf messages
    or plain stand-ins in tests) - nothing protobuf-specific is used.

    Assets with no matching description (``missing=True`` entries, or a
    ``classid``/``instanceid`` Steam didn't send a description for) still
    produce an :class:`InventoryItem`, just with the description fields left
    at their defaults - dropping them silently would understate the account's
    actual item count.
    """
    descriptions_by_class = {(d.classid, d.instanceid): d for d in descriptions}
    items = []

    for asset in assets:
        if asset.missing:
            continue

        desc = descriptions_by_class.get((asset.classid, asset.instanceid))

        tradable_after, trade_hold_note = (
            resolve_trade_hold(
                desc.owner_descriptions,
                desc.descriptions,
                item_expiration=desc.item_expiration,
                sealed=bool(desc.sealed),
            )
            if desc
            else (None, None)
        )

        items.append(
            InventoryItem(
                appid=asset.appid,
                contextid=asset.contextid,
                assetid=asset.assetid,
                classid=asset.classid,
                instanceid=asset.instanceid,
                amount=asset.amount,
                name=desc.name if desc else None,
                market_name=desc.market_name if desc else None,
                market_hash_name=desc.market_hash_name if desc else None,
                name_color=desc.name_color if desc else None,
                background_color=desc.background_color if desc else None,
                icon_url=desc.icon_url if desc else None,
                icon_url_large=desc.icon_url_large if desc else None,
                type=desc.type if desc else None,
                tradable=bool(desc.tradable) if desc else False,
                marketable=bool(desc.marketable) if desc else False,
                commodity=bool(desc.commodity) if desc else False,
                market_tradable_restriction=(
                    desc.market_tradable_restriction if desc else None
                ),
                market_marketable_restriction=(
                    desc.market_marketable_restriction if desc else None
                ),
                descriptions=[
                    InventoryDescriptionLine(
                        type=line.type,
                        value=line.value,
                        color=line.color,
                        label=line.label,
                        name=line.name,
                    )
                    for line in (desc.descriptions if desc else [])
                ],
                owner_descriptions=[
                    InventoryDescriptionLine(
                        type=line.type,
                        value=line.value,
                        color=line.color,
                        label=line.label,
                        name=line.name,
                    )
                    for line in (desc.owner_descriptions if desc else [])
                ],
                tags=[
                    InventoryTag(
                        category=tag.category,
                        internal_name=tag.internal_name,
                        localized_category_name=tag.localized_category_name,
                        localized_tag_name=tag.localized_tag_name,
                        color=tag.color,
                    )
                    for tag in (desc.tags if desc else [])
                ],
                actions=[
                    InventoryAction(link=action.link, name=action.name)
                    for action in (desc.actions if desc else [])
                ],
                fraudwarnings=list(desc.fraudwarnings) if desc else [],
                sealed=bool(desc.sealed) if desc else False,
                tradable_after=tradable_after,
                trade_hold_note=trade_hold_note,
            )
        )

    return items


async def _fetch_page(
    client: SteamCMClient,
    steamid: int,
    appid: int,
    contextid: int,
    *,
    language: str,
    tradable_only: bool,
    marketable_only: bool,
    include_trade_locked: bool,
    start_assetid: int,
    count: int,
) -> CEcon_GetInventoryItemsWithDescriptions_Response:
    request = CEcon_GetInventoryItemsWithDescriptions_Request()
    request.steamid = steamid
    request.appid = appid
    request.contextid = contextid
    request.get_descriptions = True
    request.language = language
    request.count = count
    request.for_trade_offer_verification = include_trade_locked
    if start_assetid:
        request.start_assetid = start_assetid
    if tradable_only:
        request.filters.tradable_only = True
    if marketable_only:
        request.filters.marketable_only = True

    body = await client.call_service_method(
        TARGET_JOB_GET_INVENTORY, steamid, request.SerializeToString()
    )

    response = CEcon_GetInventoryItemsWithDescriptions_Response()
    response.ParseFromString(body)
    return response


async def _fetch_all_pages(
    client: SteamCMClient,
    steamid: int,
    appid: int,
    contextid: int,
    *,
    language: str,
    tradable_only: bool,
    marketable_only: bool,
    include_trade_locked: bool,
    page_size: int,
    max_pages: int,
) -> tuple[list[CEcon_Asset], list[CEconItem_Description], int | None]:
    """Drive the paginated RPC for one ``(steamid, appid, contextid)`` to
    completion and return the raw, un-merged ``assets``/``descriptions``.

    :raises SteamProtocolError: ``max_pages`` was reached with more items still
        pending, or Steam's response was otherwise malformed.
    """
    all_assets: list[CEcon_Asset] = []
    all_descriptions: list[CEconItem_Description] = []
    total_inventory_count: int | None = None
    start_assetid = 0

    for _ in range(max_pages):
        response = await _fetch_page(
            client,
            steamid,
            appid,
            contextid,
            language=language,
            tradable_only=tradable_only,
            marketable_only=marketable_only,
            include_trade_locked=include_trade_locked,
            start_assetid=start_assetid,
            count=page_size,
        )

        all_assets.extend(response.assets)
        all_descriptions.extend(response.descriptions)
        if response.HasField("total_inventory_count"):
            total_inventory_count = response.total_inventory_count

        if not response.more_items:
            break
        if not response.last_assetid:
            raise SteamProtocolError(
                "GetInventoryItemsWithDescriptions reported more_items without a last_assetid"
            )
        start_assetid = response.last_assetid
    else:
        raise SteamProtocolError(
            f"Inventory still has more items after {max_pages} pages "
            f"({len(all_assets)} assets fetched so far) - raise max_pages"
        )

    return all_assets, all_descriptions, total_inventory_count


async def fetch_inventory(
    client: SteamCMClient,
    steamid: int,
    appid: int,
    contextid: int,
    *,
    language: str = "english",
    tradable_only: bool = False,
    marketable_only: bool = False,
    include_trade_locked: bool = True,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> Inventory:
    """Fetch the full inventory for ``(steamid, appid, contextid)`` over
    ``client``, which must already be logged on (see
    :meth:`~steam_api.cm_client.SteamCMClient.logon_with_refresh_token`).

    Pages via ``start_assetid``/``more_items``/``last_assetid`` until Steam
    reports no more items or ``max_pages`` is hit (raises rather than silently
    truncating, so callers don't mistake a capped fetch for the whole inventory).

    :param include_trade_locked: include items currently under a trade hold
        (``for_trade_offer_verification`` on the request). Defaults to ``True``
        because Steam otherwise drops held items from the response outright -
        set ``False`` to mirror what a plain (non-owner) inventory view shows.
        Also controls whether CS2's separate "Trade Protected" context (see
        ``CS2_TRADE_PROTECTED_CONTEXTID``) gets merged in, for
        ``appid=730, contextid=2`` requests.
    :raises SteamLogonError: Steam rejected the call (e.g. a private inventory
        you don't own, or the account is rate-limited).
    :raises SteamProtocolError: ``max_pages`` was reached with more items still
        pending, or Steam's response was otherwise malformed.
    """
    all_assets, all_descriptions, total_inventory_count = await _fetch_all_pages(
        client,
        steamid,
        appid,
        contextid,
        language=language,
        tradable_only=tradable_only,
        marketable_only=marketable_only,
        include_trade_locked=include_trade_locked,
        page_size=page_size,
        max_pages=max_pages,
    )

    if (
        include_trade_locked
        and appid == CS2_APPID
        and contextid == CS2_MAIN_CONTEXTID
        and not tradable_only
        and not marketable_only
    ):
        try:
            protected_assets, protected_descriptions, _ = await _fetch_all_pages(
                client,
                steamid,
                appid,
                CS2_TRADE_PROTECTED_CONTEXTID,
                language=language,
                tradable_only=False,
                marketable_only=False,
                include_trade_locked=True,
                page_size=page_size,
                max_pages=max_pages,
            )
        except (SteamApiError, OSError, TimeoutError):
            pass
        else:
            seen_assetids = {asset.assetid for asset in all_assets}
            all_assets.extend(
                asset for asset in protected_assets if asset.assetid not in seen_assetids
            )
            all_descriptions.extend(protected_descriptions)

    items = _merge_assets_and_descriptions(all_assets, all_descriptions)

    return Inventory(
        steamid=steamid,
        appid=appid,
        contextid=contextid,
        items=items,
        total_inventory_count=total_inventory_count,
    )


async def get_inventory_via_cm(
    refresh_token: str,
    account_name: str,
    appid: int,
    contextid: int,
    steamid: int | None = None,
    *,
    language: str = "english",
    tradable_only: bool = False,
    marketable_only: bool = False,
    include_trade_locked: bool = True,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_pages: int = DEFAULT_MAX_PAGES,
    connect_timeout: float = 10.0,
    message_timeout: float = 15.0,
    max_server_attempts: int = 5,
) -> Inventory:
    """One-shot inventory fetch: connect to a CM server, log on with the
    refresh token, fetch every page of ``(steamid, appid, contextid)``'s
    inventory, and log off.

    :param account_name: the *logged-on* account's login name (not persona/
        display name). Required by CM even for token-based logon.
    :param steamid: whose inventory to fetch. Defaults to the refresh token's
        own account (via its ``sub`` claim) - pass a different SteamID64 to
        fetch another (public) inventory using this account's session.
    :param include_trade_locked: see :func:`fetch_inventory` - defaults to
        ``True`` so items currently on a trade hold aren't silently dropped.
    :raises SteamConnectionError: no CM server could be reached.
    :raises SteamLogonError: Steam rejected the logon, or the inventory call
        (e.g. a private inventory, or rate limiting).
    :raises SteamProtocolError: an unexpected/malformed response was received.
    """
    logon_steamid = get_steamid_from_token(refresh_token)
    target_steamid = steamid if steamid is not None else logon_steamid

    async with SteamCMClient(
        connect_timeout=connect_timeout,
        message_timeout=message_timeout,
        max_server_attempts=max_server_attempts,
    ) as client:
        await client.logon_with_refresh_token(refresh_token, logon_steamid, account_name)
        try:
            return await fetch_inventory(
                client,
                target_steamid,
                appid,
                contextid,
                language=language,
                tradable_only=tradable_only,
                marketable_only=marketable_only,
                include_trade_locked=include_trade_locked,
                page_size=page_size,
                max_pages=max_pages,
            )
        finally:
            await client.logoff()
