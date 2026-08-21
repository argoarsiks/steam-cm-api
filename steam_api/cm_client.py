"""A Steam CM (Connection Manager) client: enough to log on with a
desktop/mobile refresh token and then make authenticated Unified Messages
service calls over the encrypted channel - ``Authentication.GenerateAccessTokenForApp``
(web session cookies) and ``Econ.GetInventoryItemsWithDescriptions`` (inventory,
see ``inventory.py``), or any other service call via :meth:`call_service_method`.

This is the CM-native way to reach these RPCs, the same one the official Steam
client uses - as opposed to the public web endpoints (``/inventory/`` JSON,
``IEconService``), which are cookie/session-based, more aggressively
rate-limited, and in the inventory case openly discouraged by Valve in favor
of this exact CM call.

One session, many calls: unlike a strictly one-shot client, :class:`SteamCMClient`
stays connected and logged on across multiple RPCs - log on once, then call
:meth:`generate_access_token`, :meth:`call_service_method` (used by
``inventory.fetch_inventory`` for pagination), or any combination, before
:meth:`logoff`. There's still no heartbeat loop or reconnect logic - if the
session drops, construct a new client and log on again.
"""

import gzip
import itertools
import secrets
import socket
import struct
import time
import zlib
from dataclasses import dataclass

from steam_api import crypto, framing, structs
from steam_api import emsg as emsg_mod
from steam_api.cm_server_list import fetch_cm_servers
from steam_api.connection import CMConnection
from steam_api.exceptions import (
    SteamApiError,
    SteamConnectionError,
    SteamLogonError,
    SteamProtocolError,
)
from steam_api.machine_id import build_machine_id
from steam_api.proto import (
    CAuthentication_AccessToken_GenerateForApp_Request,
    CAuthentication_AccessToken_GenerateForApp_Response,
    CMsgClientLogOff,
    CMsgClientLogon,
    CMsgClientLogonResponse,
    CMsgMulti,
    CMsgProtoBufHeader,
)
from steam_api.schemas import SteamWebCookies
from steam_api.token_claims import get_steamid_from_token

PROTOCOL_VERSION = 65581
CLIENT_PACKAGE_VERSION = 1771
EOS_TYPE_WIN10 = 16
TARGET_JOB_GENERATE_ACCESS_TOKEN = "Authentication.GenerateAccessTokenForApp#1"

_EUNIVERSE_PUBLIC = 1
_EACCOUNT_TYPE_INDIVIDUAL = 1
_STEAMID_INSTANCE_DESKTOP = 1
_PLACEHOLDER_HEADER_STEAMID = (
    (_EUNIVERSE_PUBLIC << 56)
    | (_EACCOUNT_TYPE_INDIVIDUAL << 52)
    | (_STEAMID_INSTANCE_DESKTOP << 32)
)

_job_id_seq = itertools.count(1)


def _next_job_id() -> int:
    return (int(time.time() * 1000) << 20) | (next(_job_id_seq) & 0xFFFFF)


@dataclass
class _ParsedMessage:
    emsg: int
    header: CMsgProtoBufHeader
    body: bytes


