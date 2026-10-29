"""fish play | rehearse

    fish play --name Ana                        one player on this machine
    fish rehearse --names Ana Ben Cy Di Ed Flo  linked players on this machine, over loopback
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import signal
import socket
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .app import Player, make_phone_server, make_server
from .mesh import Mesh, clean_name
from .phones import lan_ip
from .rpc import LocalApi
from .service import APP_ID, ServiceSpec, default_home

log = logging.getLogger("fish")

# What a player home may hold. --fresh deletes a home only when it holds
# nothing else, so a mistyped --home never takes someone's files with it.
HOME_ENTRIES = {"config.json", "store.key", "keys", "state", "service.log", "service.log.1",
                "table.json", "table.tmp", "phones.json", "phones.tmp"}


def wipe_home(home: Path) -> None:
    if not home.exists():
        return
    stray = [p.name for p in home.iterdir() if p.name not in HOME_ENTRIES]
    if stray:
        raise SystemExit(f"--fresh: {home} holds other files ({', '.join(stray[:3])}); not deleting it")
    shutil.rmtree(home)


def port_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        # As uvicorn binds: connections from a page that was just closed
        # linger in TIME_WAIT and must not count as the port being taken.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


async def start_watching(players: list[Player], entries: list[Entry], procs: list[subprocess.Popen[bytes]]) -> None:
    """Starts the players, but stops at once if an SDK service dies, with
    the end of its log, instead of waiting for a socket that never comes."""
    starting = asyncio.ensure_future(asyncio.gather(*(p.start() for p in players)))
    while not starting.done():
        for entry, proc in zip(entries, procs):
            if proc.poll() is not None:
                starting.cancel()
                await asyncio.gather(starting, return_exceptions=True)
                raise SystemExit(service_died(entry))
        await asyncio.wait([starting], timeout=0.3)
    starting.result()


def service_died(entry: Entry) -> str:
    log_path = entry.spec.home / "service.log"
    try:
        tail = log_path.read_text(errors="replace").strip().splitlines()[-8:]
    except OSError:
        tail = []
    lines = [f"{entry.name}: the SDK service stopped while starting. The end of {log_path}:", ""]
    return "\n".join(lines + ["    " + line for line in tail])


@dataclass
class Entry:
    """One player to run: their name, SDK service, page port and phone port."""
    name: str
    spec: ServiceSpec
    port: int
    phone_port: int | None = None


async def run_players(entries: list[Entry], open_browser: bool, bot_delay: float, lan_ip: str | None) -> None:
    """Starts a service, a player and a web page (and a phone page) for each entry."""
    for e in entries:
        if not port_free(e.port):
            raise SystemExit(f"port {e.port} is already in use; pick another with --port")
        if e.phone_port and not port_free(e.phone_port, "0.0.0.0"):
            raise SystemExit(f"port {e.phone_port} is already in use; pick another with --phone-port")
    procs: list[subprocess.Popen[bytes]] = []
    players: list[Player] = []
    try:
        for e in entries:
            log.info("%s: starting the SDK service (%s), log %s", e.name, e.spec.transport, e.spec.home / "service.log")
            procs.append(e.spec.spawn())
        for e in entries:
            mesh = Mesh(LocalApi(str(e.spec.socket_path), APP_ID), e.name)
            players.append(Player(mesh, e.spec.home, bot_delay=bot_delay, phone_port=e.phone_port, lan_ip=lan_ip))
        await start_watching(players, entries, procs)
        servers = [make_server(p, e.port) for p, e in zip(players, entries)]
        servers += [make_phone_server(p) for p in players if p.phones]
        serving = [asyncio.create_task(s.serve()) for s in servers]
        await asyncio.sleep(0.3)
        for player, e in zip(players, entries):
            log.info("%s is at http://127.0.0.1:%d/   (%s)", e.name, e.port, player.mesh.address)
            if player.phones and not player.phones.url:
                log.info("  phones: no local network found, so phones cannot join; try --lan-ip")
        if open_browser:
            for e in entries:
                webbrowser.open(f"http://127.0.0.1:{e.port}/")
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await asyncio.wait([*serving, asyncio.create_task(stop.wait())], return_when=asyncio.FIRST_COMPLETED)
        log.info("stopping")
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*serving, return_exceptions=True)
    finally:
        for player in players:
            try:
                await player.close()
            except Exception:
                pass
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(20)
            except subprocess.TimeoutExpired:
                proc.kill()


def cmd_play(args: argparse.Namespace) -> int:
    name = clean_name(args.name)
    if not name:
        print("give a name with --name", file=sys.stderr)
        return 2
    home = Path(args.home).expanduser() if args.home else default_home(name)
    if args.fresh:
        wipe_home(home)
    spec = ServiceSpec(home, transport=args.transport, listen=args.listen, peers=args.peer,
                       lan_discovery=not args.no_lan_discovery)
    entry = Entry(name, spec, args.port, None if args.no_phones else args.phone_port)
    asyncio.run(run_players([entry], not args.no_browser, args.bot_delay, args.lan_ip or lan_ip()))
    return 0


def cmd_rehearse(args: argparse.Namespace) -> int:
    names = [clean_name(n) for n in args.names]
    if len(set(n.lower() for n in names if n)) != len(names) or len(names) < 2:
        print("rehearse needs two or more different names", file=sys.stderr)
        return 2
    root = Path(args.root).resolve()
    links = [f"127.0.0.1:{args.link_port + i}" for i in range(len(names))]
    entries = []
    for i, name in enumerate(names):
        home = root / f".{name.lower()}"
        if args.fresh:
            wipe_home(home)
        # Each player dials only the ones before it: two nodes dialling each
        # other at once can lose the first key exchange.
        spec = ServiceSpec(home, transport="lan", listen=links[i], peers=links[:i])
        entries.append(Entry(name, spec, args.port + i, None if args.no_phones else args.phone_port + i))
    asyncio.run(run_players(entries, args.open, args.bot_delay, args.lan_ip or lan_ip()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fish", description="Fish, the team card game, played offline.")
    sub = parser.add_subparsers(dest="command", required=True)

    play = sub.add_parser("play", help="play on this machine with players nearby")
    play.add_argument("--name", required=True, help="your name at the table")
    play.add_argument("--port", type=int, default=7700, help="loopback port for your page (default 7700)")
    play.add_argument("--transport", choices=["lan", "ble", "both"], default="lan",
                      help="lan: the same Wi-Fi or network (default); ble: Bluetooth")
    play.add_argument("--listen", help="lan: HOST:PORT to accept other players on")
    play.add_argument("--peer", action="append", default=[], help="lan: HOST:PORT of another player (repeatable)")
    play.add_argument("--no-lan-discovery", action="store_true", help="lan: find players only through --peer")
    play.add_argument("--phone-port", type=int, default=7710,
                      help="port phones on the local network join on (default 7710)")
    play.add_argument("--no-phones", action="store_true", help="do not let phones join from this laptop")
    play.add_argument("--lan-ip", help="this laptop's address for phones, if Fish picks the wrong one")
    play.add_argument("--home", help="where this player's keys and state live (default ~/.fish/<name>)")
    play.add_argument("--fresh", action="store_true", help="start with a new identity and no saved game")
    play.add_argument("--no-browser", action="store_true", help="do not open the page")
    play.add_argument("--bot-delay", type=float, default=1.2, help="seconds a computer player thinks")
    play.set_defaults(fn=cmd_play)

    rehearse = sub.add_parser("rehearse", help="several linked players on this machine")
    rehearse.add_argument("--names", nargs="+", default=["Ana", "Ben", "Cy", "Di", "Ed", "Flo"])
    rehearse.add_argument("--root", default="./rehearsal", help="where the players' homes go")
    rehearse.add_argument("--port", type=int, default=7700, help="first page port; each player gets the next")
    rehearse.add_argument("--link-port", type=int, default=7920, help="first loopback link port")
    rehearse.add_argument("--phone-port", type=int, default=7710, help="first phone port")
    rehearse.add_argument("--no-phones", action="store_true", help="no phone pages")
    rehearse.add_argument("--lan-ip", help="the address phones use to reach this machine")
    rehearse.add_argument("--fresh", action="store_true", help="new identities and no saved games")
    rehearse.add_argument("--open", action="store_true", help="open every player's page")
    rehearse.add_argument("--bot-delay", type=float, default=1.2, help="seconds a computer player thinks")
    rehearse.set_defaults(fn=cmd_rehearse)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
