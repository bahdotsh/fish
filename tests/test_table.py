import asyncio
import random

import pytest

from fish.bot import Bot
from fish.engine import team_of
from fish.table import Table


class Bus:
    """Delivers messages between tables in order, like the mesh but in memory."""

    def __init__(self):
        self.tables: dict[str, Table] = {}
        self.queue: list[tuple[str, str, dict]] = []
        self.sent: list[tuple[str, str, dict]] = []

    def add(self, address: str, name: str, **kw) -> Table:
        async def send(to, message, sender=address):
            self.queue.append((sender, to, message))
            self.sent.append((sender, to, message))

        table = Table(address, name, send, bot_delay=0, rng=random.Random(len(self.tables)), **kw)
        self.tables[address] = table
        return table

    async def meet(self):
        """Everyone says hello to everyone."""
        for a, ta in self.tables.items():
            for b, tb in self.tables.items():
                if a != b:
                    await tb.peer_seen(a, ta.name)
        await self.settle()

    async def settle(self):
        for _ in range(100000):
            await asyncio.sleep(0)  # let bot tasks run
            if not self.queue:
                await asyncio.sleep(0)
                if not self.queue:
                    return
                continue
            sender, to, message = self.queue.pop(0)
            if to in self.tables:
                await self.tables[to].receive(sender, message)
        raise AssertionError("the bus never went quiet")


async def seated_room(size=6, humans=None):
    bus = Bus()
    humans = humans or size
    names = ["Ana", "Ben", "Cy", "Di", "Ed", "Flo", "Gus", "Hal"][:humans]
    tables = [bus.add(f"off1{n.lower()}", n) for n in names]
    await bus.meet()
    host = tables[0]
    await host.command({"cmd": "host", "size": size})
    await bus.settle()
    for i, t in enumerate(tables):
        if t is not host:
            await t.command({"cmd": "join", "host": host.address})
            await bus.settle()
        await t.command({"cmd": "sit", "seat": i})
        await bus.settle()
    return bus, host, tables


async def test_rooms_are_advertised_and_joined():
    bus, host, tables = await seated_room()
    ben = tables[1]
    assert ben.rooms[host.address]["seated"] == 6
    assert ben.joined == host.address
    assert [s["name"] for s in ben.lobby["seats"]] == ["Ana", "Ben", "Cy", "Di", "Ed", "Flo"]


async def test_a_taken_seat_is_refused():
    bus, host, tables = await seated_room()
    await tables[1].command({"cmd": "sit", "seat": 0})
    await bus.settle()
    assert tables[1].error == "That seat is taken"
    assert host.room.seats[1] == tables[1].address


async def test_start_needs_every_seat():
    bus = Bus()
    host = bus.add("off1ana", "Ana")
    await host.command({"cmd": "host", "size": 6})
    await host.command({"cmd": "sit", "seat": 0})
    await host.command({"cmd": "start"})
    assert "seats must be filled" in host.error


async def test_each_player_gets_only_their_own_hand():
    bus, host, tables = await seated_room()
    await host.command({"cmd": "start"})
    await bus.settle()
    game = host.room.game
    for seat, t in enumerate(tables):
        assert t.view["you"] == seat
        assert set(t.view["hand"]) == game.hands[seat]
    # No message to a player ever carries a hand but their own.
    for sender, to, message in bus.sent:
        if message["t"] == "state":
            seat = host.room.seat_of(to)
            assert message["view"]["you"] == seat


async def test_illegal_moves_come_back_as_errors():
    bus, host, tables = await seated_room()
    await host.command({"cmd": "start"})
    await bus.settle()
    game = host.room.game
    idle = next(t for t in tables if t.view["turn"] != t.view["you"])
    await idle.command({"cmd": "act", "action": "ask", "target": 0, "card": "2S"})
    await bus.settle()
    assert idle.error


async def play_out(bus, host, tables, rng):
    """Every human plays as a bot would, through their own table."""
    brains = {t.address: Bot(i, rng) for i, t in enumerate(tables)}
    for _ in range(5000):
        game = host.room.game
        if game.over:
            return
        mover = next((t for t in tables if t.view and t.view["turn"] == t.view["you"]), None)
        if mover is None:
            await bus.settle()
            continue
        action = brains[mover.address].act(mover.view)
        await mover.command({"cmd": "act", **action})
        await bus.settle()
        if mover.error:  # a guess the rules refused: the bluff is always legal
            mine = mover.view["hand"][0]
            opp = next(p for p in range(game.players) if team_of(p) != mover.view["team"] and mover.view["counts"][p])
            await mover.command({"cmd": "act", "action": "ask", "target": opp, "card": mine})
            await bus.settle()
    raise AssertionError("the game did not finish")


