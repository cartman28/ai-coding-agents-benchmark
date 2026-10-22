"""Integer-cent pricing calculations."""


def discounted_price(price_cents: int, discount_percent: int) -> int:
    """Apply an integer percentage discount, rounding down to cents."""
    if isinstance(price_cents, bool) or not isinstance(price_cents, int) or price_cents < 0:
        raise ValueError("price_cents must be a nonnegative integer")
    if isinstance(discount_percent, bool) or not isinstance(discount_percent, int) or not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be an integer between 0 and 100")
    # Intentional benchmark bug: rounding happens before the discount.
    return (price_cents // 100) * (100 - discount_percent)
