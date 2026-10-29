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
from pathlib import Path

from .app import Player, make_server
from .mesh import Mesh, clean_name
from .rpc import LocalApi
from .service import APP_ID, ServiceSpec, default_home

log = logging.getLogger("fish")

# What a player home may hold. --fresh deletes a home only when it holds
# nothing else, so a mistyped --home never takes someone's files with it.
HOME_ENTRIES = {"config.json", "store.key", "keys", "state", "service.log", "service.log.1",
                "table.json", "table.tmp"}


def wipe_home(home: Path) -> None:
    if not home.exists():
        return
    stray = [p.name for p in home.iterdir() if p.name not in HOME_ENTRIES]
    if stray:
        raise SystemExit(f"--fresh: {home} holds other files ({', '.join(stray[:3])}); not deleting it")
    shutil.rmtree(home)


def port_free(port: int) -> bool:
    with socket.socket() as s:
        # As uvicorn binds: connections from a page that was just closed
        # linger in TIME_WAIT and must not count as the port being taken.
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


async def run_players(entries: list[tuple[str, ServiceSpec, int]], open_browser: bool, bot_delay: float) -> None:
    """Starts a service, a player and a web page for each (name, spec, port)."""
    taken = [port for _, _, port in entries if not port_free(port)]
    if taken:
        raise SystemExit(f"port {taken[0]} is already in use; pick another with --port")
    procs: list[subprocess.Popen[bytes]] = []
    players: list[Player] = []
    try:
        for name, spec, port in entries:
            log.info("%s: starting the SDK service (%s), log %s", name, spec.transport, spec.home / "service.log")
            procs.append(spec.spawn())
        for name, spec, port in entries:
            mesh = Mesh(LocalApi(str(spec.socket_path), APP_ID), name)
            players.append(Player(mesh, spec.home, bot_delay=bot_delay))
        await asyncio.gather(*(p.start() for p in players))
        servers = [make_server(p, port) for p, (_, _, port) in zip(players, entries)]
        serving = [asyncio.create_task(s.serve()) for s in servers]
        await asyncio.sleep(0.3)
        for player, (name, _, port) in zip(players, entries):
            log.info("%s is at http://127.0.0.1:%d/   (%s)", name, port, player.mesh.address)
        if open_browser:
            for _, _, port in entries:
                webbrowser.open(f"http://127.0.0.1:{port}/")
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
    asyncio.run(run_players([(name, spec, args.port)], not args.no_browser, args.bot_delay))
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
        entries.append((name, spec, args.port + i))
    asyncio.run(run_players(entries, args.open, args.bot_delay))
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
    rehearse.add_argument("--fresh", action="store_true", help="new identities and no saved games")
    rehearse.add_argument("--open", action="store_true", help="open every player's page")
    rehearse.add_argument("--bot-delay", type=float, default=1.2, help="seconds a computer player thinks")
    rehearse.set_defaults(fn=cmd_rehearse)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
