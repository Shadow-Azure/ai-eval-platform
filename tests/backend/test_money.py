"""Money and CNY billing helpers (M5-10).

These pin the single amount representation used by spot pricing, model spend,
budgets and cost estimates: the billing currency, quantization at the DB
scales, API serialization and cost-estimate denomination.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.money import BILLING_CURRENCY, Money, MoneyError, money_str, money_value


def test_billing_currency_is_cny() -> None:
    assert BILLING_CURRENCY == "CNY"
    assert Money.of(1).currency == "CNY"


def test_money_preserves_stored_scale_when_serialized() -> None:
    # Matches the precision an API previously returned for Numeric(10,4).
    assert money_str(Decimal("0.5000")) == "0.5000"
    assert money_str(Decimal("3.600000")) == "3.600000"


def test_money_quantizes_and_sums_mixed_values() -> None:
    total = Money.sum([Decimal("0.420000"), "0.18", 0.06])
    assert total == Money.of("0.66")
    assert total.quantize(Decimal("0.000001")).as_str() == "0.660000"


def test_money_arithmetic_and_comparison() -> None:
    price = Money.of("3.6000")
    assert (price * 2).as_str() == "7.2000"
    assert (price / 2).as_str() == "1.8000"
    assert price > "0.7200"
    assert price - "0.6000" == Money.of("3.0000")
    assert Money.of("-1") < 0


def test_money_rejects_other_currencies_and_invalid_amounts() -> None:
    with pytest.raises(MoneyError):
        Money(Decimal("1"), currency="USD")
    with pytest.raises(MoneyError):
        Money.of("not-a-number")


def test_money_str_and_value_keep_none_semantics() -> None:
    assert money_str(None) is None
    assert money_str("1.5000") == "1.5000"
    assert money_value("2.25") == Decimal("2.25")


def test_cost_estimate_is_denominated_in_cny() -> None:
    from app.config import Settings
    from app.models import ResourcePool
    from app.services.preparations import estimate_cost

    settings = Settings()
    pool = ResourcePool(price_cny=Decimal("3.6000"))
    result = estimate_cost(
        pricing={"input_per_1m": 1.008, "output_per_1m": 2.016},
        case_count=6,
        effective_concurrency=2,
        pool=pool,
        settings=settings,
    )

    assert result is not None
    assert result["currency"] == "CNY"
    assert set(result["model_cny"]) == {"low", "high"}
    assert set(result["total_cny"]) == {"low", "high"}
    assert result["runner_cny"] is not None
    # The estimate text carries the CNY symbol instead of "$".
    assert "¥" in result["basis"]
    assert "$" not in result["basis"]
