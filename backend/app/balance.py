"""Exact growth values with a shared gameplay level cap."""

from decimal import Decimal, Overflow, ROUND_CEILING, ROUND_FLOOR, localcontext
from functools import lru_cache

MAX_SAFE_INTEGER = 2**53 - 1
MAX_LEVEL = 40
GOLD_BASE, GOLD_GROWTH = Decimal("10"), Decimal("1.25")
COST_BASE, COST_GROWTH = Decimal("100"), Decimal("1.6")


class InvalidPlayerLevel(ValueError):
    """Stored levels must be valid; never silently reset or clamp them."""


class NumericLimitExceeded(ValueError):
    """The current Lua/JavaScript number representation cannot be exact."""


@lru_cache(maxsize=128)
def balance_for_level(level: int) -> dict[str, int | None]:
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise InvalidPlayerLevel(f"Level must be within 1..{MAX_LEVEL}: {level!r}")
    with localcontext() as context:
        # Enough digits for exact powers, including levels beyond the old table.
        context.prec = max(100, 3 * level + 10)
        return {
            "gold_per_click": int((GOLD_BASE * GOLD_GROWTH ** (level - 1)).to_integral_value(rounding=ROUND_FLOOR)),
            "next_gold_per_click": int((GOLD_BASE * GOLD_GROWTH ** level).to_integral_value(rounding=ROUND_FLOOR)) if level < MAX_LEVEL else None,
            "next_upgrade_cost": int((COST_BASE * COST_GROWTH ** (level - 1)).to_integral_value(rounding=ROUND_CEILING)) if level < MAX_LEVEL else None,
        }


def safe_balance_for_level(level: int) -> dict[str, int | None]:
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise InvalidPlayerLevel(f"Level must be within 1..{MAX_LEVEL}: {level!r}")
    # Reject unrepresentable legacy data before allocating huge exact powers.
    try:
        with localcontext() as context:
            context.prec = 32
            if COST_BASE * COST_GROWTH ** (level - 1) > MAX_SAFE_INTEGER:
                raise NumericLimitExceeded(f"Balance exceeds exact numeric range at level {level}")
    except Overflow as exc:
        raise NumericLimitExceeded(f"Balance exceeds exact numeric range at level {level}") from exc
    balance = balance_for_level(level)
    if any(value is not None and value > MAX_SAFE_INTEGER for value in balance.values()):
        raise NumericLimitExceeded(f"Balance exceeds exact numeric range at level {level}")
    return balance


LUA_BALANCE_HELPER = f"local MAX_SAFE_INTEGER = {MAX_SAFE_INTEGER}\nlocal MAX_LEVEL = {MAX_LEVEL}\n" + """
local function read_balance(level, payload)
    local balance = cjson.decode(payload or "{}")
    if balance.level ~= level then return nil end
    return balance
end
"""
