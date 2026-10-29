"""A computer player that fills an empty seat.

It decides from `Game.view(seat)` alone, the same information a person in
that seat has, and remembers what the asks it sees reveal. It is meant to
keep a game moving, not to be good at it: after PATIENCE asks with no set
declared, it takes its best guess at one.
"""

from __future__ import annotations

import random

from .cards import SET_OF, SETS
from .engine import team_of

PATIENCE = 8


class Bot:
    def __init__(self, seat: int, rng: random.Random | None = None):
        self.seat = seat
        self.rng = rng or random.Random()
        # card -> seat last known to hold it, learned from the asks we see.
        self.known: dict[str, int] = {}
        self.asks_since_claim = 0
        self._claimed = 0

    def observe(self, view: dict) -> None:
        for ask in view["history"]:
            if ask["got"]:
                self.known[ask["card"]] = ask["asker"]
            elif self.known.get(ask["card"]) == ask["target"]:
                del self.known[ask["card"]]
        for set_id in view["claimed"]:
            for card in SETS[set_id]:
                self.known.pop(card, None)
        for card in view["hand"]:
            self.known[card] = self.seat
        if len(view["claimed"]) != self._claimed:
            self._claimed = len(view["claimed"])
            self.asks_since_claim = 0

    def act(self, view: dict) -> dict | None:
        """The action to take, or None when it is not this seat's move."""
        self.observe(view)
        if view["over"] or view["turn"] != self.seat:
            return None
        me, counts, players = self.seat, view["counts"], view["players"]
        mates = [p for p in range(players) if team_of(p) == team_of(me)]
        hand = set(view["hand"])

        if view["must_pass"]:
            return {"action": "pass", "to": self.rng.choice([p for p in mates if p != me and counts[p]])}

        # Declare a set we can place card by card.
        for set_id, cards in SETS.items():
            if set_id in view["claimed"]:
                continue
            places = {c: self.known.get(c) for c in cards}
            if all(p is not None and team_of(p) == team_of(me) for p in places.values()):
                return {"action": "declare", "assignment": places}

        opponents = [p for p in range(players) if team_of(p) != team_of(me) and counts[p]]
        if not opponents or self.asks_since_claim >= PATIENCE:
            return {"action": "declare", "assignment": self._guess(view, mates)}

        # Ask for a card we know an opponent has, else a random missing one.
        held_sets = {SET_OF[c] for c in hand}
        for card, holder in self.known.items():
            if SET_OF[card] in held_sets and holder in opponents:
                self.asks_since_claim += 1
                return {"action": "ask", "target": holder, "card": card}
        wanted = [c for s in held_sets for c in SETS[s]
                  if c not in hand and self.known.get(c) not in mates]
        card = self.rng.choice(wanted or sorted(hand))
        self.asks_since_claim += 1
        return {"action": "ask", "target": self.rng.choice(opponents), "card": card}

    def _guess(self, view: dict, mates: list[int]) -> dict[str, int]:
        """The open set we know most of on our team, unknown cards spread
        over teammates who still hold cards."""
        open_sets = [s for s in SETS if s not in view["claimed"]]
        set_id = max(open_sets, key=lambda s: sum(self.known.get(c) in mates for c in SETS[s]))
        holders = [p for p in mates if view["counts"][p]] or mates
        self.asks_since_claim = 0
        return {c: self.known[c] if self.known.get(c) in mates else self.rng.choice(holders)
                for c in SETS[set_id]}
