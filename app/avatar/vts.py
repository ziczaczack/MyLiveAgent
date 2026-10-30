"""VTube Studio WebSocket plugin API client: auth, token file, request/response matching."""
from __future__ import annotations

import asyncio
import itertools
from pathlib import Path
from typing import Callable, Protocol

API_NAME = "VTubeStudioPublicAPI"
API_VERSION = "1.0"
TOKEN_WAIT = 60.0  # the owner has to click Allow in VTube Studio


class VTSError(Exception):
    """API error or protocol failure."""


class Transport(Protocol):
    async def send(self, msg: dict) -> None: ...
    async def receive(self) -> dict: ...  # raises ConnectionError when closed
    async def close(self) -> None: ...


class _AiohttpTransport:
    def __init__(self, session, ws) -> None:
        self._session = session
        self._ws = ws

    async def send(self, msg: dict) -> None:
        try:
            await self._ws.send_json(msg)
        except Exception as e:
            raise ConnectionError(str(e)) from e

    async def receive(self) -> dict:
        try:
            return await self._ws.receive_json()
        except Exception as e:  # closed socket, bad frame, network error
            raise ConnectionError(str(e)) from e

    async def close(self) -> None:
        try:
            await self._ws.close()
        finally:
            await self._session.close()


async def aiohttp_connect(url: str) -> Transport:
    import aiohttp
    session = aiohttp.ClientSession()
    try:
        ws = await session.ws_connect(url)
    except BaseException:
        await session.close()
        raise
    return _AiohttpTransport(session, ws)


class VTSClient:
    def __init__(self, transport: Transport, plugin_name: str, plugin_developer: str,
                 token_path: Path, on_notice: Callable[[str], None]) -> None:
        self._transport = transport
        self._name = plugin_name
        self._developer = plugin_developer
        self._token_path = Path(token_path)
        self._on_notice = on_notice
        self._pending: dict[str, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._reader: asyncio.Task | None = None
        self._dead = False

    async def authenticate(self) -> None:
        token = self._read_token()
        if token and await self._try_token(token):
            return
        self._on_notice("VTube Studio: click Allow in the popup to let this plugin connect.")
        data = await self.request("AuthenticationToken", self._identity(), timeout=TOKEN_WAIT)
        token = data.get("authenticationToken")
        if not token:
            raise VTSError("no authentication token returned")
        if not await self._try_token(token):
            raise VTSError("authentication rejected")
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        self._token_path.write_text(token, encoding="utf-8")

    async def request(self, message_type: str, data: dict | None = None, timeout: float = 5.0) -> dict:
        if self._dead:
            raise VTSError("disconnected")
        if self._reader is None:
            self._reader = asyncio.create_task(self._read_loop())
        rid = f"r{next(self._ids)}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._transport.send({
                "apiName": API_NAME, "apiVersion": API_VERSION, "requestID": rid,
                "messageType": f"{message_type}Request", "data": data or {},
            })
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise VTSError(f"{message_type} timed out") from None
        except ConnectionError as e:
            self._fail_all()
            raise VTSError("disconnected") from e
        finally:
            self._pending.pop(rid, None)

    async def close(self) -> None:
        self._fail_all()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
        await self._transport.close()

    def _identity(self) -> dict:
        return {"pluginName": self._name, "pluginDeveloper": self._developer}

    def _read_token(self) -> str:
        try:
            return self._token_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    async def _try_token(self, token: str) -> bool:
        data = await self.request("Authentication", {**self._identity(), "authenticationToken": token})
        return bool(data.get("authenticated"))

    async def _read_loop(self) -> None:
        try:
            while True:
                msg = await self._transport.receive()
                fut = self._pending.get(msg.get("requestID"))
                if fut is None or fut.done():
                    continue
                data = msg.get("data") or {}
                if msg.get("messageType") == "APIError":
                    fut.set_exception(VTSError(f"APIError {data.get('errorID')}: {data.get('message')}"))
                else:
                    fut.set_result(data)
        except ConnectionError:
            self._fail_all()

    def _fail_all(self) -> None:
        self._dead = True
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(VTSError("disconnected"))
