"""Phones joining a laptop's game through the phone server."""

import asyncio
import random

import pytest
from starlette.testclient import TestClient

from fish.phones import Phones, build_phone_app
from fish.app import Static, WEB
from fish.table import Table

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def laptop(tmp_path):
    async def send(address, message):  # as the app routes it: phones only here
        await phones.deliver(address, message)

    host = Table("off1laptop", "Laptop", send, rng=random.Random(1), bot_delay=0)
    asyncio.run(host.command({"cmd": "host", "size": 6}))
    phones = Phones(host, tmp_path, 7710, "192.168.1.5", bot_delay=0)
    client = TestClient(build_phone_app(phones, lambda: Static(directory=WEB / "static")))
    return host, phones, client


def join(client, phones, name="Pia"):
    res = client.post("/join", json={"k": phones.key, "name": name}, headers=ORIGIN)
    assert res.status_code == 200
    return res.json()


def test_the_qr_code_carries_the_join_key(laptop):
    host, phones, client = laptop
    assert phones.url == f"http://192.168.1.5:7710/?k={phones.key}"
    assert phones.qr_svg().startswith(b"<?xml")


def test_joining_needs_the_key_and_a_name(laptop):
    host, phones, client = laptop
    assert client.post("/join", json={"k": "wrong", "name": "Pia"}, headers=ORIGIN).status_code == 403
    assert client.post("/join", json={"k": phones.key, "name": "  "}, headers=ORIGIN).status_code == 400
    assert set(join(client, phones)) == {"id", "secret"}


def test_a_phone_needs_its_secret_to_connect(laptop):
    host, phones, client = laptop
    guest = join(client, phones)
    with pytest.raises(Exception):
        with client.websocket_connect(f"/ws?id={guest['id']}&s=nope", headers=ORIGIN) as ws:
            ws.receive_json()


def test_a_phone_joins_sits_and_goes_away(laptop):
    host, phones, client = laptop
    guest = join(client, phones)
    address = f"phone:{guest['id']}"
    with client.websocket_connect(f"/ws?id={guest['id']}&s={guest['secret']}", headers=ORIGIN) as ws:
        snap = ws.receive_json()
        assert snap["device"] == "phone" and snap["host_name"] == "Laptop"
        assert snap["joined"] == "off1laptop"  # joined the open room by itself
        ws.send_json({"cmd": "sit", "seat": 3})
        ws.send_json({"cmd": "host", "size": 8})  # a phone may not run a room: ignored
        for _ in range(20):
            snap = ws.receive_json()
            if snap["lobby"]["seats"][3]:
                break
        assert snap["lobby"]["seats"][3]["name"] == "Pia"
    assert host.room.seats[3] == address
    assert host.room.size == 6
    assert host.lobby["seats"][3]["away"]  # the phone's socket closed


def test_phones_keep_their_seats_across_a_laptop_restart(laptop, tmp_path):
    host, phones, client = laptop
    guest = join(client, phones)
    again = Phones(host, tmp_path, 7710, "192.168.1.5", bot_delay=0)
    assert again.key == phones.key
    assert again.find(guest["id"], guest["secret"]) is not None
