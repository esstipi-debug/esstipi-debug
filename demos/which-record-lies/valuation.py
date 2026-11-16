"""Before and after, in orders and in BRL (PRE-N0-01, section 4).

R0, the naive rule: a delivered order with a "not received" review is a delivery dispute.
R1, the triage of which_record_lies.py, unchanged: only the conflicts are.
Disputes avoided = R0 - R1 = the claims classed as late.
"""

from __future__ import annotations

import pandas as pd

import which_record_lies as wrl

CLASSES = (wrl.LATE, wrl.FLAGGED, wrl.INDETERMINATE)


def claim_classes(
    order_table: pd.DataFrame,
    *,
    alpha: float = wrl.DEFAULT_ALPHA,
    min_orders: int = wrl.DEFAULT_MIN_ORDERS,
) -> pd.DataFrame:
    """One row per claim: order_id and its class, sorted by order_id."""
    sellers = wrl.flag_sellers(order_table, alpha=alpha, min_orders=min_orders)
    flagged = set(sellers.index[sellers["flagged"]])
    labels = wrl.classify_claims(order_table, flagged)
    claims = pd.DataFrame({
        "order_id": order_table.loc[labels.index, "order_id"].to_numpy(),
        "klass": labels.to_numpy(),
    })
    return claims.sort_values("order_id", kind="stable").reset_index(drop=True)


def order_values(items: pd.DataFrame) -> pd.Series:
    """BRL per order: price + freight_value of every item, summed by order_id.

    A value that is not a number raises instead of counting as zero."""
    value = pd.to_numeric(items["price"]) + pd.to_numeric(items["freight_value"])
    return value.groupby(items["order_id"]).sum()


def counterfactual(claims: pd.DataFrame, items: pd.DataFrame) -> pd.DataFrame:
    """One row per class plus the three totals. An order without items is worth 0 BRL."""
    if claims.empty:
        raise ValueError("no claims to value")
    valued = claims.assign(value_brl=claims["order_id"].map(order_values(items)).fillna(0.0))
    total_orders, total_value = len(valued), float(valued["value_brl"].sum())

    def row(name: str, part: pd.DataFrame) -> dict:
        orders, value = len(part), float(part["value_brl"].sum())
        return {
            "row": name,
            "orders": orders,
            "share_of_claims": orders / total_orders,
            "value_brl": value,
            "share_of_value": value / total_value if total_value else 0.0,
        }

    by_class = {klass: valued[valued["klass"] == klass] for klass in CLASSES}
    rows = [row(klass, by_class[klass]) for klass in CLASSES]
    rows.append(row("R0_naive_disputes", valued))
    rows.append(row("R1_conflicts_after_triage", valued[valued["klass"] != wrl.LATE]))
    rows.append(row("avoided_R0_minus_R1", by_class[wrl.LATE]))
    return pd.DataFrame(rows)
