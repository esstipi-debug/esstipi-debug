"""Tests that close the three mutants that survived the PRE-N0-01 run of 2026-11-21 (M3, M4, M5).

The run reported 2 of 5 one-line mutants caught by the 106 tests. Each test here kills one survivor:
M3 compares timestamps instead of days, M4 moves the default alpha, M5 averages the base rate per seller.
"""

from __future__ import annotations

import which_record_lies as wrl
from test_which_record_lies import DELIVERED_AT, flags, marketplace, order_table, spec


def test_a_claim_later_on_the_delivery_day_is_still_late_not_a_conflict():
    """M3: delivery recorded at 12:00, claim written at 18:00 the same day. Day granularity says late."""
    assert DELIVERED_AT.startswith("2018-01-10 12:00")
    table = order_table([spec("a", reviews=(("Não recebi", "2018-01-10 18:00:00"),))])
    assert flags(table, "a") == (True, False)


def test_the_default_alpha_is_the_pre_registered_one():
    """M4: PRE-N0-01 fixes alpha at 0.01; the sensitivity to 0.001 and 0.05 is reported apart."""
    assert wrl.DEFAULT_ALPHA == 0.01


def test_the_base_rate_is_conflicts_over_all_orders_not_an_average_of_sellers():
    """M5: with sellers of different size, the per-order rate and the mean of per-seller rates differ.

    600 orders and 20 conflicts give 1/30 per order; the mean of the five seller rates is 0.08.
    """
    plan = {"big": (400, 0), **{f"s{i}": (50, 5) for i in range(4)}}
    table = wrl.build_order_table(*marketplace(plan, late_claims=0))
    sellers = wrl.flag_sellers(table, alpha=0.01, min_orders=50)
    per_order = float(table["conflict"].mean())
    assert per_order == 20 / 600
    for seller in (f"s{i}" for i in range(4)):
        assert sellers.loc[seller, "threshold"] == wrl.poisson_upper_quantile(50 * per_order, 0.01)
