"""The rules of Fish, with no networking.

Players sit in a circle, numbered 0..n-1. Player i is on team i % 2, so
teammates sit alternately. The host owns one Game; every player sees only
what `view(player)` gives them.
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass

from .cards import DECK, SET_OF, SETS, sort_cards

PLAYER_COUNTS = (6, 8)
HISTORY_LENGTH = 2


class IllegalMove(Exception):
    """A move the rules do not allow. The message is shown to the player."""


@dataclass(frozen=True)
class Ask:
    asker: int
    target: int
    card: str
    got: bool


@dataclass(frozen=True)
class Declaration:
    declarer: int
    set_id: str
    correct: bool
    team: int  # the team that won the set


def team_of(player: int) -> int:
    return player % 2


class Game:
    def __init__(self, players: int, rng: random.Random | None = None):
        if players not in PLAYER_COUNTS:
            raise ValueError(f"Fish needs 6 or 8 players, not {players}")
        rng = rng or random.Random()
        deck = list(DECK)
        rng.shuffle(deck)
        self.players = players
        # Round-robin deal: with 8 players, seats 0-5 get 7 and seats 6-7 get 6.
        self.hands: list[set[str]] = [set(deck[i::players]) for i in range(players)]
        self.turn = rng.randrange(players)
        # True when the turn holder has no cards and must hand the turn to a teammate.
        self.must_pass = False
        self.claimed: dict[str, int] = {}
        self.history: deque[Ask] = deque(maxlen=HISTORY_LENGTH)
        self.last_declaration: Declaration | None = None

    # -- queries -----------------------------------------------------------

    @property
    def over(self) -> bool:
        return len(self.claimed) == len(SETS)

    def score(self) -> tuple[int, int]:
        teams = list(self.claimed.values())
        return teams.count(0), teams.count(1)

    @property
    def winner(self) -> int | None:
        if not self.over:
            return None
        a, b = self.score()
        return 0 if a > b else 1  # 9 sets, so never a tie

    def holder(self, card: str) -> int | None:
        for player, hand in enumerate(self.hands):
            if card in hand:
                return player
        return None

    def team_cards(self, team: int) -> int:
        return sum(len(h) for p, h in enumerate(self.hands) if team_of(p) == team)

    # -- moves -------------------------------------------------------------

    def ask(self, asker: int, target: int, card: str) -> Ask:
        """`asker` asks `target` for `card`. Returns what happened."""
        self._check_player(asker)
        self._check_player(target)
        if self.over:
            raise IllegalMove("The game is over")
        if asker != self.turn:
            raise IllegalMove("It is not your turn")
        if self.must_pass:
            raise IllegalMove("You have no cards: pass the turn to a teammate")
        if team_of(target) == team_of(asker):
            raise IllegalMove("You can only ask a player on the other team")
        if not self.hands[target]:
            raise IllegalMove("That player has no cards")
        if card not in SET_OF:
            raise IllegalMove(f"No such card: {card}")
        set_id = SET_OF[card]
        if not any(SET_OF[c] == set_id for c in self.hands[asker]):
            raise IllegalMove("You can only ask for a card from a set you hold a card of")

        got = card in self.hands[target]
        if got:
            self.hands[target].remove(card)
            self.hands[asker].add(card)
        else:
            self.turn = target
        result = Ask(asker, target, card, got)
        self.history.append(result)
        return result

    def declare(self, declarer: int, assignment: dict[str, int]) -> Declaration:
        """`declarer` names which teammate holds each card of one set.

        Right or wrong, the set's cards leave play and the turn stays where
        it is. If any card is not where the declarer said, the other team
        wins the set.
        """
        self._check_player(declarer)
        if self.over:
            raise IllegalMove("The game is over")
        team = team_of(declarer)
        if team != team_of(self.turn):
            raise IllegalMove("You can only declare on your team's turn")
        set_ids = {SET_OF.get(card) for card in assignment}
        if None in set_ids or len(set_ids) != 1:
            raise IllegalMove("A declaration must name the cards of exactly one set")
        set_id = set_ids.pop()
        if set_id in self.claimed:
            raise IllegalMove("That set has already been declared")
        if set(assignment) != set(SETS[set_id]):
            raise IllegalMove("Name a holder for all 6 cards of the set")
        for player in assignment.values():
            self._check_player(player)
            if team_of(player) != team:
                raise IllegalMove("You can only name players on your own team")

        correct = all(card in self.hands[player] for card, player in assignment.items())
        winner = team if correct else 1 - team
        for hand in self.hands:
            hand.difference_update(SETS[set_id])
        self.claimed[set_id] = winner
        result = Declaration(declarer, set_id, correct, winner)
        self.last_declaration = result
        self._settle_turn()
        return result

    def pass_turn(self, player: int, to: int) -> None:
        """A turn holder with no cards hands the turn to a teammate who has some."""
        self._check_player(player)
        self._check_player(to)
        if player != self.turn or not self.must_pass:
            raise IllegalMove("You can only pass the turn when it is yours and you have no cards")
        if team_of(to) != team_of(player) or to == player:
            raise IllegalMove("Pass the turn to a teammate")
        if not self.hands[to]:
            raise IllegalMove("That teammate has no cards")
        self.turn = to
        self.must_pass = False

    def _settle_turn(self) -> None:
        """Keep the turn with someone who can use it after cards leave play."""
        self.must_pass = False
        if self.over or self.hands[self.turn]:
            return
        team = team_of(self.turn)
        if self.team_cards(team):
            self.must_pass = True
            return
        # The whole team is out: the next player round the circle with cards
        # takes the turn. Only the other team has cards now.
        for step in range(1, self.players):
            player = (self.turn + step) % self.players
            if self.hands[player]:
                self.turn = player
                return

    def _check_player(self, player: int) -> None:
        if not isinstance(player, int) or not 0 <= player < self.players:
            raise IllegalMove(f"No such player: {player}")

    # -- what each player may see -------------------------------------------

    def view(self, player: int) -> dict:
        """Everything `player` is allowed to know, as plain JSON-ready data."""
        self._check_player(player)
        last = self.last_declaration
        return {
            "you": player,
            "team": team_of(player),
            "players": self.players,
            "hand": sort_cards(self.hands[player]),
            "counts": [len(h) for h in self.hands],
            "turn": self.turn,
            "must_pass": self.must_pass,
            "claimed": dict(self.claimed),
            "score": list(self.score()),
            "history": [vars(a) for a in self.history],
            "last_declaration": vars(last) if last else None,
            "over": self.over,
            "winner": self.winner,
        }
