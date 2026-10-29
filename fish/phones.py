"""Phones at the table: players who join the laptop's game from a browser.

A phone cannot run the SDK, so it joins the laptop instead. The laptop
serves a phone page on the local network; the QR code it shows carries a
join key, so only people who can see the laptop's screen can join.

Inside the laptop's app each phone is a guest with a Table of its own,
wired straight to the laptop's Table instead of over the mesh. So a phone
is a player like any other: it joins, sits, and gets only its own view
from the host, and a phone whose screen locks shows as away until it
comes back.

The link between phone and laptop is plain HTTP on the local network,
not the SDK's encryption.
"""

from __future__ import annotations

import asyncio
import hmac
import io
import json
import logging
import secrets
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import segno
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from .mesh import clean_name
from .table import Table

logger = logging.getLogger(__name__)

PREFIX = "phone:"
MAX_GUESTS = 24
MAX_COMMAND_CHARS = 16 * 1024
# What a phone may ask of its own table: never to host or run a room.
PHONE_COMMANDS = {"join", "leave", "sit", "act"}


def is_phone(address: str | None) -> bool:
    return bool(address) and address.startswith(PREFIX)


def lan_ip() -> str | None:
    """This machine's address on the local network, if it has one."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.254.254.254", 1))  # picks a route; sends nothing
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                return ip
        except OSError:
            pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return None


@dataclass
class Guest:
    id: str
    name: str
    secret: str
    table: Table | None = None
    sockets: set[WebSocket] = field(default_factory=set)
    dirty: asyncio.Event = field(default_factory=asyncio.Event)
    pusher: asyncio.Task[None] | None = None

    @property
    def address(self) -> str:
        return PREFIX + self.id


class Phones:
    def __init__(self, host: Table, home: Path, port: int, ip: str | None, bot_delay: float):
        self.host = host
        self.path = home / "phones.json"
        self.port = port
        self.ip = ip
        self.bot_delay = bot_delay
        self.guests: dict[str, Guest] = {}
        self.key = secrets.token_urlsafe(9)
        self._load()

    # ------------------------------------------------------------- the key

    @property
    def url(self) -> str | None:
        return f"http://{self.ip}:{self.port}/?k={self.key}" if self.ip else None

    def qr_svg(self) -> bytes:
        out = io.BytesIO()
        segno.make(self.url or "", error="m").save(out, kind="svg", scale=8, border=2,
                                                   dark="#0f2a3a", light="#ffffff")
        return out.getvalue()

    def key_ok(self, key: Any) -> bool:
        return isinstance(key, str) and hmac.compare_digest(key, self.key)

    # ------------------------------------------------------------- guests

    def _load(self) -> None:
        """Guests survive a restart, so phones reconnect to their seats."""
        if not self.path.exists():
            self._save()
            return
        try:
            data = json.loads(self.path.read_text())
            self.key = data["key"]
            for g in data["guests"]:
                self.guests[g["id"]] = Guest(g["id"], g["name"], g["secret"])
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("ignoring phones.json that does not load: %s", exc)

    def _save(self) -> None:
        data = {"key": self.key,
                "guests": [{"id": g.id, "name": g.name, "secret": g.secret} for g in self.guests.values()]}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.path)

    def add(self, name: str) -> Guest:
        if len(self.guests) >= MAX_GUESTS:
            # Make room by forgetting phones that hold no place in the room.
            room = self.host.room
            for gid, g in list(self.guests.items()):
                if not g.sockets and not (room and g.address in room.names):
                    del self.guests[gid]
            if len(self.guests) >= MAX_GUESTS:
                raise ValueError("This table has too many phones already")
        guest = Guest(secrets.token_hex(4), name, secrets.token_urlsafe(18))
        self.guests[guest.id] = guest
        self._save()
        return guest

    def find(self, gid: Any, secret: Any) -> Guest | None:
        guest = self.guests.get(gid) if isinstance(gid, str) else None
        if guest and isinstance(secret, str) and hmac.compare_digest(secret, guest.secret):
            return guest
        return None

    def _table(self, guest: Guest) -> Table:
        if guest.table is None:
            async def send(to: str, message: dict[str, Any], sender: str = guest.address) -> None:
                if to == self.host.address:
                    await self.host.receive(sender, message)

            guest.table = Table(guest.address, guest.name, send, bot_delay=self.bot_delay)
            guest.table.on_change = guest.dirty.set
            guest.pusher = asyncio.create_task(self._push_loop(guest))
        return guest.table

    async def deliver(self, address: str, message: dict[str, Any]) -> None:
        """The host table sends to a phone: straight into that phone's table."""
        guest = self.guests.get(address[len(PREFIX):])
        if guest and guest.table and guest.sockets:
            await guest.table.receive(self.host.address, message)

    async def close(self) -> None:
        for guest in self.guests.values():
            if guest.pusher:
                guest.pusher.cancel()

    # ------------------------------------------------------------ sockets

    def snapshot(self, guest: Guest) -> dict[str, Any]:
        snap = guest.table.snapshot()
        snap["device"] = "phone"
        snap["host_name"] = self.host.name
        snap["hosting"] = False
        return snap

    async def _push_loop(self, guest: Guest) -> None:
        while True:
            await guest.dirty.wait()
            guest.dirty.clear()
            await self._push(guest)
            await asyncio.sleep(0.05)

    async def _push(self, guest: Guest, only: WebSocket | None = None) -> None:
        frame = json.dumps(self.snapshot(guest))
        for ws in [only] if only else list(guest.sockets):
            try:
                await ws.send_text(frame)
            except Exception:
                guest.sockets.discard(ws)

    async def socket(self, ws: WebSocket) -> None:
        guest = self.find(ws.query_params.get("id"), ws.query_params.get("s"))
        if guest is None or ws.headers.get("origin") != f"http://{ws.headers.get('host')}":
            await ws.close(code=4403)
            return
        await ws.accept()
        table = self._table(guest)
        first = not guest.sockets
        guest.sockets.add(ws)
        if first:
            # The phone is here: each side hears from the other as over the mesh.
            await table.peer_seen(self.host.address, self.host.name)
            await self.host.peer_seen(guest.address, guest.name)
            room = self.host.room
            if room and table.joined is None and not room.started:
                await table.command({"cmd": "join", "host": self.host.address})
        await self._push(guest, ws)
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_COMMAND_CHARS:
                    continue
                try:
                    cmd = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(cmd, dict) and cmd.get("cmd") in PHONE_COMMANDS:
                    await table.command(cmd)
        except WebSocketDisconnect:
            pass
        finally:
            guest.sockets.discard(ws)
            if not guest.sockets:
                await self.host.peer_lost(guest.address)


WEB = Path(__file__).parent / "web"


def build_phone_app(phones: Phones, static: Callable[[], Any]) -> Starlette:
    async def page(request: Request) -> Response:
        return FileResponse(WEB / "phone.html", headers={"Cache-Control": "no-store"})

    async def join(request: Request) -> Response:
        if request.headers.get("origin") not in (None, f"http://{request.headers.get('host')}"):
            return JSONResponse({"error": "Open the page from the QR code"}, status_code=403)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not phones.key_ok(body.get("k")):
            return JSONResponse({"error": "This link is out of date. Scan the QR code on the laptop again."},
                                status_code=403)
        name = clean_name(body.get("name"))
        if not name:
            return JSONResponse({"error": "Type a name first"}, status_code=400)
        try:
            guest = phones.add(name)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse({"id": guest.id, "secret": guest.secret})

    return Starlette(routes=[
        Route("/", page),
        Route("/join", join, methods=["POST"]),
        WebSocketRoute("/ws", phones.socket),
        Mount("/static", static()),
    ])
