"""Пример: обменять refresh token на веб-куки (steamLoginSecure) через CM.

Заполни константы ниже своими данными и запусти:

    uv run python examples/fetch_web_cookies.py
"""

import asyncio

from steam_api import SteamApiError, get_web_cookies_via_cm

REFRESH_TOKEN = ""
ACCOUNT_NAME = ""


async def main() -> None:
    if not REFRESH_TOKEN or not ACCOUNT_NAME:
        raise SystemExit("Заполни REFRESH_TOKEN и ACCOUNT_NAME выше")

    try:
        cookies = await get_web_cookies_via_cm(REFRESH_TOKEN, ACCOUNT_NAME)
    except SteamApiError as exc:
        print(f"Ошибка: {exc}")
        return

    print(f"steamid:          {cookies.steamid}")
    print(f"steamLoginSecure: {cookies.steam_login_secure}")
    print(f"sessionid:        {cookies.session_id}")
    print(f"cookie dict:      {cookies.as_dict()}")


if __name__ == "__main__":
    asyncio.run(main())