@pytest.mark.parametrize("size", [6, 8])
async def test_a_whole_game_over_the_bus(size):
    bus, host, tables = await seated_room(size)
    await host.command({"cmd": "start"})
    await bus.settle()
    await play_out(bus, host, tables, random.Random(size))
    final = [t.view for t in tables]
    assert all(v["over"] for v in final)
    assert len({v["winner"] for v in final}) == 1
    assert sum(final[0]["score"]) == 9


async def test_bots_fill_seats_and_play_to_the_end():
    bus = Bus()
    host = bus.add("off1ana", "Ana")
    await host.command({"cmd": "host", "size": 6})
    await host.command({"cmd": "sit", "seat": 0})
    for seat in range(1, 6):
        await host.command({"cmd": "add_bot", "seat": seat})
    assert host.lobby["seats"][3]["name"].endswith("(bot)")
    await host.command({"cmd": "start"})
    brain = Bot(0, random.Random(9))
    for _ in range(5000):
        await bus.settle()
        view = host.view
        if view["over"]:
            break
        if view["turn"] == 0:
            await host.command({"cmd": "act", **brain.act(view)})
            if host.error:
                await host.command({"cmd": "act", "action": "ask", "target": 1, "card": view["hand"][0]})
        await asyncio.sleep(0)
    assert host.view["over"]
    assert sum(host.view["score"]) == 9


async def test_a_restarted_player_is_put_back_in_their_seat():
    bus, host, tables = await seated_room()
    await host.command({"cmd": "start"})
    await bus.settle()
    cy = tables[2]
    # Cy's app restarts: a fresh table with the same address knows nothing.
    fresh = bus.add(cy.address, "Cy")
    await host.peer_seen(fresh.address, "Cy")
    await bus.settle()
    assert fresh.joined == host.address
    assert fresh.view["you"] == 2
    assert set(fresh.view["hand"]) == host.room.game.hands[2]


async def test_a_restarted_host_resumes_the_game(tmp_path):
    save = tmp_path / "table.json"
    bus = Bus()
    host = bus.add("off1ana", "Ana", save_path=save)
    await host.command({"cmd": "host", "size": 6})
    await host.command({"cmd": "sit", "seat": 0})
    for seat in range(1, 6):
        await host.command({"cmd": "add_bot", "seat": seat})
    await host.command({"cmd": "start"})
    hands = [set(h) for h in host.room.game.hands]
    turn = host.room.game.turn

    host.bot_delay = 3600  # freeze the bots before "restarting"
    again = Table("off1ana", "Ana", host._send, save_path=save, bot_delay=3600)
    assert again.room.game.hands == hands and again.room.game.turn == turn
    assert again.view["hand"] and again.joined == "off1ana"


async def test_leaving_a_game_in_progress_is_refused():
    bus, host, tables = await seated_room()
    await host.command({"cmd": "start"})
    await bus.settle()
    await tables[3].command({"cmd": "leave"})
    assert "in progress" in tables[3].error


async def test_closing_the_room_tells_the_players():
    bus, host, tables = await seated_room()
    await host.command({"cmd": "close"})
    await bus.settle()
    assert tables[1].joined is None and host.address not in tables[1].rooms


async def test_computer_players_get_different_names():
    bus = Bus()
    host = bus.add("off1ana", "Ana")
    await host.command({"cmd": "host", "size": 8})
    for seat in range(8):
        await host.command({"cmd": "add_bot", "seat": seat})
    names = [s["name"] for s in host.lobby["seats"]]
    assert len(set(names)) == 8


async def test_players_out_of_range_show_as_away_and_come_back():
    bus, host, tables = await seated_room()
    await host.command({"cmd": "start"})
    await bus.settle()
    cy, ben = tables[2], tables[1]
    await host.peer_lost(cy.address)
    await bus.settle()
    assert ben.lobby["seats"][2]["away"] and not ben.lobby["seats"][1]["away"]
    await host.peer_seen(cy.address, "Cy")
    await bus.settle()
    assert not ben.lobby["seats"][2]["away"]


async def test_a_player_sees_when_the_host_is_out_of_range():
    bus, host, tables = await seated_room()
    ben = tables[1]
    assert not ben.snapshot()["host_away"]
    await ben.peer_lost(host.address)
    assert ben.snapshot()["host_away"]
