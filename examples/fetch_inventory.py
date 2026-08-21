"""Пример: получить инвентарь Steam-аккаунта через реальную CM-сессию.

Заполни константы ниже своими данными и запусти:

    uv run python examples/fetch_inventory.py
"""

import asyncio
import json

from steam_api import SteamApiError, get_inventory_via_cm

REFRESH_TOKEN = ""
ACCOUNT_NAME = ""

APPID = 730
CONTEXTID = 2

STEAMID: int | None = 76561198364721123


async def main() -> None:
    if not REFRESH_TOKEN or not ACCOUNT_NAME:
        raise SystemExit("Заполни REFRESH_TOKEN и ACCOUNT_NAME выше")

    try:
        inventory = await get_inventory_via_cm(
            REFRESH_TOKEN,
            ACCOUNT_NAME,
            APPID,
            CONTEXTID,
            steamid=STEAMID,
        )
    except SteamApiError as exc:
        print(f"Ошибка: {exc}")
        return

    print(f"steamid: {inventory.steamid}")
    print(f"appid={inventory.appid} contextid={inventory.contextid}")
    print(f"предметов получено: {len(inventory)}")
    if inventory.total_inventory_count is not None:
        print(f"всего в инвентаре (по данным Steam): {inventory.total_inventory_count}")

    print()
    for item in inventory.items[:20]:
        print(f"  x{item.amount}  {item.display_name}")

    if len(inventory) > 20:
        print(f"  ... и ещё {len(inventory) - 20}")

    held = inventory.held_items
    if held:
        print(f"\nв трейд-холде: {len(held)}")
        for item in held:
            remaining = item.time_until_tradable()
            if remaining is not None:
                until = item.tradable_after.isoformat() if item.tradable_after else "?"
                print(f"  {item.display_name}: освободится {until} (через {remaining})")
            else:
                print(f"  {item.display_name}: холд есть, но дату распознать не удалось: {item.trade_hold_note!r}")
    else:
        print("\nпредметов в трейд-холде нет")

    with open("inventory.json", "w", encoding="utf-8") as f:
        json.dump(inventory.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
    print("\nПолный инвентарь сохранён в inventory.json")


if __name__ == "__main__":
    asyncio.run(main())
