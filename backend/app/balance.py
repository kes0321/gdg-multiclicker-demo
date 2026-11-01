"""Single source for response values and Redis Lua growth tables."""

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, localcontext

MAX_LEVEL = 30

with localcontext() as context:
    context.prec = 100
    GOLD_PER_CLICK = tuple(
        int((Decimal("10") * Decimal("1.25") ** exponent).to_integral_value(
            rounding=ROUND_FLOOR
        ))
        for exponent in range(MAX_LEVEL)
    )
    UPGRADE_COST = tuple(
        int((Decimal("100") * Decimal("1.6") ** exponent).to_integral_value(
            rounding=ROUND_CEILING
        ))
        for exponent in range(MAX_LEVEL - 1)
    )


class InvalidPlayerLevel(ValueError):
    """Stored levels must be valid; never silently reset or clamp them."""


def balance_for_level(level: int) -> dict[str, int | None]:
    if type(level) is not int or not 1 <= level <= MAX_LEVEL:
        raise InvalidPlayerLevel(f"Level outside 1..{MAX_LEVEL}: {level!r}")
    return {
        "gold_per_click": GOLD_PER_CLICK[level - 1],
        "next_upgrade_cost": UPGRADE_COST[level - 1] if level < MAX_LEVEL else None,
    }


LUA_BALANCE_TABLES = (
    "local GOLD_PER_CLICK = {" + ",".join(map(str, GOLD_PER_CLICK)) + "}\n"
    "local UPGRADE_COST = {" + ",".join(map(str, UPGRADE_COST)) + "}\n"
    "local MAX_LEVEL = #GOLD_PER_CLICK\n"
)