class SteamCMClient:
    """A logged-on CM session: connect -> secure channel -> log on -> any
    number of RPCs -> log off.

    Construct it, use it as an async context manager (which connects and
    secures the channel), log on, then call whatever RPCs you need::

        async with SteamCMClient() as client:
            await client.logon_with_refresh_token(refresh_token, steamid, account_name)
            cookies = await client.get_web_cookies(refresh_token, account_name)  # or:
            inventory = await fetch_inventory(client, steamid, appid=730, contextid=2)

    The lower-level primitives (:meth:`connect_any`, :meth:`logon_with_refresh_token`,
    :meth:`call_service_method`, :meth:`logoff`) stay available directly for callers
    that want more control, e.g. supplying their own CM server candidates or making
    an arbitrary Unified Messages service call.
    """

    def __init__(
        self,
        connect_timeout: float = 10.0,
        message_timeout: float = 15.0,
        max_server_attempts: int = 5,
    ) -> None:
        self._connect_timeout = connect_timeout
        self._message_timeout = message_timeout
        self._max_server_attempts = max_server_attempts
        self._conn = CMConnection()
        self._channel_key: bytes | None = None
        self._channel_hmac: bytes | None = None
        self._pending: list[_ParsedMessage] = []
        self._session_id: int | None = None

    async def __aenter__(self) -> "SteamCMClient":
        candidates = await fetch_cm_servers()
        await self.connect_any(candidates[: self._max_server_attempts])
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    async def connect_any(self, candidates: list[tuple[str, int]]) -> tuple[str, int]:
        """Try each ``(host, port)`` candidate until one connects and completes
        the channel-encryption handshake.

        :raises SteamConnectionError: none of the candidates worked.
        """
        last_error: Exception | None = None

        for host, port in candidates:
            try:
                await self._conn.connect(host, port, timeout=self._connect_timeout)
                await self._secure_channel()
                return host, port
            except (OSError, SteamConnectionError, TimeoutError) as exc:
                last_error = exc
                await self._conn.close()

        detail = f"{type(last_error).__name__}: {last_error}" if last_error else "no candidates"
        raise SteamConnectionError(f"Could not connect to any CM server: {detail}")

    async def close(self) -> None:
        await self._conn.close()


    async def _secure_channel(self) -> None:
        raw = await self._conn.recv(timeout=self._connect_timeout)
        header = structs.RawMsgHeader.load(raw)
        emsg = emsg_mod.clear_proto_bit(header.msg)

        if emsg != emsg_mod.EMSG_CHANNEL_ENCRYPT_REQUEST:
            raise SteamProtocolError(f"Expected ChannelEncryptRequest, got emsg={emsg}")

        req = structs.ChannelEncryptRequest.load(raw[structs.RawMsgHeader.SIZE :])

        key, encrypted_key = crypto.generate_session_key(req.challenge)
        crc = zlib.crc32(encrypted_key) & 0xFFFFFFFF

        resp = structs.ChannelEncryptResponse(key=encrypted_key, crc=crc)
        resp_header = structs.RawMsgHeader(msg=emsg_mod.EMSG_CHANNEL_ENCRYPT_RESPONSE)
        await self._conn.send(resp_header.serialize() + resp.serialize())

        raw_result = await self._conn.recv(timeout=self._connect_timeout)
        result_header = structs.RawMsgHeader.load(raw_result)
        result_emsg = emsg_mod.clear_proto_bit(result_header.msg)

        if result_emsg != emsg_mod.EMSG_CHANNEL_ENCRYPT_RESULT:
            raise SteamProtocolError(f"Expected ChannelEncryptResult, got emsg={result_emsg}")

        result = structs.ChannelEncryptResult.load(raw_result[structs.RawMsgHeader.SIZE :])
        if result.eresult != 1:
            raise SteamLogonError(result.eresult, "Channel encryption")

        self._channel_key = key
        self._channel_hmac = key[:16] if req.challenge else None


    async def _send_proto(self, emsg: int, header: CMsgProtoBufHeader, body: bytes) -> None:
        if self._channel_key is None:
            raise SteamProtocolError("Cannot send before the channel is secured")

        if self._session_id is not None:
            header.client_sessionid = self._session_id

        payload = framing.serialize_proto_message(emsg, header, body)

        if self._channel_hmac:
            encrypted = crypto.symmetric_encrypt_hmac(
                payload, self._channel_key, self._channel_hmac
            )
        else:
            encrypted = crypto.symmetric_encrypt(payload, self._channel_key)

        await self._conn.send(encrypted)

    async def _recv_proto(self) -> _ParsedMessage:
        if self._pending:
            return self._pending.pop(0)

        if self._channel_key is None:
            raise SteamProtocolError("Cannot receive before the channel is secured")

        while True:
            raw = await self._conn.recv(timeout=self._message_timeout)

            try:
                if self._channel_hmac:
                    decrypted = crypto.symmetric_decrypt_hmac(
                        raw, self._channel_key, self._channel_hmac
                    )
                else:
                    decrypted = crypto.symmetric_decrypt(raw, self._channel_key)
            except ValueError as exc:
                raise SteamProtocolError(f"Failed to decrypt message: {exc}") from exc

            emsg, header, body = framing.parse_proto_message(decrypted)

            if emsg == emsg_mod.EMSG_MULTI:
                self._unpack_multi(body)
                if self._pending:
                    return self._pending.pop(0)
                continue

            return _ParsedMessage(emsg=emsg, header=header, body=body)

    def _unpack_multi(self, body: bytes) -> None:
        multi = CMsgMulti()
        multi.ParseFromString(body)

        data = multi.message_body
        if multi.size_unzipped:
            data = gzip.decompress(data)

        offset = 0
        while offset + 4 <= len(data):
            (size,) = struct.unpack_from("<I", data, offset)
            offset += 4

            if size < 0 or offset + size > len(data):
                break

            chunk = data[offset : offset + size]
            offset += size

            try:
                sub_emsg, sub_header, sub_body = framing.parse_proto_message(chunk)
            except SteamProtocolError:
                continue

            self._pending.append(_ParsedMessage(emsg=sub_emsg, header=sub_header, body=sub_body))


    async def logon_with_refresh_token(
        self, refresh_token: str, steamid: int, account_name: str
    ) -> None:
        """:raises SteamLogonError: Steam rejected the logon."""
        logon = CMsgClientLogon()
        logon.protocol_version = PROTOCOL_VERSION
        logon.client_os_type = EOS_TYPE_WIN10
        logon.client_language = "english"
        logon.cell_id = 0
        logon.client_package_version = CLIENT_PACKAGE_VERSION
        logon.should_remember_password = True
        logon.supports_rate_limit_response = True
        logon.chat_mode = 2
        logon.obfuscated_private_ip.v4 = 0
        logon.account_name = account_name
        logon.access_token = refresh_token
        logon.machine_name = socket.gethostname()
        seed = f"steam_api-{steamid}"
        logon.machine_id = build_machine_id(f"{seed}-bb3", f"{seed}-ff2", f"{seed}-3b3")

        header = CMsgProtoBufHeader()
        header.steamid = _PLACEHOLDER_HEADER_STEAMID

        await self._send_proto(emsg_mod.EMSG_CLIENT_LOGON, header, logon.SerializeToString())

        message = await self._wait_for(emsg_mod.EMSG_CLIENT_LOGON_RESPONSE)

        response = CMsgClientLogonResponse()
        response.ParseFromString(message.body)

        if response.eresult != 1:
            raise SteamLogonError(response.eresult, "Logon")

        self._session_id = message.header.client_sessionid

    async def _wait_for(self, wanted_emsg: int, max_messages: int = 20) -> _ParsedMessage:
        for _ in range(max_messages):
            message = await self._recv_proto()
            if message.emsg == wanted_emsg:
                return message
        raise SteamProtocolError(f"Timed out waiting for emsg={wanted_emsg}")


    async def call_service_method(
        self, target_job_name: str, steamid: int, request_body: bytes
    ) -> bytes:
        """Call any Steam Unified Messages service RPC (``Interface.Method#version``,
        e.g. ``"Econ.GetInventoryItemsWithDescriptions#1"``) over this session's
        authenticated channel, and return the raw serialized response bytes.

        Requires an active logon (see :meth:`logon_with_refresh_token`) - the
        response header's ``eresult`` (checked here) is only meaningful once the
        server has an identity for this connection.

        :raises SteamLogonError: Steam rejected the call (non-OK eresult).
        :raises SteamProtocolError: no matching reply arrived before the wait gave up.
        """
        header = CMsgProtoBufHeader()
        header.steamid = steamid
        header.target_job_name = target_job_name
        job_id = _next_job_id()
        header.jobid_source = job_id

        await self._send_proto(
            emsg_mod.EMSG_SERVICE_METHOD_CALL_FROM_CLIENT, header, request_body
        )

        message = await self._wait_for_job(job_id, target_job_name)

        if message.header.eresult and message.header.eresult != 1:
            raise SteamLogonError(message.header.eresult, target_job_name)

        return message.body

    async def _wait_for_job(
        self, job_id: int, target_job_name: str, max_messages: int = 20
    ) -> _ParsedMessage:
        for _ in range(max_messages):
            message = await self._recv_proto()
            if (
                message.emsg == emsg_mod.EMSG_SERVICE_METHOD_RESPONSE
                and message.header.jobid_target == job_id
            ):
                return message
        raise SteamProtocolError(f"Timed out waiting for {target_job_name} response")

    async def generate_access_token(self, refresh_token: str, steamid: int) -> str:
        """Call ``Authentication.GenerateAccessTokenForApp`` over the CM channel.

        :raises SteamLogonError: Steam rejected the call.
        """
        request = CAuthentication_AccessToken_GenerateForApp_Request()
        request.refresh_token = refresh_token
        request.steamid = steamid

        body = await self.call_service_method(
            TARGET_JOB_GENERATE_ACCESS_TOKEN, steamid, request.SerializeToString()
        )

        response = CAuthentication_AccessToken_GenerateForApp_Response()
        response.ParseFromString(body)

        if not response.access_token:
            raise SteamProtocolError("GenerateAccessTokenForApp returned an empty response")

        return str(response.access_token)

    async def logoff(self) -> None:
        try:
            header = CMsgProtoBufHeader()
            if self._session_id is not None:
                header.client_sessionid = self._session_id
            await self._send_proto(
                emsg_mod.EMSG_CLIENT_LOG_OFF, header, CMsgClientLogOff().SerializeToString()
            )
        except SteamApiError:
            pass


    async def get_web_cookies(self, refresh_token: str, account_name: str) -> SteamWebCookies:
        """Log on with ``refresh_token`` and exchange it for web session cookies,
        over a channel this client has already connected and secured (see
        :meth:`__aenter__`/:meth:`connect_any`).

        :param account_name: the account's login name (not persona/display name -
            e.g. the ``AccountName`` field in Steam's own ``loginusers.vdf``).
        :raises SteamLogonError: Steam rejected the logon or the RPC call.
        :raises SteamProtocolError: an unexpected/malformed response was received.
        """
        steamid = get_steamid_from_token(refresh_token)
        await self.logon_with_refresh_token(refresh_token, steamid, account_name)
        access_token = await self.generate_access_token(refresh_token, steamid)
        await self.logoff()

        return SteamWebCookies(
            steamid=steamid,
            steam_login_secure=f"{steamid}||{access_token}",
            session_id=secrets.token_hex(12),
        )


async def get_web_cookies_via_cm(
    refresh_token: str,
    account_name: str,
    connect_timeout: float = 10.0,
    message_timeout: float = 15.0,
    max_server_attempts: int = 5,
) -> SteamWebCookies:
    """Full CM-native flow: connect to a CM server, log on with the refresh
    token, call ``Authentication.GenerateAccessTokenForApp`` over the
    authenticated channel, and build the web session cookies from the result.

    :param account_name: the account's login name (not persona/display name -
        e.g. the ``AccountName`` field in Steam's own ``loginusers.vdf``).
        Required by CM even for token-based logon.
    :raises SteamConnectionError: no CM server could be reached.
    :raises SteamLogonError: Steam rejected the logon or the RPC call.
    :raises SteamProtocolError: an unexpected/malformed response was received.
    """
    async with SteamCMClient(
        connect_timeout=connect_timeout,
        message_timeout=message_timeout,
        max_server_attempts=max_server_attempts,
    ) as client:
        return await client.get_web_cookies(refresh_token, account_name)
