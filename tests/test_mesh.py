"""Three real SDK services on this machine, linked over loopback, each with
a Fish player: discovery, hello, a room, seats, bots and a game."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import pytest

from fish.app import Player
from fish.mesh import Mesh
from fish.rpc import LocalApi
from fish.service import APP_ID, ServiceSpec

NAMES = ["Ana", "Ben", "Cy"]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(predicate, timeout: float = 60.0, every: float = 0.2):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(every)


@pytest.fixture
async def trio(tmp_path: Path):
    links = [f"127.0.0.1:{free_port()}" for _ in NAMES]
    # Each dials only the ones before it (see the README's known issues).
    specs = [ServiceSpec(tmp_path / n.lower(), transport="lan", listen=links[i], peers=links[:i])
             for i, n in enumerate(NAMES)]
    procs = [spec.spawn() for spec in specs]
    players = [Player(Mesh(LocalApi(str(s.socket_path), APP_ID), n), s.home, bot_delay=0.05)
               for s, n in zip(specs, NAMES)]
    try:
        await asyncio.gather(*(p.start() for p in players))
        yield [p.table for p in players]
    finally:
        for p in players:
            await p.close()
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(20)


async def test_a_game_across_three_real_services(trio):
    ana, ben, cy = trio
    await until(lambda: all(len(t.peers) == 2 for t in trio))
    assert ben.peers[ana.address] == "Ana"

    await ana.command({"cmd": "host", "size": 6})
    await until(lambda: ana.address in ben.rooms and ana.address in cy.rooms)
    for seat, t in ((1, ben), (2, cy)):
        await t.command({"cmd": "join", "host": ana.address})
        await until(lambda: t.lobby is not None)
        await t.command({"cmd": "sit", "seat": seat})
    await ana.command({"cmd": "sit", "seat": 0})
    await until(lambda: [s and s["name"] for s in cy.lobby["seats"][:3]] == ["Ana", "Ben", "Cy"])

    for seat in range(3, 6):
        await ana.command({"cmd": "add_bot", "seat": seat})
    await ana.command({"cmd": "start"})
    await until(lambda: ben.view is not None and cy.view is not None)
    game = ana.room.game
    assert set(ben.view["hand"]) == game.hands[1]
    assert set(cy.view["hand"]) == game.hands[2]

    # A refused move comes back as an error, over the network.
    idle = next(t for t in (ben, cy) if t.view["turn"] != t.view["you"])
    await idle.command({"cmd": "act", "action": "pass", "to": 0})
    await until(lambda: idle.error is not None)

    # A move by Ben reaches everyone: hand Ben the turn and let him bluff.
    game.turn, game.must_pass = 1, False
    await ana.resume()
    await until(lambda: ben.view["turn"] == 1)
    card = ben.view["hand"][0]
    await ben.command({"cmd": "act", "action": "ask", "target": 0, "card": card})
    await until(lambda: cy.view["history"] and cy.view["history"][-1]["card"] == card)
    assert cy.view["history"][-1] == {"asker": 1, "target": 0, "card": card, "got": False}
    assert cy.view["turn"] == 0  # Ana, a person, so nothing moves on by itself
