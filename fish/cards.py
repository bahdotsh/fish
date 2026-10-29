"""The Fish deck: 54 cards in 9 sets of 6.

Cards are short strings: rank then suit ("2S", "10H", "QD"), plus the two
jokers "WJ" (white) and "BJ" (black).
"""

SUITS = ("S", "H", "D", "C")
SUIT_NAMES = {"S": "Spades", "H": "Hearts", "D": "Diamonds", "C": "Clubs"}
LOW_RANKS = ("2", "3", "4", "5", "6", "7")
HIGH_RANKS = ("9", "10", "J", "Q", "K", "A")
JOKERS = ("WJ", "BJ")


def _build_sets() -> dict[str, tuple[str, ...]]:
    sets: dict[str, tuple[str, ...]] = {}
    for suit in SUITS:
        sets[f"low-{suit}"] = tuple(rank + suit for rank in LOW_RANKS)
    sets["eights"] = tuple("8" + suit for suit in SUITS) + JOKERS
    for suit in SUITS:
        sets[f"high-{suit}"] = tuple(rank + suit for rank in HIGH_RANKS)
    return sets


SETS = _build_sets()
SET_NAMES = {
    **{f"low-{s}": f"Low {SUIT_NAMES[s]}" for s in SUITS},
    "eights": "Eights & Jokers",
    **{f"high-{s}": f"High {SUIT_NAMES[s]}" for s in SUITS},
}
DECK = tuple(card for cards in SETS.values() for card in cards)
SET_OF = {card: set_id for set_id, cards in SETS.items() for card in cards}
# Position in DECK, for sorting a hand into set order.
ORDER = {card: i for i, card in enumerate(DECK)}

assert len(DECK) == 54 and len(SET_OF) == 54


def sort_cards(cards) -> list[str]:
    return sorted(cards, key=ORDER.__getitem__)
