# Fish

A team card game for 6 or 8 people, played on laptops sitting round the
same table. There's no server and no internet: the laptops find each other
over the local network or Bluetooth through the
[Offline Protocol SDK](https://github.com/Offline-Protocol/offline-protocol-sdk),
and every message between them is end-to-end encrypted.

## The rules

**Teams.** 6 or 8 players sit in a circle. Every other seat is on the same
team, so there are two teams of 3 or 4: Team Gold and Team Tide. Teammates
may not talk to each other or share information.

**The deck.** 54 cards (a standard deck plus a white and a black joker) in
9 sets of 6:

| Set | Cards |
|---|---|
| Low Spades, Hearts, Diamonds, Clubs | 2, 3, 4, 5, 6, 7 of that suit |
| Eights & Jokers | 8♠ 8♥ 8♦ 8♣, white joker, black joker |
| High Spades, Hearts, Diamonds, Clubs | 9, 10, J, Q, K, A of that suit |

With 6 players everyone gets 9 cards. With 8, six players get 7 and two get 6.

**Asking.** A random player goes first. On your turn you ask one player on
the other team, who has cards, for one specific card. You must already hold
a card of that card's set.

- If they have it, they hand it over and you ask again.
- If they don't, the turn passes to them.
- The only bluff allowed: you may ask for a card you hold yourself. The
  answer is always no, and the turn passes.

**Declaring.** On your team's turn, anyone on the team can declare a set,
even a player with no cards left. You name which teammate holds each of its
six cards.

- All six right: your team wins the set.
- Anything wrong, whether a card is with the other team or with a different
  teammate: the other team wins the set.
- Either way the set's cards leave play and the turn stays where it is.

**Running out.** A player with no cards can't ask. If the turn lands on
them, they hand it to a teammate who still has cards. If the whole team is
out, the turn goes to the next player round the circle who has cards. A
team with no cards can't declare: it waits for the other team to get one
wrong.

**Winning.** The game ends when all 9 sets are declared. The team with more
sets wins. With 9 sets there's never a tie.

**What you see.** Your own hand, how many cards everyone holds, the sets
each team has won, the last declaration, and the last 2 asks.

## Install

Needs Python 3.10 or later, on macOS (Apple silicon) or Linux.

```bash
git clone <this repo> fish && cd fish
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

This pulls `offline-protocol-sdk` from PyPI, which ships the
`offline-protocol-service` that Fish runs underneath.

## Play

Every player runs Fish on their own laptop, with their own name:

```bash
.venv/bin/fish play --name Ana
```

Your page opens at <http://127.0.0.1:7700/>. One player hosts a 6- or
8-seat room. Everyone else sees it under *Rooms nearby*, joins, and picks a
seat; the seat colour is the team. Empty seats can take computer players.
When every seat is filled, the host starts the game.

**Finding each other.** By default Fish uses the local network and finds
other players on the same Wi-Fi by itself. It doesn't need internet access,
only a shared network, which can be a phone hotspot. Other ways in:

```bash
fish play --name Ana --transport ble                 # Bluetooth, no network at all
fish play --name Ana --listen 0.0.0.0:7911 \
                     --peer 192.168.1.20:7911        # name the other laptops yourself
```

**If someone drops out.** Their seat shows *Away* and the game waits on
their turn. When their laptop is back in range, or their app restarts, they
get their seat and hand back. The host's app saves the game after every
move, so if the host restarts, the game picks up where it was.

A player's identity and the host's saved game live in `~/.fish/<name>`.
`--fresh` starts over with a new identity and no saved game.

## Try it on one machine

```bash
.venv/bin/fish rehearse --names Ana Ben --open
```

This runs linked players in one process over loopback, with pages on ports
7700, 7701 and up. Two people plus four computer players make a full table.

## How it works

```
browser page        draws the table, sends your moves
   |  WebSocket on 127.0.0.1
fish app            the table: lobby, seats, and (on the host) the game
   |  JSON-RPC on a Unix socket
SDK service         identity, MLS sessions, Bluetooth / LAN transport
   |
the other laptops
```

- **The host holds the deck.** Its app owns the only real game. It checks
  every move and sends each player their own view, which holds their own
  hand and nothing else. No other laptop ever receives cards that aren't
  its own.
- **Messages** are small JSON envelopes inside ordinary 1:1 SDK messages,
  over the MLS session the SDK sets up between each pair of laptops.
- **The page** only listens on loopback. The WebSocket accepts nothing but
  Fish's own page, so another website can't drive your game.

| File | What it does |
|---|---|
| `fish/cards.py` | the deck and its 9 sets |
| `fish/engine.py` | the rules: dealing, asking, declaring, turns, scoring |
| `fish/table.py` | the lobby, seats and host-run game, as messages |
| `fish/bot.py` | the computer player |
| `fish/mesh.py` | players nearby, over the SDK |
| `fish/rpc.py`, `fish/service.py` | the SDK's local API and service |
| `fish/app.py`, `fish/cli.py` | the local web server and the `fish` command |
| `fish/web/` | the page |

## Tests

```bash
.venv/bin/pytest                 # rules, protocol, and three real SDK services
FISH_SLOW=1 .venv/bin/pytest     # adds a full 6-player game over real services,
                                 # with a player and the host restarting mid-game
```

## Limits

- **Players have to trust the host.** The host's laptop knows every hand.
  That's fine among friends, but it isn't cheat-proof.
- **No new players mid-game.** Only people already seated can come back to
  a game in progress.
- **Bluetooth hasn't been tried with a full table.** Six or eight laptops
  have only been tested over loopback.
