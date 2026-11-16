"""Negative control of PRE-N0-01 (section 7): how much separation do the dates give against chance?

The texts of the reviews are shuffled among the reviews of orders delivered in the same month, with a fixed
seed, and the triage is run again. The dates stay with their orders, so the share that lands in "late" is what
a random pairing of text and date produces. It is reported next to the real share, with no threshold.
"""

from __future__ import annotations

import random

import pandas as pd

import valuation
import which_record_lies as wrl

TEXT_COLUMNS = ["review_comment_title", "review_comment_message"]


def permute_texts(orders: pd.DataFrame, reviews: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Reviews with title and message permuted together within each delivery month of their order."""
    month = orders.set_index("order_id")["order_delivered_customer_date"].str[:7]
    out = reviews.sort_values(["order_id", "review_id"], kind="stable").reset_index(drop=True)
    out["_month"] = out["order_id"].map(month)
    rng = random.Random(seed)
    for value in sorted(out["_month"].dropna().unique()):
        rows = out.index[out["_month"] == value].to_numpy()
        shuffled = list(range(len(rows)))
        rng.shuffle(shuffled)
        out.loc[rows, TEXT_COLUMNS] = out.loc[rows[shuffled], TEXT_COLUMNS].to_numpy()
    return out.drop(columns="_month")


def negative_control(
    orders: pd.DataFrame,
    reviews: pd.DataFrame,
    items: pd.DataFrame,
    *,
    seed: int,
    alpha: float = wrl.DEFAULT_ALPHA,
    min_orders: int = wrl.DEFAULT_MIN_ORDERS,
) -> dict:
    table = wrl.build_order_table(orders, permute_texts(orders, reviews, seed), items)
    claims = valuation.claim_classes(table, alpha=alpha, min_orders=min_orders)
    n = len(claims)
    late = int((claims["klass"] == wrl.LATE).sum())
    return {
        "claims": n,
        "late_share": round(late / n, 6) if n else None,
        "conflict_share": round(1 - late / n, 6) if n else None,
    }
