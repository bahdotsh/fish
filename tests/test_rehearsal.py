"""Six real SDK services on this machine, linked over loopback: a whole
game, with a player's app and then the host's app restarting mid-game.

Slow (a few minutes), so it runs only with FISH_SLOW=1.
"""

from __future__ import annotations

import asyncio
import os
import random
import socket
from pathlib import Path

import pytest

from fish.app import Player
from fish.bot import Bot
from fish.engine import team_of
from fish.mesh import Mesh
from fish.rpc import LocalApi
from fish.service import APP_ID, ServiceSpec

pytestmark = pytest.mark.skipif(not os.environ.get("FISH_SLOW"), reason="set FISH_SLOW=1 to run")

NAMES = ["Ana", "Ben", "Cy", "Di", "Ed", "Flo"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(predicate, timeout: float = 90.0, every: float = 0.1):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(every)


class Seat:
    """One player's machine: a service process and the app on top of it."""

    def __init__(self, spec: ServiceSpec, name: str):
        self.spec, self.name = spec, name
        self.proc = None
        self.player: Player | None = None

    async def up(self):
        self.proc = self.spec.spawn()
        self.player = Player(Mesh(LocalApi(str(self.spec.socket_path), APP_ID), self.name), self.spec.home,
                             bot_delay=0.05)
        await self.player.start()

    async def down(self):
        await self.player.close()
        self.proc.terminate()
        await asyncio.to_thread(self.proc.wait, 20)

    @property
    def table(self):
        return self.player.table


@pytest.fixture
async def room(tmp_path: Path):
    links = [f"127.0.0.1:{free_port()}" for _ in NAMES]
    seats = [Seat(ServiceSpec(tmp_path / n.lower(), transport="lan", listen=links[i], peers=links[:i]), n)
             for i, n in enumerate(NAMES)]
    try:
        await asyncio.gather(*(s.up() for s in seats))
        yield seats
    finally:
        for s in seats:
            try:
                await s.down()
            except Exception:
                pass


async def move(seats, brains):
    """The player whose turn it is makes one move. Returns False if nobody can yet."""
    for s in seats:
        v = s.table.view
        if v and not v["over"] and v["turn"] == v["you"]:
            before = s.table.seq
            await s.table.command({"cmd": "act", **brains[v["you"]].act(v)})
            if s.table.error:  # a guess the rules refused: the bluff is always legal
                opp = next(p for p in range(6) if team_of(p) != v["team"] and v["counts"][p])
                await s.table.command({"cmd": "act", "action": "ask", "target": opp, "card": v["hand"][0]})
            await until(lambda: s.table.seq > before, timeout=30)
            return True
    return False


async def test_a_whole_game_with_restarts(room):
    seats = room
    ana = seats[0].table
    await until(lambda: all(len(s.table.peers) == 5 for s in seats))

    await ana.command({"cmd": "host", "size": 6})
    await until(lambda: all(ana.address in s.table.rooms for s in seats[1:]))
    for i, s in enumerate(seats):
        if i:
            await s.table.command({"cmd": "join", "host": ana.address})
            await until(lambda: s.table.lobby is not None)
        await s.table.command({"cmd": "sit", "seat": i})
    await until(lambda: all(seat for seat in ana.room.seats))
    await ana.command({"cmd": "start"})
    await until(lambda: all(s.table.view for s in seats))

    brains = {i: Bot(i, random.Random(i)) for i in range(6)}
    moves = 0
    restarted_player = restarted_host = False
    while not ana.room.game.over:
        assert moves < 600, "the game did not finish"
        if await move(seats, brains):
            moves += 1
        else:
            await asyncio.sleep(0.05)

        if moves == 10 and not restarted_player:
            # Cy's laptop restarts: Cy's app comes back knowing nothing.
            restarted_player = True
            cy = seats[2]
            await cy.down()
            await cy.up()
            await until(lambda: cy.table.view is not None)
            assert set(cy.table.view["hand"]) == ana.room.game.hands[2]

        if moves == 20 and not restarted_host:
            # Ana's laptop restarts: the game comes back from her saved room.
            restarted_host = True
            hands = [set(h) for h in ana.room.game.hands]
            await seats[0].down()
            await seats[0].up()
            ana = seats[0].table
            assert [set(h) for h in ana.room.game.hands] == hands
            seq = ana.room.seq
            await until(lambda: all(s.table.seq >= seq for s in seats[1:]))

    finals = [s.table for s in seats]
    await until(lambda: all(t.view["over"] for t in finals))
    assert len({t.view["winner"] for t in finals}) == 1
    assert sum(finals[0].view["score"]) == 9
