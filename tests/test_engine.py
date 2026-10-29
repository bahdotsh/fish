import random

import pytest

from fish.cards import DECK, SETS
from fish.engine import Game, IllegalMove, team_of


def rigged(hands: dict[int, list[str]], players: int = 6, turn: int = 0) -> Game:
    """A game with chosen hands; seats not listed are empty."""
    game = Game(players, random.Random(0))
    game.hands = [set(hands.get(p, [])) for p in range(players)]
    game.turn = turn
    return game


def everyone_owns_a_set(players: int = 6) -> Game:
    """Each seat holds whole sets: seat p gets set p (and seats 0-2 an extra one in 6p)."""
    ids = list(SETS)
    hands = {p: [] for p in range(players)}
    for i, set_id in enumerate(ids):
        hands[i % players] += SETS[set_id]
    return rigged(hands, players)


# -- deck and deal -------------------------------------------------------------


def test_deck_has_nine_sets_of_six():
    assert len(SETS) == 9
    assert all(len(cards) == 6 for cards in SETS.values())
    assert len(set(DECK)) == 54
    assert set(SETS["eights"]) == {"8S", "8H", "8D", "8C", "WJ", "BJ"}


@pytest.mark.parametrize("players, sizes", [(6, [9] * 6), (8, [7] * 6 + [6] * 2)])
def test_deal(players, sizes):
    game = Game(players, random.Random(1))
    assert [len(h) for h in game.hands] == sizes
    assert set().union(*game.hands) == set(DECK)
    assert 0 <= game.turn < players


def test_only_six_or_eight_players():
    with pytest.raises(ValueError):
        Game(7)


def test_teams_alternate():
    assert [team_of(p) for p in range(6)] == [0, 1, 0, 1, 0, 1]


# -- asking --------------------------------------------------------------------


def test_successful_ask_moves_the_card_and_keeps_the_turn():
    game = rigged({0: ["2S"], 1: ["3S", "AH"]})
    result = game.ask(0, 1, "3S")
    assert result.got
    assert game.hands[0] == {"2S", "3S"} and game.hands[1] == {"AH"}
    assert game.turn == 0


def test_failed_ask_passes_the_turn_to_the_asked_player():
    game = rigged({0: ["2S"], 1: ["AH"]})
    assert not game.ask(0, 1, "3S").got
    assert game.turn == 1


def test_bluff_asking_for_your_own_card():
    game = rigged({0: ["2S"], 1: ["AH"]})
    assert not game.ask(0, 1, "2S").got
    assert game.hands[0] == {"2S"}
    assert game.turn == 1


@pytest.mark.parametrize(
    "asker, target, card, message",
    [
        (2, 1, "3S", "not your turn"),
        (0, 2, "3S", "other team"),
        (0, 3, "3S", "no cards"),
        (0, 1, "9S", "set you hold"),
        (0, 1, "ZZ", "No such card"),
        (0, 9, "3S", "No such player"),
    ],
)
def test_illegal_asks(asker, target, card, message):
    game = rigged({0: ["2S"], 1: ["AH"], 2: ["4S"]})
    with pytest.raises(IllegalMove, match=message):
        game.ask(asker, target, card)


def test_history_keeps_the_last_two_asks():
    game = rigged({0: ["2S"], 1: ["3S", "4S", "5S"]})
    for card in ("3S", "4S", "5S"):
        game.ask(0, 1, card)
    assert [a.card for a in game.history] == ["4S", "5S"]
    assert game.view(3)["history"][-1] == {"asker": 0, "target": 1, "card": "5S", "got": True}


# -- declaring -----------------------------------------------------------------


def low_spades(holders):
    return dict(zip(SETS["low-S"], holders))


def test_correct_declaration_wins_the_set_and_keeps_the_turn():
    game = rigged({0: ["2S", "3S", "4S", "AH"], 2: ["5S", "6S"], 4: ["7S"], 1: ["KD"]})
    result = game.declare(2, low_spades([0, 0, 0, 2, 2, 4]))
    assert result.correct and result.team == 0
    assert game.claimed == {"low-S": 0}
    assert game.hands[0] == {"AH"} and not game.hands[2] and not game.hands[4]
    assert game.turn == 0


def test_wrong_holder_in_own_team_gives_the_set_away_but_keeps_the_turn():
    game = rigged({0: ["2S", "3S", "4S", "AH"], 2: ["5S", "6S"], 4: ["7S"], 1: ["KD"]})
    result = game.declare(0, low_spades([0, 0, 0, 2, 4, 2]))
    assert not result.correct and result.team == 1
    assert game.claimed == {"low-S": 1}
    assert game.turn == 0


def test_card_with_the_other_team_gives_the_set_away_and_removes_their_card():
    game = rigged({0: ["2S", "3S", "4S", "AH"], 2: ["5S", "6S"], 1: ["7S", "KD"]})
    result = game.declare(0, low_spades([0, 0, 0, 2, 2, 0]))
    assert result.team == 1
    assert game.hands[1] == {"KD"}


def test_player_with_no_cards_can_declare_on_their_teams_turn():
    game = rigged({0: ["2S", "3S", "4S", "5S", "6S", "7S", "AH"], 1: ["KD"]})
    assert game.declare(2, low_spades([0] * 6)).correct


