"""Annex B of PRE-N0-01: four predictions about Olist delivery times, written before anything is computed.

    python gestalt_b.py --data <Olist folder> --verify
    python gestalt_b.py --data <Olist folder> --predictions predictions.json

predictions.json, written and dated before this script is run, holds the four answers:

    {"B1": "approval" | "handoff" | "transit",      stage with the largest median duration
     "B2": "approval" | "handoff" | "transit",      stage with the largest share of the variance of the total
     "B3": 13.0,                                     % of delivered orders delivered after the estimated day
     "B4": "SP"}                                     seller state with the largest median total time

The script refuses to run without the predictions file: the order (predict first, compute after) is part of
the protocol. A hit is an exact match for B1, B2 and B4 and within 2 points for B3. Success is 3 hits of 4.

Stages, in days: approval = approved - purchase; handoff = carrier - approved; transit = customer - carrier.
Total = customer - purchase. Only delivered orders with all four timestamps count. B2 uses
cov(stage, total) / var(total), which adds up to 1 over the three stages. B3 compares days, as the triage does.
B4 uses the seller of the order's first item and the states with at least 1,000 such orders.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

import prereg
import run_n0

STAGES = ("approval", "handoff", "transit")
TIMESTAMPS = [
    "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
    "order_delivered_customer_date", "order_estimated_delivery_date",
]
USECOLS = {
    "olist_orders_dataset.csv": ["order_id", "order_status", *TIMESTAMPS],
    "olist_order_items_dataset.csv": ["order_id", "order_item_id", "seller_id"],
    "olist_sellers_dataset.csv": ["seller_id", "seller_state"],
}
SECONDS_PER_DAY = 86400.0


def _days(later: pd.Series, earlier: pd.Series) -> pd.Series:
    return (later - earlier).dt.total_seconds() / SECONDS_PER_DAY


def compute(orders: pd.DataFrame, items: pd.DataFrame, sellers: pd.DataFrame) -> dict:
    """The four facts the predictions are about."""
    delivered = orders[orders["order_status"] == "delivered"].copy()
    for column in TIMESTAMPS:
        delivered[column] = pd.to_datetime(delivered[column])
    timed = delivered.dropna(subset=TIMESTAMPS[:4])
    if timed.empty:
        raise ValueError("no delivered orders with the four timestamps")
    purchase, approved, carrier, customer = (timed[c] for c in TIMESTAMPS[:4])
    stages = pd.DataFrame({
        "approval": _days(approved, purchase),
        "handoff": _days(carrier, approved),
        "transit": _days(customer, carrier),
    })
    total = _days(customer, purchase)
    medians = stages.median()
    shares = pd.Series({s: float(stages[s].cov(total) / total.var()) for s in STAGES})

    estimated = delivered.dropna(subset=["order_delivered_customer_date", "order_estimated_delivery_date"])
    late = (estimated["order_delivered_customer_date"].dt.normalize()
            > estimated["order_estimated_delivery_date"].dt.normalize())

    first_item = (items.assign(item_no=pd.to_numeric(items["order_item_id"]))
                  .sort_values(["order_id", "item_no"]).drop_duplicates("order_id"))
    by_state = (timed.assign(total=total).merge(first_item[["order_id", "seller_id"]], on="order_id")
                .merge(sellers[["seller_id", "seller_state"]], on="seller_id")
                .groupby("seller_state")["total"].agg(["size", "median"]))
    eligible = by_state[by_state["size"] >= prereg.GESTALT_MIN_ORDERS]
    if eligible.empty:
        raise ValueError(f"no seller state has {prereg.GESTALT_MIN_ORDERS} orders or more")
    # idxmax takes the first maximum, so a tie goes to the state that sorts first
    return {
        "B1": str(medians.idxmax()),
        "B2": str(shares.idxmax()),
        "B3": round(100 * float(late.mean()), 2),
        "B4": str(eligible["median"].idxmax()),
        "k_states": int(len(eligible)),
        "medians_days": {s: round(float(medians[s]), 4) for s in STAGES},
        "variance_shares": {s: round(float(shares[s]), 4) for s in STAGES},
    }


def score(predictions: dict, result: dict) -> dict:
    """Hits per question and the verdict. A missing or malformed prediction is an error, not a miss."""
    for key in ("B1", "B2", "B3", "B4"):
        if key not in predictions:
            raise ValueError(f"predictions.json has no {key}")
    for key in ("B1", "B2"):
        if predictions[key] not in STAGES:
            raise ValueError(f"{key} must be one of {STAGES}, got {predictions[key]!r}")
    if not isinstance(predictions["B3"], (int, float)) or isinstance(predictions["B3"], bool):
        raise ValueError("B3 must be a number (a percentage)")
    if not isinstance(predictions["B4"], str):
        raise ValueError("B4 must be a state code")
    hits = {
        "B1": predictions["B1"] == result["B1"],
        "B2": predictions["B2"] == result["B2"],
        "B3": abs(predictions["B3"] - result["B3"]) <= prereg.GESTALT_WINDOW,
        "B4": predictions["B4"] == result["B4"],
    }
    return {"hits": hits, "hit_count": sum(hits.values()), "passes": sum(hits.values()) >= prereg.GESTALT_MIN_HITS}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRE-N0-01 Annex B: predictions first, calculation after.")
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--verify", action="store_true", help="only check the input SHA-256")
    parser.add_argument("--predictions", type=Path, help="predictions.json, written before this run")
    args = parser.parse_args(argv)
    problems = run_n0.verify_inputs(args.data, prereg.EXPECTED_SHA256_B)
    if args.verify:
        for name in prereg.EXPECTED_SHA256_B:
            print(f"{'MISMATCH' if any(p.startswith(name) for p in problems) else 'ok':8} {name}")
        for problem in problems:
            print(problem, file=sys.stderr)
        return 3 if problems else 0
    if args.predictions is None:
        print("error: --predictions is required: write and date the four answers before computing", file=sys.stderr)
        return 2
    if problems:
        print("error: inputs differ from the pre-registration:\n  " + "\n  ".join(problems), file=sys.stderr)
        return 3
    try:
        predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
        tables = (pd.read_csv(args.data / name, dtype=str, usecols=cols, encoding="utf-8")
                  for name, cols in USECOLS.items())
        result = compute(*tables)
        verdict = score(predictions, result)
    except (FileNotFoundError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    print(json.dumps({"result": result, "predictions": predictions, **verdict}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
