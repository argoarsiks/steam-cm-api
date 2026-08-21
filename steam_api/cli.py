import argparse
import asyncio
import json
import sys

from steam_api.cm_client import SteamCMClient
from steam_api.exceptions import SteamApiError
from steam_api.inventory import (
    DEFAULT_MAX_PAGES,
    DEFAULT_PAGE_SIZE,
    get_inventory_via_cm,
)
from steam_api.schemas import Inventory, SteamWebCookies


def _print_cookies(cookies: SteamWebCookies) -> None:
    print(f"steamid:          {cookies.steamid}")
    print(f"steamLoginSecure: {cookies.steam_login_secure}")
    print(f"sessionid:        {cookies.session_id}")


def _print_inventory_summary(inventory: Inventory) -> None:
    print(f"steamid:  {inventory.steamid}")
    print(f"app:      {inventory.appid} (context {inventory.contextid})")
    print(f"items:    {len(inventory)}", end="")
    if inventory.total_inventory_count is not None:
        print(f" (Steam reports {inventory.total_inventory_count} total)")
    else:
        print()

    held = inventory.held_items
    if held:
        print(f"in trade hold: {len(held)}")
        for item in held:
            until = item.tradable_after.isoformat() if item.tradable_after else "unknown date"
            print(f"  {item.display_name}: tradable after {until}")


async def _run_cookies(refresh_token: str, account_name: str) -> None:
    async with SteamCMClient() as client:
        cookies = await client.get_web_cookies(refresh_token, account_name)
    _print_cookies(cookies)


async def _run_inventory(args: argparse.Namespace) -> None:
    inventory = await get_inventory_via_cm(
        args.refresh_token,
        args.account_name,
        args.appid,
        args.contextid,
        steamid=args.steamid,
        language=args.language,
        tradable_only=args.tradable_only,
        marketable_only=args.marketable_only,
        include_trade_locked=args.include_trade_locked,
        page_size=args.page_size,
        max_pages=args.max_pages,
    )

    if args.output:
        indent = 2 if args.pretty else None
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(inventory.model_dump(mode="json"), f, ensure_ascii=False, indent=indent)
        print(f"Wrote {len(inventory.items)} items to {args.output}", file=sys.stderr)
    elif args.json:
        print(inventory.model_dump_json(indent=2 if args.pretty else None))
    else:
        _print_inventory_summary(inventory)


def _run_extract_token(account_name: str) -> None:
    from steam_api.local_client import read_local_refresh_token

    print(read_local_refresh_token(account_name))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steam_api")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inventory = subparsers.add_parser(
        "inventory", help="Fetch an account's inventory over a real CM session"
    )
    inventory.add_argument(
        "refresh_token", help="Desktop/mobile refresh token of the logon account"
    )
    inventory.add_argument(
        "account_name", help="Logon account's login name (not persona/display name)"
    )
    inventory.add_argument("appid", type=int, help="e.g. 730 for CS2, 570 for Dota 2")
    inventory.add_argument("contextid", type=int, help="e.g. 2 for the standard trading context")
    inventory.add_argument(
        "--steamid",
        type=int,
        default=None,
        help="Whose inventory to fetch (SteamID64). Defaults to the logon account itself",
    )
    inventory.add_argument("--language", default="english")
    inventory.add_argument("--tradable-only", action="store_true")
    inventory.add_argument("--marketable-only", action="store_true")
    inventory.add_argument(
        "--exclude-trade-locked",
        dest="include_trade_locked",
        action="store_false",
        help=(
            "Don't include items currently on a trade hold. By default they're "
            "included (Steam otherwise omits them from the response entirely) "
            "- see InventoryItem.tradable_after/in_trade_hold()"
        ),
    )
    inventory.add_argument("--page-size", type=int, default=DEFAULT_PAGE_SIZE)
    inventory.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    inventory.add_argument("--json", action="store_true", help="Print the full inventory as JSON")
    inventory.add_argument("--pretty", action="store_true", help="Indent --json/--output output")
    inventory.add_argument("--output", default=None, help="Write the full inventory JSON to a file")

    cookies = subparsers.add_parser(
        "cookies", help="Exchange a refresh token for web session cookies via a real CM session"
    )
    cookies.add_argument("refresh_token")
    cookies.add_argument("account_name", help="Login name, not persona/display name")

    extract = subparsers.add_parser(
        "extract-token",
        help="Read the refresh token from the local Steam client's storage (Windows only)",
    )
    extract.add_argument("account_name", help="Login name, not persona/display name")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        if args.command == "inventory":
            asyncio.run(_run_inventory(args))
        elif args.command == "cookies":
            asyncio.run(_run_cookies(args.refresh_token, args.account_name))
        elif args.command == "extract-token":
            _run_extract_token(args.account_name)
    except SteamApiError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
