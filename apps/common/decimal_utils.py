from decimal import Decimal, ROUND_HALF_UP

RUB_STEP = Decimal("0.01")
USDT_STEP = Decimal("0.000001")
PRICE_STEP = Decimal("0.000001")


def q_rub(value: Decimal) -> Decimal:
    return Decimal(value).quantize(RUB_STEP, rounding=ROUND_HALF_UP)


def q_usdt(value: Decimal) -> Decimal:
    return Decimal(value).quantize(USDT_STEP, rounding=ROUND_HALF_UP)


def q_price(value: Decimal) -> Decimal:
    return Decimal(value).quantize(PRICE_STEP, rounding=ROUND_HALF_UP)


def d(value) -> Decimal:
    if value is None:
        return Decimal("0")
    return Decimal(str(value))
