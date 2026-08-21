# steam-api

Fetch a Steam account's inventory from a desktop/mobile refresh token, over a
real CM (Connection Manager) session - the same protocol path the official
Steam client itself uses, via `Econ.GetInventoryItemsWithDescriptions`. Not
the deprecated/rate-limited `/inventory/<steamid>/<appid>/<contextid>` web
endpoint, and not `finalizelogin` (which only accepts web-platform tokens,
not the desktop/mobile ones a Steam client itself holds).

Fully typed (`py.typed`, mypy `--strict` clean).

```python
import asyncio
from steam_api import get_inventory_via_cm

async def main():
    inventory = await get_inventory_via_cm(
        refresh_token="<desktop/mobile refresh token>",
        account_name="<login name, not persona name>",
        appid=730,
        contextid=2,
    )

    print(f"{len(inventory)} items, complete={inventory.is_complete}")

    for item in inventory:
        print(item.amount, item.display_name)

    for item in inventory.held_items:
        print(item.display_name, "tradable after", item.tradable_after)

asyncio.run(main())
```

`Inventory` is iterable/indexable and comes with a handful of convenience
views: `.get(assetid)`, `.tradable_items`, `.marketable_items`,
`.held_items`, `.group_by_name()`, `.counts_by_name()`, `.is_complete`.
`InventoryItem` has `.display_name`, `.in_trade_hold()`,
`.time_until_tradable()`, `.tag_value(category)`.

Reuse one logged-on session for several calls (multiple apps, or an
inventory fetch plus `get_web_cookies`) without reconnecting in between:

```python
from steam_api import SteamCMClient
from steam_api.inventory import fetch_inventory

async with SteamCMClient() as client:
    await client.logon_with_refresh_token(refresh_token, steamid, account_name)
    cs2_inventory = await fetch_inventory(client, steamid, appid=730, contextid=2)
    dota_inventory = await fetch_inventory(client, steamid, appid=570, contextid=2)
```

More end-to-end examples in [`examples/`](examples/).

## Trade holds

Items currently on a trade hold are included by default - Steam otherwise
drops them from the response outright instead of flagging them, which looks
like missing items rather than a hold. Check `item.in_trade_hold()` /
`item.tradable_after` / `item.time_until_tradable()` / `item.sealed`
(resolved from several inconsistent signals Steam doesn't expose as one
clean field, see `trade_hold.py`); pass `include_trade_locked=False` / CLI
`--exclude-trade-locked` to mirror a plain (non-owner) inventory view instead.

**CS2 specifically** splits "Trade Protected" items (received via trade,
still within their 7-day protection period) into a completely separate
inventory context (16, not the normal 2) - they never appear in the context 2
response no matter what request flags are set. `fetch_inventory`/
`get_inventory_via_cm` fetch and merge context 16 in automatically for
`appid=730, contextid=2` requests (best-effort - a failure there doesn't fail
the main fetch), same as Steam's own trade-offer UI does. This is why an
inventory fetch that doesn't do this can appear to be missing roughly half a
CS2 account's items.

## CLI

```powershell
uv run steam-api inventory <refresh_token> <account_name> 730 2 --json --pretty
uv run steam-api inventory <refresh_token> <account_name> 730 2 --output inventory.json
uv run steam-api cookies <refresh_token> <account_name>
uv run steam-api extract-token <account_name>
```

`cookies` exchanges the token for `steamLoginSecure` web cookies instead of
fetching an inventory. `extract-token` is Windows-only: reads the refresh
token straight out of the local Steam client's storage.

## Architecture

CM protocol stack, bottom to top: `connection.py` (raw TCP + wire framing) ->
`crypto.py` (RSA/AES channel handshake) -> `framing.py` (app-message framing)
-> `cm_client.py` (`SteamCMClient`: connect, log on, generic Unified Messages
`call_service_method`) -> `inventory.py` (`Econ.GetInventoryItemsWithDescriptions`
pagination + CS2 context-16 merge + merging assets with their shared item
descriptions into `schemas.InventoryItem`) -> `trade_hold.py` (resolves the
free-text/timestamp/sealed signals into a hold end time).

`schemas.py` holds the public data model (`Inventory`, `InventoryItem`,
`SteamWebCookies`, ...); `exceptions.py` the error hierarchy
(`SteamApiError` and its subclasses); `token_claims.py`/`machine_id.py`/
`cm_server_list.py`/`eresult.py` are small supporting pieces used by the
stack above.

See `steam_api/proto.py` for the protoc invocation that generates `steam_api/pb2/`
from `protobufs/*.proto` (gitignored, local-only codegen input).

## Commands

```powershell
uv sync                               # install deps into .venv
uv sync --extra codegen               # + grpcio-tools, to regenerate pb2/
uv run pytest                         # unit tests (no network)
uvx ruff check .
uv run --with mypy mypy steam_api
```

`.pre-commit-config.yaml` runs the three commands above as `local`/`system`
hooks on every commit - `uv run pre-commit install` once to enable, or
`uv run pre-commit run --all-files` on demand.

`.github/workflows/ci.yml` runs the same three checks on every PR/push to
`main`. `.github/workflows/release.yml` is manual (`workflow_dispatch`): bumps
`pyproject.toml`/`uv.lock` to the given version, re-runs the checks as a gate,
commits, tags, builds, and publishes to PyPI via trusted publishing - needs a
PyPI *Trusted Publisher* configured for this repo/workflow/`pypi` environment
before the first release.
