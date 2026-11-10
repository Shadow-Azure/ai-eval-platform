"""Money and the platform billing currency (M5-10).

Every fee the platform stores, compares, returns or displays is denominated in
one currency: ``CNY``. Provider quotes that are already in CNY (Aliyun ECS
spot) are used as-is; model pricing is configured directly in CNY per million
tokens. There is no runtime exchange-rate conversion.

``Money`` is the single amount type for spot prices, model usage, budgets and
cost estimates. It keeps amounts as :class:`~decimal.Decimal`, enforces the
billing currency and centralizes quantization/serialization so every module
rounds and formats fees the same way.

Rows written before M5-10 were denominated in USD. Migration
``0018_billing_cny`` converts them once at the documented 7.2 rate, so this
module never needs a conversion helper.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

BILLING_CURRENCY = "CNY"
BILLING_CURRENCY_SYMBOL = "¥"

# Database scales: ``model_usage.cost_cny``/``eval_run.model_budget_cny`` are
# Numeric(12, 6); spot prices are Numeric(10, 4).
MICRO = Decimal("0.000001")
UNIT_PRICE = Decimal("0.0001")
CENT = Decimal("0.01")


class MoneyError(ValueError):
    """A fee amount could not be parsed as a valid money value."""


@dataclass(frozen=True)
class Money:
    """An amount in the platform billing currency."""

    amount: Decimal
    currency: str = BILLING_CURRENCY

    def __post_init__(self) -> None:
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if self.currency != BILLING_CURRENCY:
            raise MoneyError(f"平台计费币种必须是 {BILLING_CURRENCY}")

    # -- construction -------------------------------------------------------

    @classmethod
    def of(cls, value: object = "0") -> Money:
        """Coerce a DB value, request field or plain number into ``Money``."""

        if isinstance(value, Money):
            return value
        if value is None:
            return cls.zero()
        try:
            return cls(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise MoneyError("费用金额必须是十进制数字") from exc

    @classmethod
    def zero(cls) -> Money:
        return cls(Decimal("0"))

    @classmethod
    def sum(cls, values: Iterable[object]) -> Money:
        total = cls.zero()
        for value in values:
            total = total + cls.of(value)
        return total

    # -- arithmetic ---------------------------------------------------------

    def quantize(self, places: Decimal = MICRO) -> Money:
        return Money(self.amount.quantize(places))

    def _combine(self, other: object, operation) -> Money:
        return Money(operation(self.amount, Money.of(other).amount))

    def __add__(self, other: object) -> Money:
        return self._combine(other, lambda left, right: left + right)

    def __sub__(self, other: object) -> Money:
        return self._combine(other, lambda left, right: left - right)

    def __mul__(self, factor: object) -> Money:
        return Money(self.amount * Decimal(str(factor)))

    __rmul__ = __mul__

    def __truediv__(self, divisor: object) -> Money:
        return Money(self.amount / Decimal(str(divisor)))

    def __neg__(self) -> Money:
        return Money(-self.amount)

    def __lt__(self, other: object) -> bool:
        return self.amount < Money.of(other).amount

    def __le__(self, other: object) -> bool:
        return self.amount <= Money.of(other).amount

    def __gt__(self, other: object) -> bool:
        return self.amount > Money.of(other).amount

    def __ge__(self, other: object) -> bool:
        return self.amount >= Money.of(other).amount

    # -- serialization ------------------------------------------------------

    def as_str(self, places: Decimal | None = None) -> str:
        """Plain decimal string for API payloads and storage."""

        amount = self.amount if places is None else self.amount.quantize(places)
        return format(amount, "f")

    def format(self, places: Decimal = CENT) -> str:
        """User-facing label, e.g. ``¥3.60``."""

        return f"{BILLING_CURRENCY_SYMBOL}{format(self.amount.quantize(places), 'f')}"


def money_str(value: object, places: Decimal | None = None) -> str | None:
    """``Money``/Decimal/None -> API string, preserving ``None``."""

    if value is None:
        return None
    return Money.of(value).as_str(places)


def money_value(value: object) -> Decimal:
    """Coerce to the ``Decimal`` a Numeric column stores."""

    return Money.of(value).amount
