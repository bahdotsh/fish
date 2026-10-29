"""The table: lobby, seats and the game, as messages between players.

Every player runs one Table. One of them hosts: the host owns the only
real Game, applies every move and sends each seated player their own
`Game.view`. Nobody else ever holds another player's cards.

The Table knows nothing about the network. It sends with an async
``send(address, message)`` and is fed with ``receive(sender, message)``,
so tests can wire tables together in memory and the SDK mesh can carry
them between machines.

Messages (``t``):

  room    host -> everyone nearby   the room it hosts, or null once closed
  join    player -> host            take a place in the room
  leave   player -> host
  sit     player -> host            {seat}: a seat number, or null to stand
  lobby   host -> members           the room in full
  state   host -> seated players    {seq, view}: that player's own view
  act     player -> host            {action: ask | declare | pass, ...}
  error   host -> player            {message}: a refused request
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import secrets
from pathlib import Path
from typing import Any, Awaitable, Callable

from .bot import Bot
from .cards import SET_NAMES, SETS
from .engine import PLAYER_COUNTS, Game, IllegalMove, team_of

logger = logging.getLogger(__name__)

Send = Callable[[str, dict[str, Any]], Awaitable[None]]

BOT_PREFIX = "bot:"
BOT_NAMES = ("Ash", "Birch", "Cedar", "Elm", "Fir", "Hazel", "Maple", "Oak")


def is_bot(address: str | None) -> bool:
    return bool(address) and address.startswith(BOT_PREFIX)


class TableError(Exception):
    """A request the table refuses. The message is shown to the player."""


class Room:
    """The host's own record of the room it runs."""

    def __init__(self, host: str, size: int, room_id: str | None = None):
        self.id = room_id or secrets.token_hex(4)
        self.host = host
        self.size = size
        self.names: dict[str, str] = {}  # every member, host first
        self.seats: list[str | None] = [None] * size
        self.game: Game | None = None
        self.seq = 0

    @property
    def started(self) -> bool:
        return self.game is not None

    def seat_of(self, address: str) -> int | None:
        return self.seats.index(address) if address in self.seats else None

    def lobby(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "host": self.host,
            "size": self.size,
            "members": [{"address": a, "name": n} for a, n in self.names.items()],
            "seats": [{"address": a, "name": self.names.get(a)} if a else None for a in self.seats],
            "started": self.started,
        }

    def advert(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "host": self.host,
            "host_name": self.names.get(self.host),
            "size": self.size,
            "seated": sum(1 for a in self.seats if a),
            "started": self.started,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "host": self.host, "size": self.size, "names": self.names,
            "seats": self.seats, "seq": self.seq, "game": self.game.to_dict() if self.game else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Room:
        room = cls(data["host"], data["size"], data["id"])
        room.names = dict(data["names"])
        room.seats = list(data["seats"])
        room.seq = data["seq"]
        room.game = Game.from_dict(data["game"]) if data["game"] else None
        return room


class Table:
    def __init__(self, address: str, name: str, send: Send, save_path: Path | None = None,
                 bot_delay: float = 1.2, rng: random.Random | None = None):
        self.address = address
        self.name = name
        self._send = send
        self.save_path = save_path
        self.bot_delay = bot_delay
        self.rng = rng or random.Random()
        self.on_change: Callable[[], None] | None = None

        self.peers: dict[str, str] = {}  # address -> name, players nearby
        self.rooms: dict[str, dict[str, Any]] = {}  # host address -> advert
        self.room: Room | None = None  # set when this player hosts
        self.joined: str | None = None  # the host of the room we are in
        self.lobby: dict[str, Any] | None = None
        self.view: dict[str, Any] | None = None
        self.seq = -1
        self.error: str | None = None
        self.bots: dict[int, Bot] = {}
        self._bot_task: asyncio.Task[None] | None = None
        self._advert: dict[str, Any] | None = None  # the last one sent
        self._load()

    # ---------------------------------------------------------------- output

    def snapshot(self) -> dict[str, Any]:
        """Everything this player's screen shows."""
        return {
            "me": {"address": self.address, "name": self.name},
            "peers": [{"address": a, "name": n} for a, n in sorted(self.peers.items(), key=lambda p: p[1].lower())],
            "rooms": sorted(self.rooms.values(), key=lambda r: (r["started"], r["host_name"] or "")),
            "hosting": self.room is not None,
            "joined": self.joined,
            "lobby": self.lobby,
            "game": self.view,
            "error": self.error,
            "sets": SETS,
            "set_names": SET_NAMES,
        }

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    async def _post(self, address: str, message: dict[str, Any]) -> None:
        if is_bot(address):
            return
        try:
            await self._send(address, message)
        except Exception as exc:  # a peer out of range must not break the table
            logger.info("could not send %s to %s: %s", message.get("t"), address[:12], exc)

    # ------------------------------------------------------------- the mesh

    async def peer_seen(self, address: str, name: str) -> None:
        """A player nearby said hello (again): tell them where things stand."""
        self.peers[address] = name
        if self.room:
            room = self.room
            await self._post(address, {"t": "room", "room": room.advert()})
            if address in room.names:
                room.names[address] = name
                await self._post(address, {"t": "lobby", "lobby": room.lobby()})
                await self._send_state(address)
        self._changed()

    def peer_lost(self, address: str) -> None:
        self.peers.pop(address, None)
        self._changed()

    async def receive(self, sender: str, message: dict[str, Any]) -> None:
        kind = message.get("t")
        try:
            if kind == "room":
                self._on_room(sender, message.get("room"))
            elif kind == "lobby":
                self._on_lobby(sender, message.get("lobby"))
            elif kind == "state":
                self._on_state(sender, message)
            elif kind == "error":
                if sender == self.joined:
                    self.error = str(message.get("message", ""))[:200]
                    if self.lobby is None:
                        self.joined = None  # the host turned our join down
            elif kind in ("join", "leave", "sit", "act"):
                await self._host_request(sender, kind, message)
            else:
                return
        except TableError as exc:
            await self._post(sender, {"t": "error", "message": str(exc)})
        self._changed()

    # ------------------------------------------------------ player commands

    async def command(self, cmd: dict[str, Any]) -> None:
        """A command from this player's own screen."""
        self.error = None
        try:
            await self._command(cmd)
        except (TableError, IllegalMove) as exc:
            self.error = str(exc)
        self._changed()

    async def _command(self, cmd: dict[str, Any]) -> None:
        what = cmd.get("cmd")
        if what == "host":
            await self.host(int(cmd.get("size", 6)))
        elif what == "close":
            await self.close_room()
        elif what == "join":
            await self.join(str(cmd.get("host", "")))
        elif what == "leave":
            await self.leave()
        elif what == "add_bot":
            await self._host_add_bot(cmd.get("seat"))
        elif what == "remove_bot":
            await self._host_remove_bot(cmd.get("seat"))
        elif what == "start":
            await self._host_start()
        elif what in ("sit", "act"):
            if not self.joined:
                raise TableError("Join a room first")
            await self._request(what, {k: v for k, v in cmd.items() if k != "cmd"})
        else:
            raise TableError(f"Unknown command {what!r}")

    async def _request(self, kind: str, body: dict[str, Any]) -> None:
        message = {"t": kind, **body}
        if self.joined == self.address:
            await self._host_request(self.address, kind, message)
        else:
            await self._post(self.joined, message)

    async def host(self, size: int) -> None:
        if size not in PLAYER_COUNTS:
            raise TableError("A room has 6 or 8 seats")
        if self.joined:
            raise TableError("Leave your room first")
        self.room = Room(self.address, size)
        self.room.names[self.address] = self.name
        self.joined = self.address
        await self._host_broadcast()

    async def close_room(self) -> None:
        room = self.room
        if not room:
            raise TableError("You are not hosting a room")
        self.room = None
        self._advert = None
        self.bots.clear()
        self.joined, self.lobby, self.view, self.seq = None, None, None, -1
        self._save()
        for address in self.peers:
            await self._post(address, {"t": "room", "room": None, "id": room.id})

    async def join(self, host: str) -> None:
        if self.joined:
            raise TableError("Leave your room first")
        if host not in self.rooms:
            raise TableError("That room is gone")
        self.joined = host
        await self._post(host, {"t": "join"})

    async def leave(self) -> None:
        if self.room:
            await self.close_room()
            return
        if self.view and not self.view["over"]:
            raise TableError("You can't leave a game in progress")
        if self.joined:
            await self._post(self.joined, {"t": "leave"})
        self.joined, self.lobby, self.view, self.seq = None, None, None, -1

    # --------------------------------------------------- what players hear

    def _on_room(self, sender: str, advert: Any) -> None:
        if advert is None:
            self.rooms.pop(sender, None)
            if self.joined == sender:
                self.joined, self.lobby, self.view, self.seq = None, None, None, -1
                self.error = "The host closed the room"
            return
        if isinstance(advert, dict) and advert.get("host") == sender:
            self.rooms[sender] = advert

    def _on_lobby(self, sender: str, lobby: Any) -> None:
        if not isinstance(lobby, dict) or lobby.get("host") != sender:
            return
        members = {m.get("address") for m in lobby.get("members", []) if isinstance(m, dict)}
        if self.address not in members:
            if self.joined == sender:
                self.joined, self.lobby, self.view, self.seq = None, None, None, -1
            return
        if self.joined not in (None, sender):
            return  # we are in someone else's room
        # A host that still lists us (after we restarted, say) puts us back.
        self.joined = sender
        self.lobby = lobby
        if not lobby.get("started"):
            self.view, self.seq = None, -1

    def _on_state(self, sender: str, message: dict[str, Any]) -> None:
        if sender != self.joined or not isinstance(message.get("view"), dict):
            return
        seq = message.get("seq")
        if not isinstance(seq, int) or seq <= self.seq:
            return  # an older state that arrived late
        self.seq = seq
        self.view = message["view"]

    # ------------------------------------------------------------ the host

    async def _host_request(self, sender: str, kind: str, message: dict[str, Any]) -> None:
        room = self.room
        if room is None:
            return
        if kind == "join":
            if room.started and sender not in room.names:
                raise TableError("That game has already started")
            room.names[sender] = self.peers.get(sender, sender[:10])
        elif kind == "leave":
            if sender == room.host or sender not in room.names:
                return
            seat = room.seat_of(sender)
            if room.started and seat is not None:
                return  # a seated player stays in a running game; they can come back
            if seat is not None:
                room.seats[seat] = None
            del room.names[sender]
        elif kind == "sit":
            self._host_sit(sender, message.get("seat"))
        elif kind == "act":
            self._host_act(sender, message)
        await self._host_broadcast(also=[sender] if kind == "leave" else [])

    def _host_sit(self, sender: str, seat: Any) -> None:
        room = self.room
        if sender not in room.names:
            raise TableError("Join the room first")
        if room.started:
            raise TableError("The game has started")
        current = room.seat_of(sender)
        if seat is None:
            if current is not None:
                room.seats[current] = None
            return
        if not isinstance(seat, int) or not 0 <= seat < room.size:
            raise TableError("No such seat")
        if room.seats[seat] is not None:
            raise TableError("That seat is taken")
        if current is not None:
            room.seats[current] = None
        room.seats[seat] = sender

    def _host_act(self, sender: str, message: dict[str, Any]) -> None:
        room = self.room
        game = room.game
        if game is None:
            raise TableError("The game has not started")
        seat = room.seat_of(sender)
        if seat is None:
            raise TableError("You are not seated in this game")
        self._apply(game, seat, message)

    @staticmethod
    def _apply(game: Game, seat: int, action: dict[str, Any]) -> None:
        what = action.get("action")
        try:
            if what == "ask":
                game.ask(seat, action.get("target"), str(action.get("card")))
            elif what == "declare":
                assignment = action.get("assignment")
                if not isinstance(assignment, dict):
                    raise IllegalMove("A declaration names a holder for each card")
                game.declare(seat, {str(c): p for c, p in assignment.items()})
            elif what == "pass":
                game.pass_turn(seat, action.get("to"))
            else:
                raise IllegalMove(f"Unknown action {what!r}")
        except IllegalMove as exc:
            raise TableError(str(exc)) from exc

    async def _host_add_bot(self, seat: Any) -> None:
        room = self._own_room()
        if room.started:
            raise TableError("The game has started")
        if not isinstance(seat, int) or not 0 <= seat < room.size or room.seats[seat] is not None:
            raise TableError("Pick an empty seat for the computer player")
        address = f"{BOT_PREFIX}{seat}"
        taken = {room.names.get(a) for a in room.seats if a}
        room.names[address] = next(n for n in BOT_NAMES if f"{n} (bot)" not in taken) + " (bot)"
        room.seats[seat] = address
        await self._host_broadcast()

    async def _host_remove_bot(self, seat: Any) -> None:
        room = self._own_room()
        if room.started:
            raise TableError("The game has started")
        if not isinstance(seat, int) or not 0 <= seat < room.size or not is_bot(room.seats[seat]):
            raise TableError("No computer player in that seat")
        room.names.pop(room.seats[seat], None)
        room.seats[seat] = None
        await self._host_broadcast()

    async def _host_start(self) -> None:
        room = self._own_room()
        if room.started and not room.game.over:
            raise TableError("The game has already started")
        if None in room.seats:
            raise TableError(f"All {room.size} seats must be filled to start")
        room.game = Game(room.size, self.rng)
        self.bots = {i: Bot(i, self.rng) for i, a in enumerate(room.seats) if is_bot(a)}
        await self._host_broadcast()

    def _own_room(self) -> Room:
        if not self.room:
            raise TableError("Only the host can do that")
        return self.room

    async def _host_broadcast(self, also: list[str] = ()) -> None:
        """Sends everyone the room as it now stands, and saves it."""
        room = self.room
        room.seq += 1
        self._save()
        lobby = room.lobby()
        self._on_lobby(self.address, lobby)
        for address in list(room.names) + list(also):
            if address != self.address:
                await self._post(address, {"t": "lobby", "lobby": lobby})
        for address in room.seats:
            await self._send_state(address)
        advert = room.advert()
        if advert != self._advert:
            self._advert = advert
            for address in self.peers:
                await self._post(address, {"t": "room", "room": advert})
        self._schedule_bots()

    async def _send_state(self, address: str | None) -> None:
        room = self.room
        if not room or not room.game or address is None:
            return
        seat = room.seat_of(address)
        if seat is None:
            return
        view = room.game.view(seat)
        if is_bot(address):
            self.bots[seat].observe(view)
        elif address == self.address:
            self._on_state(self.address, {"seq": room.seq, "view": view})
        else:
            await self._post(address, {"t": "state", "seq": room.seq, "view": view})

    def _schedule_bots(self) -> None:
        room = self.room
        if not room or not room.game or room.game.over or not self.bots:
            return
        if room.game.turn in self.bots and (self._bot_task is None or self._bot_task.done()):
            self._bot_task = asyncio.create_task(self._run_bot())

    async def _run_bot(self) -> None:
        await asyncio.sleep(self.bot_delay)
        self._bot_task = None  # so the broadcast below can schedule the next bot
        room = self.room
        if not room or not room.game or room.game.over:
            return
        game, seat = room.game, room.game.turn
        bot = self.bots.get(seat)
        if bot is None:
            return
        action = bot.act(game.view(seat))
        if action is None:
            return
        try:
            self._apply(game, seat, action)
        except TableError:
            logger.exception("bot in seat %d made an illegal move %s", seat, action)
            self._apply(game, seat, self._safe_move(game, seat))
        await self._host_broadcast()
        self._changed()

    @staticmethod
    def _safe_move(game: Game, seat: int) -> dict[str, Any]:
        """A move that is always legal, for a bot whose own choice was not."""
        mates = [p for p in range(game.players) if team_of(p) == team_of(seat) and game.hands[p]]
        if game.must_pass:
            return {"action": "pass", "to": next(p for p in mates if p != seat)}
        opponents = [p for p in range(game.players) if team_of(p) != team_of(seat) and game.hands[p]]
        if opponents:  # the bluff: asking for our own card is always allowed
            return {"action": "ask", "target": opponents[0], "card": next(iter(game.hands[seat]))}
        set_id = next(s for s in SETS if s not in game.claimed)
        return {"action": "declare", "assignment": {c: seat for c in SETS[set_id]}}

    # ---------------------------------------------------------- persistence

    def _save(self) -> None:
        if not self.save_path:
            return
        data = {"room": self.room.to_dict() if self.room else None}
        tmp = self.save_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(self.save_path)

    def _load(self) -> None:
        if not self.save_path or not self.save_path.exists():
            return
        try:
            data = json.loads(self.save_path.read_text())
            if data.get("room"):
                self.room = Room.from_dict(data["room"])
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning("ignoring a saved room that does not load: %s", exc)
            return
        if self.room and self.room.host != self.address:
            self.room = None  # saved under another identity
            return
        if self.room:
            room = self.room
            self.joined = self.address
            self._on_lobby(self.address, room.lobby())
            if room.game:
                self.bots = {i: Bot(i, self.rng) for i, a in enumerate(room.seats) if is_bot(a)}
                seat = room.seat_of(self.address)
                if seat is not None:
                    self._on_state(self.address, {"seq": room.seq, "view": room.game.view(seat)})

    async def resume(self) -> None:
        """After a restart, picks a saved game back up (bots included)."""
        if self.room:
            await self._host_broadcast()
