from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel


class GenerateAccessTokenForAppResponse(BaseModel):
    """``CAuthentication_AccessToken_GenerateForApp_Response``."""

    access_token: str | None = None
    refresh_token: str | None = None


class SteamWebCookies(BaseModel):
    """Cookies ready to drop into a ``requests``/``httpx`` session for
    ``steamcommunity.com`` / ``store.steampowered.com`` / ``help.steampowered.com``.
    """

    steamid: int
    steam_login_secure: str
    session_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "steamLoginSecure": self.steam_login_secure,
            "sessionid": self.session_id,
        }


class InventoryDescriptionLine(BaseModel):
    """``CEconItem_DescriptionLine``: one line of an item's rendered description."""

    type: str | None = None
    value: str | None = None
    color: str | None = None
    label: str | None = None
    name: str | None = None


class InventoryTag(BaseModel):
    """``CEconItem_Tag``: one facet/filter tag (rarity, type, exterior, ...)."""

    category: str | None = None
    internal_name: str | None = None
    localized_category_name: str | None = None
    localized_tag_name: str | None = None
    color: str | None = None


class InventoryAction(BaseModel):
    """``CEconItem_Action``: a context-menu action link (e.g. "Inspect in Game")."""

    link: str | None = None
    name: str | None = None


class InventoryItem(BaseModel):
    """One inventory entry: an owned asset (``CEcon_Asset``, unique per
    ``assetid``) merged with its shared item description (``CEconItem_Description``,
    shared by every asset with the same ``classid``/``instanceid``).
    """

    appid: int
    contextid: int
    assetid: int
    classid: int
    instanceid: int
    amount: int

    name: str | None = None
    market_name: str | None = None
    market_hash_name: str | None = None
    name_color: str | None = None
    background_color: str | None = None
    icon_url: str | None = None
    icon_url_large: str | None = None
    type: str | None = None
    tradable: bool = False
    marketable: bool = False
    commodity: bool = False
    market_tradable_restriction: int | None = None
    market_marketable_restriction: int | None = None
    descriptions: list[InventoryDescriptionLine] = []
    owner_descriptions: list[InventoryDescriptionLine] = []
    tags: list[InventoryTag] = []
    actions: list[InventoryAction] = []
    fraudwarnings: list[str] = []

    sealed: bool = False

    tradable_after: datetime | None = None
    trade_hold_note: str | None = None

    @property
    def display_name(self) -> str:
        """The best available human-readable name: ``market_hash_name``, then
        ``name``, then a ``classid``-based placeholder if Steam sent no
        description for this item at all.
        """
        return self.market_hash_name or self.name or f"classid={self.classid}"

    def in_trade_hold(self, now: datetime | None = None) -> bool:
        """Whether this item is still within its trade hold at ``now`` (UTC,
        defaults to the current time).

        Falls back to the ``sealed`` flag when no date could be parsed (e.g.
        CS2 Trade Protected items whose free-text notice didn't match any
        known phrasing) - so this doesn't silently report "not held" for an
        item Steam itself flags as held.
        """
        if self.tradable_after is not None:
            current = now if now is not None else datetime.now(timezone.utc)
            return current < self.tradable_after
        return self.sealed

    def time_until_tradable(self, now: datetime | None = None) -> timedelta | None:
        """How long until ``tradable_after`` - ``None`` if there's no parsed
        hold end date (see ``tradable_after``/``trade_hold_note``), or
        ``timedelta(0)`` if the hold has already expired.
        """
        if self.tradable_after is None:
            return None
        current = now if now is not None else datetime.now(timezone.utc)
        remaining = self.tradable_after - current
        return remaining if remaining > timedelta(0) else timedelta(0)

    def tag_value(self, category: str) -> str | None:
        """The localized tag name for a given tag ``category``
        (e.g. ``"Rarity"``, ``"Exterior"``, ``"Weapon"``, ``"Type"``), or
        ``None`` if this item has no tag in that category.
        """
        for tag in self.tags:
            if tag.category == category:
                return tag.localized_tag_name
        return None


class Inventory(BaseModel):
    """A full, paginated ``Econ.GetInventoryItemsWithDescriptions`` result for
    one ``(steamid, appid, contextid)``.

    Iterable directly (``for item in inventory``) and indexable by position
    (``inventory[0]``); use :meth:`get` to look an item up by ``assetid``.
    """

    steamid: int
    appid: int
    contextid: int
    items: list[InventoryItem]
    total_inventory_count: int | None = None

    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[InventoryItem]:  # type: ignore[override]
        return iter(self.items)

    def __getitem__(self, index: int) -> InventoryItem:
        return self.items[index]

    @property
    def is_complete(self) -> bool | None:
        """Whether every item Steam reports owning was actually fetched -
        ``len(self) == total_inventory_count``. ``None`` if Steam didn't
        report a ``total_inventory_count`` to compare against.
        """
        if self.total_inventory_count is None:
            return None
        return len(self.items) == self.total_inventory_count

    @property
    def tradable_items(self) -> list[InventoryItem]:
        return [item for item in self.items if item.tradable]

    @property
    def marketable_items(self) -> list[InventoryItem]:
        return [item for item in self.items if item.marketable]

    @property
    def held_items(self) -> list[InventoryItem]:
        """Items currently within a trade hold - see ``InventoryItem.in_trade_hold``."""
        return [item for item in self.items if item.in_trade_hold()]

    def get(self, assetid: int) -> InventoryItem | None:
        """Look up a single item by ``assetid``, or ``None`` if it's not in
        this inventory.
        """
        for item in self.items:
            if item.assetid == assetid:
                return item
        return None

    def counts_by_name(self) -> dict[str, int]:
        """Total ``amount`` owned per unique item name (``display_name``) -
        e.g. how many of each case/sticker/currency item this inventory holds.
        """
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.display_name] = counts.get(item.display_name, 0) + item.amount
        return counts

    def group_by_name(self) -> dict[str, list[InventoryItem]]:
        """Every item (each a distinct ``assetid``), grouped by unique item
        name (``display_name``).
        """
        groups: dict[str, list[InventoryItem]] = {}
        for item in self.items:
            groups.setdefault(item.display_name, []).append(item)
        return groups