@pytest.mark.parametrize(
    "declarer, assignment, message",
    [
        (1, low_spades([0] * 6), "team's turn"),
        (0, dict(list(low_spades([0] * 6).items())[:5]), "all 6 cards"),
        (0, {**low_spades([0] * 5), "9S": 0}, "exactly one set"),
        (0, low_spades([0, 0, 0, 0, 0, 1]), "own team"),
    ],
)
def test_illegal_declarations(declarer, assignment, message):
    game = rigged({0: ["2S", "3S", "4S", "5S", "6S", "7S"], 1: ["KD"]})
    with pytest.raises(IllegalMove, match=message):
        game.declare(declarer, assignment)


def test_a_set_can_only_be_declared_once():
    game = rigged({0: ["2S", "3S", "4S", "5S", "6S", "7S", "AH"], 1: ["KD"]})
    game.declare(0, low_spades([0] * 6))
    with pytest.raises(IllegalMove, match="already"):
        game.declare(0, low_spades([0] * 6))


# -- the turn after cards leave play -------------------------------------------


def test_empty_turn_holder_must_pass_to_a_teammate():
    game = rigged({0: ["2S", "3S", "4S", "5S", "6S", "7S"], 2: ["AH"], 4: ["KH"], 1: ["KD"]})
    game.declare(0, low_spades([0] * 6))
    assert game.turn == 0 and game.must_pass
    with pytest.raises(IllegalMove, match="pass the turn"):
        game.ask(0, 1, "KD")
    with pytest.raises(IllegalMove, match="teammate"):
        game.pass_turn(0, 1)
    game.pass_turn(0, 4)
    assert game.turn == 4 and not game.must_pass


def test_cannot_pass_to_a_teammate_with_no_cards():
    game = rigged({0: ["2S", "3S", "4S", "5S", "6S", "7S"], 2: ["AH"], 1: ["KD"]})
    game.declare(0, low_spades([0] * 6))
    with pytest.raises(IllegalMove, match="no cards"):
        game.pass_turn(0, 4)


def test_team_out_of_cards_hands_the_turn_to_the_other_team():
    game = rigged({0: ["2S", "3S", "4S", "5S", "6S", "7S"], 3: ["KD"], 5: ["AH"]})
    game.declare(0, low_spades([0] * 6))
    assert game.turn == 3 and not game.must_pass


def test_team_with_no_cards_cannot_declare():
    game = rigged({0: ["2S", "3S", "4S", "5S", "6S", "7S"], 3: ["KD"]})
    game.declare(0, low_spades([0] * 6))
    assert game.turn == 3
    with pytest.raises(IllegalMove, match="team's turn"):
        game.declare(2, dict(zip(SETS["high-D"], [0] * 6)))


# -- end of game and views -----------------------------------------------------


def test_game_ends_when_all_sets_are_declared():
    game = everyone_owns_a_set()
    for i, (set_id, cards) in enumerate(SETS.items()):
        owner = i % 6
        game.turn = owner
        game.must_pass = False
        game.declare(owner, {card: owner for card in cards})
    assert game.over
    assert game.score() == (5, 4)  # sets 0,2,4,6,8 belong to team 0
    assert game.winner == 0
    with pytest.raises(IllegalMove, match="over"):
        game.declare(0, low_spades([0] * 6))


def test_view_shows_only_your_own_hand():
    game = Game(6, random.Random(2))
    view = game.view(3)
    assert view["hand"] == sorted(view["hand"], key=list(DECK).index)
    assert set(view["hand"]) == game.hands[3]
    assert view["counts"] == [9] * 6
    assert "hands" not in view
    assert view["winner"] is None


def test_random_games_always_finish_with_cards_conserved():
    """Players ask and declare at random; the game must always reach an end."""
    for seed in range(200):
        rng = random.Random(seed)
        game = Game(rng.choice([6, 8]), rng)
        for _ in range(5000):
            if game.over:
                break
            in_play = sum(len(h) for h in game.hands)
            assert in_play == 54 - 6 * len(game.claimed)
            me = game.turn
            if game.must_pass:
                mates = [p for p in range(game.players) if team_of(p) == team_of(me) and game.hands[p] and p != me]
                game.pass_turn(me, rng.choice(mates))
                continue
            team = [p for p in range(game.players) if team_of(p) == team_of(me)]
            opponents = [p for p in range(game.players) if team_of(p) != team_of(me) and game.hands[p]]
            if not opponents or rng.random() < 0.1:
                set_id = rng.choice([s for s in SETS if s not in game.claimed])
                game.declare(rng.choice(team), {c: rng.choice(team) for c in SETS[set_id]})
            else:
                held_sets = {s for s, cards in SETS.items() if game.hands[me] & set(cards)}
                card = rng.choice([c for s in held_sets for c in SETS[s]])
                game.ask(me, rng.choice(opponents), card)
        assert game.over, f"seed {seed} did not finish"
        assert sum(game.score()) == 9


def test_a_saved_game_restores_exactly():
    game = Game(8, random.Random(3))
    me = game.turn
    held = next(iter(game.hands[me]))
    game.ask(me, (me + 1) % 8, held)
    copy = Game.from_dict(game.to_dict())
    assert copy.to_dict() == game.to_dict()
    assert all(copy.view(p) == game.view(p) for p in range(8))


def test_the_public_view_shows_no_hand():
    game = Game(6, random.Random(4))
    view = game.view(None)
    assert view["hand"] == [] and view["you"] is None and view["team"] is None
    assert view["counts"] == [9] * 6
