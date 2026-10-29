"""One player's app: the SDK mesh, the table, and the page in the browser.

The page talks to this process over one WebSocket on loopback. The server
pushes the table's snapshot whenever it changes; the page sends commands
back. The laptop's own page listens only on 127.0.0.1; phones get a
separate server on the local network (see phones.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Iterator

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .mesh import Mesh
from .phones import Phones, build_phone_app, is_phone
from .table import Table

logger = logging.getLogger(__name__)

WEB = Path(__file__).parent / "web"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]")
MAX_COMMAND_CHARS = 16 * 1024


class Player:
    """Wires one mesh to one table and to the page's sockets."""

    def __init__(self, mesh: Mesh, home: Path, bot_delay: float = 1.2,
                 phone_port: int | None = None, lan_ip: str | None = None):
        self.mesh = mesh
        self.home = home
        self.bot_delay = bot_delay
        self.phone_port = phone_port
        self.lan_ip = lan_ip
        self.phones: Phones | None = None
        self.table: Table | None = None
        self.sockets: set[WebSocket] = set()
        self._dirty = asyncio.Event()
        self._pusher: asyncio.Task[None] | None = None

    async def start(self) -> None:
        await self.mesh.start()
        self.table = Table(self.mesh.address, self.mesh.name, self._send,
                           save_path=self.home / "table.json", bot_delay=self.bot_delay)
        self.table.on_change = self._dirty.set
        if self.phone_port:
            self.phones = Phones(self.table, self.home, self.phone_port, self.lan_ip, self.bot_delay)
        self.mesh.on_hello = self.table.peer_seen
        self.mesh.on_lost = self.table.peer_lost
        self.mesh.on_message = self.table.receive
        self._pusher = asyncio.create_task(self._push_loop())
        await self.table.resume()

    async def _send(self, address: str, message: dict[str, Any]) -> None:
        if is_phone(address):
            if self.phones:
                await self.phones.deliver(address, message)
        else:
            await self.mesh.send(address, message)

    def snapshot(self) -> dict[str, Any]:
        snap = self.table.snapshot()
        snap["device"] = "laptop"
        snap["phone_url"] = self.phones.url if self.phones else None
        return snap

    async def close(self) -> None:
        if self._pusher:
            self._pusher.cancel()
        if self.phones:
            await self.phones.close()
        await self.mesh.close()

    async def _push_loop(self) -> None:
        while True:
            await self._dirty.wait()
            self._dirty.clear()
            await self._push()
            await asyncio.sleep(0.05)  # coalesce bursts into one frame

    async def _push(self, only: WebSocket | None = None) -> None:
        frame = json.dumps(self.snapshot())
        for ws in [only] if only else list(self.sockets):
            try:
                await ws.send_text(frame)
            except Exception:
                self.sockets.discard(ws)

    async def socket(self, ws: WebSocket) -> None:
        if not _loopback(ws.headers.get("host")) or not _same_origin(ws.headers.get("origin"), ws.headers.get("host")):
            await ws.close(code=4403)
            return
        await ws.accept()
        self.sockets.add(ws)
        await self._push(ws)
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_COMMAND_CHARS:
                    continue
                try:
                    cmd = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(cmd, dict):
                    await self.table.command(cmd)
        except WebSocketDisconnect:
            pass
        finally:
            self.sockets.discard(ws)


def _loopback(host: str | None) -> bool:
    """A Host header naming loopback, which stops DNS rebinding."""
    if not host:
        return False
    name = host.rsplit(":", 1)[0] if not host.endswith("]") else host
    return name in LOOPBACK_HOSTS


def _same_origin(origin: str | None, host: str | None) -> bool:
    """Only our own page may open the socket; other sites may not drive it."""
    return origin in (f"http://{host}",)


class Static(StaticFiles):
    """Static files the browser revalidates every time, so a new version of
    the page never runs against a stale stylesheet or script."""

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def build_app(player: Player) -> Starlette:
    async def index(request: Any) -> Response:
        if not _loopback(request.headers.get("host")):
            return Response("Forbidden", status_code=403)
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    async def qr(request: Any) -> Response:
        if not _loopback(request.headers.get("host")) or not player.phones or not player.phones.url:
            return Response("Not found", status_code=404)
        return Response(player.phones.qr_svg(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    return Starlette(routes=[
        Route("/", index),
        Route("/qr.svg", qr),
        WebSocketRoute("/ws", player.socket),
        Mount("/static", Static(directory=WEB / "static")),
    ])


class Server(uvicorn.Server):
    """A uvicorn server that leaves signals to the caller, which stops it
    by setting ``should_exit``."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def make_server(player: Player, port: int) -> Server:
    config = uvicorn.Config(build_app(player), host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    return Server(config)


def make_phone_server(player: Player) -> Server:
    """The phone page, on every interface so phones on the Wi-Fi reach it."""
    app = build_phone_app(player.phones, lambda: Static(directory=WEB / "static"))
    config = uvicorn.Config(app, host="0.0.0.0", port=player.phones.port, log_level="warning", lifespan="off")
    return Server(config)
