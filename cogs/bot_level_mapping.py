"""Shared game-data constants used across cogs.

Single source of truth for the furnace / Fire-Crystal (FC) level table so cogs
reference one mapping instead of each duplicating it.
"""

# Raw API stove level -> in-game display name. Levels <= 30 are plain numbers
# and intentionally aren't listed (callers fall back to "Level N").
LEVEL_MAPPING = {
    31: "30-1", 32: "30-2", 33: "30-3", 34: "30-4",
    35: "FC 1", 36: "FC 1 - 1", 37: "FC 1 - 2", 38: "FC 1 - 3", 39: "FC 1 - 4",
    40: "FC 2", 41: "FC 2 - 1", 42: "FC 2 - 2", 43: "FC 2 - 3", 44: "FC 2 - 4",
    45: "FC 3", 46: "FC 3 - 1", 47: "FC 3 - 2", 48: "FC 3 - 3", 49: "FC 3 - 4",
    50: "FC 4", 51: "FC 4 - 1", 52: "FC 4 - 2", 53: "FC 4 - 3", 54: "FC 4 - 4",
    55: "FC 5", 56: "FC 5 - 1", 57: "FC 5 - 2", 58: "FC 5 - 3", 59: "FC 5 - 4",
    60: "FC 6", 61: "FC 6 - 1", 62: "FC 6 - 2", 63: "FC 6 - 3", 64: "FC 6 - 4",
    65: "FC 7", 66: "FC 7 - 1", 67: "FC 7 - 2", 68: "FC 7 - 3", 69: "FC 7 - 4",
    70: "FC 8", 71: "FC 8 - 1", 72: "FC 8 - 2", 73: "FC 8 - 3", 74: "FC 8 - 4",
    75: "FC 9", 76: "FC 9 - 1", 77: "FC 9 - 2", 78: "FC 9 - 3", 79: "FC 9 - 4",
    80: "FC 10", 81: "FC 10 - 1", 82: "FC 10 - 2", 83: "FC 10 - 3", 84: "FC 10 - 4",
}

MAX_FURNACE_LEVEL = max(LEVEL_MAPPING)
MAX_STATE = 99999           # ~4 digits today; 5 leaves headroom without accepting junk


def _squash(text: str) -> str:
    """Comparison key, so 'FC 10 - 2' == 'fc10-2' == 'FC102'."""
    return "".join(c for c in str(text).upper() if c.isalnum())


_LEVEL_LOOKUP = {_squash(name): lv for lv, name in LEVEL_MAPPING.items()}


def format_furnace_level(raw) -> str:
    """Display name for a raw API stove level (e.g. 80 -> 'FC 10'); <= 30 -> 'Level N'."""
    try:
        lv = int(raw)
    except (TypeError, ValueError):
        return str(raw)
    if lv > 30:
        return LEVEL_MAPPING.get(lv, f"Level {lv}")
    return f"Level {lv}"


def parse_furnace_level(text):
    """Raw stove level from a display name ('FC 10 - 2') or a number ('82'), else None."""
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    if raw.isdigit():
        lv = int(raw)
        return lv if 0 <= lv <= MAX_FURNACE_LEVEL else None
    squashed = _squash(raw)
    if squashed in _LEVEL_LOOKUP:
        return _LEVEL_LOOKUP[squashed]
    # "Level 25" - format_furnace_level's pre-FC output.
    if squashed.startswith("LEVEL"):
        digits = squashed[len("LEVEL"):]
        if digits.isdigit() and int(digits) <= MAX_FURNACE_LEVEL:
            return int(digits)
    return None


def parse_state(text):
    """A typed state as an int in 1..MAX_STATE, or None."""
    raw = str(text).strip() if text is not None else ""
    return int(raw) if raw.isdigit() and 1 <= int(raw) <= MAX_STATE else None
