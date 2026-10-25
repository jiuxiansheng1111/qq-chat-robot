"""Exact shape calculations, not a winning or deal-in probability model."""

from collections import Counter
from functools import lru_cache

HONORS = {"E": "1z", "S": "2z", "W": "3z", "N": "4z", "P": "5z", "F": "6z", "C": "7z"}
ORPHANS = (0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33)


def tile(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("牌必须是 1m..9m / 1p..9p / 1s..9s / 1z..7z；赤五用 0m/0p/0s")  # noqa: TRY004
    value = HONORS.get(value, value)
    if len(value) == 3 and value[0:2] in {"5m", "5p", "5s"} and value[2] == "r":
        value = "0" + value[1]
    if (
        len(value) != 2
        or value[1] not in "mpsz"
        or value[0] not in "0123456789"
        or (value[1] == "z" and value[0] not in "1234567")
    ):
        raise ValueError("非法牌编码")
    return value


def index(value: str) -> int:
    value = tile(value)
    return "mpsz".index(value[1]) * 9 + (4 if value[0] == "0" else int(value[0]) - 1)


def name(number: int) -> str:
    return str(number % 9 + 1) + "mpsz"[number // 9]


def counts(hand: tuple[str, ...] | list[str]) -> tuple[int, ...]:
    result = [0] * 34
    for value in hand:
        result[index(value)] += 1
    if max(result) > 4:
        raise ValueError("同一种牌超过四枚")
    return tuple(result)


@lru_cache(maxsize=100_000)
def _forms(part: tuple[int, ...], honor: bool = False) -> frozenset[tuple[int, int, int]]:
    """Memoized suit partitions (melds, taatsu, pair), avoiding exponential rework."""
    first = next((k for k, n in enumerate(part) if n), len(part))
    if first == len(part):
        return frozenset({(0, 0, 0)})
    result = set()

    def branch(used: tuple[int, ...], meld: int, partial: int, head: int) -> None:
        work = list(part)
        for k in used:
            work[k] -= 1
        for m, t, p in _forms(tuple(work), honor):
            if m + meld <= 4 and t + partial <= 4 and p + head <= 1:
                result.add((m + meld, t + partial, p + head))

    branch((first,), 0, 0, 0)
    if part[first] >= 3:
        branch((first,) * 3, 1, 0, 0)
    if part[first] >= 2:
        branch((first,) * 2, 0, 1, 0)
        branch((first,) * 2, 0, 0, 1)
    if not honor:
        if first < 7 and part[first + 1] and part[first + 2]:
            branch((first, first + 1, first + 2), 1, 0, 0)
        for step in (1, 2):
            if first + step < 9 and part[first + step]:
                branch((first, first + step), 0, 1, 0)
    return frozenset(result)


@lru_cache(maxsize=100_000)
def shanten_counts(hand: tuple[int, ...], open_melds: int = 0) -> int:
    """Min of standard / seven pairs / thirteen orphans; open hands use standard only."""
    states = {(open_melds, 0, 0)}
    for start, end in ((0, 9), (9, 18), (18, 27), (27, 34)):
        states = {
            (a + m, min(4, b + t), c + p)
            for a, b, c in states
            for m, t, p in _forms(hand[start:end], start == 27)
            if a + m <= 4 and c + p <= 1
        }
    best = min(8 - 2 * m - min(t, 4 - m) - p for m, t, p in states)
    if not open_melds:
        pairs = sum(n >= 2 for n in hand)
        unique = sum(n > 0 for n in hand)
        seven = 6 - pairs + max(0, 7 - unique)
        orphan = 13 - sum(hand[k] > 0 for k in ORPHANS) - any(hand[k] >= 2 for k in ORPHANS)
        best = min(best, seven, orphan)
    return best


def shanten(hand: tuple[str, ...] | list[str], open_melds: int = 0) -> int:
    return shanten_counts(counts(hand), open_melds)


def improvements(
    hand: tuple[str, ...], melds: int, visible: tuple[int, ...]
) -> tuple[tuple[str, int], ...]:
    base = counts(hand)
    current = shanten_counts(base, melds)
    result = []
    for k in range(34):
        remaining = 4 - visible[k]
        if remaining <= 0 or base[k] == 4:
            continue
        candidate = list(base)
        candidate[k] += 1
        if shanten_counts(tuple(candidate), melds) < current:
            result.append((name(k), remaining))
    return tuple(result)


def waits(hand: tuple[str, ...], melds: int = 0) -> frozenset[int]:
    base = counts(hand)
    found = set()
    for k in range(34):
        if base[k] < 4:
            candidate = list(base)
            candidate[k] += 1
            if shanten_counts(tuple(candidate), melds) == -1:
                found.add(k)
    return frozenset(found)


@lru_cache(maxsize=10_000)
def shapes(hand: tuple[int, ...], melds: int) -> frozenset[tuple[tuple[str, int], ...]]:
    """All standard completed decompositions, used for conservative post-riichi kan checks."""
    work = list(hand)
    result = set()

    def visit(groups: tuple[tuple[str, int], ...], paired: bool) -> None:
        first = next((k for k, n in enumerate(work) if n), 34)
        if first == 34:
            if paired and len(groups) == 5 - melds:
                result.add(tuple(sorted(groups)))
            return
        if not paired and work[first] >= 2:
            work[first] -= 2
            visit(groups + (("pair", first),), True)
            work[first] += 2
        if work[first] >= 3:
            work[first] -= 3
            visit(groups + (("triplet", first),), paired)
            work[first] += 3
        if first < 27 and first % 9 < 7 and work[first + 1] and work[first + 2]:
            for k in (first, first + 1, first + 2):
                work[k] -= 1
            visit(groups + (("sequence", first),), paired)
            for k in (first, first + 1, first + 2):
                work[k] += 1

    visit((), False)
    return frozenset(result)


def riichi_ankan_allowed(
    hand: tuple[str, ...], drawn: str | None, kan_tile: str, melds: int
) -> bool:
    if drawn is None or index(drawn) != index(kan_tile):
        return False
    before = list(hand)
    before.remove(drawn)
    after = list(hand)
    kan_index = index(kan_tile)
    if sum(index(t) == kan_index for t in after) != 4:
        return False
    after = [t for t in after if index(t) != kan_index]
    old_waits = waits(tuple(before), melds)
    if not old_waits or old_waits != waits(tuple(after), melds + 1):
        return False
    for winning in old_waits:
        complete = list(counts(tuple(before)))
        complete[winning] += 1
        decompositions = shapes(tuple(complete), melds)
        if not decompositions or any(("triplet", kan_index) not in s for s in decompositions):
            return False
        reduced = {tuple(g for g in s if g != ("triplet", kan_index)) for s in decompositions}
        completed_after = list(counts(tuple(after)))
        completed_after[winning] += 1
        if reduced != set(shapes(tuple(completed_after), melds + 1)):
            return False
    return True


def dora_count(hand: tuple[str, ...], indicators: tuple[str, ...]) -> int:
    held = Counter(index(t) for t in hand)
    total = sum(t[0] == "0" for t in hand)
    for indicator in indicators:
        k = index(indicator)
        if k < 27:
            dora = k // 9 * 9 + (k % 9 + 1) % 9
        elif k < 31:
            dora = 27 + (k - 27 + 1) % 4
        else:
            dora = 31 + (k - 31 + 1) % 3
        total += held[dora]
    return total
