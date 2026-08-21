"""Exception hierarchy for steam_api."""

from steam_api.eresult import eresult_name


class SteamApiError(Exception):
    """Base class for every error this library raises."""


class SteamConnectionError(SteamApiError):
    """Steam (a WebAPI endpoint or a CM server) could not be reached."""


class SteamProtocolError(SteamApiError):
    """Steam responded, but not in a way this client understands."""


class InvalidTokenError(SteamApiError, ValueError):
    """The provided string isn't a well-formed Steam refresh/access token."""


class SteamLogonError(SteamApiError):
    """Steam rejected a logon or RPC call with a specific EResult.

    Carries ``.eresult``/``.eresult_name`` so callers can inspect the specific
    reason (e.g. ``"InvalidPassword"``, ``"AccessDenied"``, ``"RateLimitExceeded"``)
    regardless of which CM call produced it (logon, GenerateAccessTokenForApp,
    GetInventoryItemsWithDescriptions, ...).
    """

    def __init__(self, eresult: int, action: str) -> None:
        self.eresult = eresult
        self.eresult_name = eresult_name(eresult)
        super().__init__(f"{action} failed: {self.eresult_name} ({eresult})")
