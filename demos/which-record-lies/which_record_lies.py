"""Which record lies? Triage of "not received" reviews on orders marked delivered.

When a customer says "I never received it" and the system says "delivered", the
records disagree. Judging the customer alone skips the first question: when was
the claim written, relative to the recorded delivery? A claim written before the
delivery date describes a late order, not a conflict. What is left is a real
disagreement between records, and the second question is whether it concentrates
at one origin more than the base rate allows.

Every order with a "not received" review lands in exactly one class:

    late_not_a_conflict      review written on or before the recorded delivery day
    conflict_flagged_seller  records disagree and the seller sits above the
                             (1 - alpha) Poisson quantile of the base conflict rate
    conflict_indeterminate   records disagree and nothing says where: standard
                             policy, logged, no penalty

Input: three files of the Olist public dataset (not included in this repository).
Usage: python which_record_lies.py <folder with the Olist CSV files>
"""

from __future__ import annotations

import argparse
import math
import sys
import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_ALPHA = 0.01
DEFAULT_MIN_ORDERS = 50
MIN_ALPHA = 1e-12

LATE = "late_not_a_conflict"
FLAGGED = "conflict_flagged_seller"
INDETERMINATE = "conflict_indeterminate"

# Review text is Portuguese; accents are stripped by normalize_text before matching.
NOT_RECEIVED_PATTERN = (
    r"\bnao (?:recebi|recebemos|chegou|foi entregue|me foi entregue)"
    r"|\bnunca recebi"
)

TABLE_COLUMNS = {
    "olist_orders_dataset.csv": [
        "order_id", "order_status", "order_delivered_customer_date",
    ],
    "olist_order_reviews_dataset.csv": [
        "order_id", "review_comment_title", "review_comment_message",
        "review_answer_timestamp",
    ],
    "olist_order_items_dataset.csv": ["order_id", "order_item_id", "seller_id"],
}


def normalize_text(text: str) -> str:
    """Lowercase and drop accents, so 'Não' and 'nao' meet the same pattern."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return decomposed.encode("ascii", "ignore").decode()


def mentions_not_received(title: pd.Series, message: pd.Series) -> pd.Series:
    """True where the review title or message says the order was not received."""
    text = (title.fillna("") + " " + message.fillna("")).map(normalize_text)
    return text.str.contains(NOT_RECEIVED_PATTERN, regex=True)


def _check_alpha(alpha: float) -> None:
    if not MIN_ALPHA <= alpha < 1:
        raise ValueError(f"alpha must be in [{MIN_ALPHA:g}, 1), got {alpha}")


def poisson_upper_quantile(mean: float, alpha: float) -> int:
    """Smallest k such that P(X <= k) >= 1 - alpha, for X ~ Poisson(mean)."""
    _check_alpha(alpha)
    if not math.isfinite(mean) or mean < 0:
        raise ValueError(f"mean must be finite and not negative, got {mean}")
    if mean == 0:
        return 0
    cumulative, k = 0.0, 0
    while True:
        cumulative += math.exp(-mean + k * math.log(mean) - math.lgamma(k + 1))
        if cumulative >= 1 - alpha:
            return k
        k += 1


def _to_day(values: pd.Series) -> pd.Series:
    """Parse timestamps and keep the day. A bad date raises instead of passing."""
    return pd.to_datetime(values).dt.normalize()


def _first_item_seller(items: pd.DataFrame) -> pd.DataFrame:
    ordered = items.assign(item_no=pd.to_numeric(items["order_item_id"]))
    ordered = ordered.sort_values(["order_id", "item_no"])
    return ordered.drop_duplicates("order_id")[["order_id", "seller_id"]]


def build_order_table(
    orders: pd.DataFrame, reviews: pd.DataFrame, items: pd.DataFrame
) -> pd.DataFrame:
    """One row per delivered order that has at least one review.

    Columns: order_id; has_claim (some review says "not received"); conflict (some
    such review was written after the recorded delivery day); seller_id (seller of
    the order's first item, NaN when the order has no items).

    The customer's words are dated by review_answer_timestamp. review_creation_date
    is not used: in this dataset it is the day the survey went out (the day after
    the recorded delivery for 89% of reviews), not the day the customer wrote.
    """
    delivered = orders[
        (orders["order_status"] == "delivered")
        & orders["order_delivered_customer_date"].notna()
    ]
    joined = delivered.merge(reviews, on="order_id", how="inner")
    joined = joined.assign(
        not_received=mentions_not_received(
            joined["review_comment_title"], joined["review_comment_message"]
        ),
        delivery_day=_to_day(joined["order_delivered_customer_date"]),
        review_day=_to_day(joined["review_answer_timestamp"]),
    )
    joined = joined.assign(
        after_delivery=joined["not_received"]
        & (joined["review_day"] > joined["delivery_day"])
    )
    per_order = joined.groupby("order_id", as_index=False).agg(
        has_claim=("not_received", "any"), conflict=("after_delivery", "any")
    )
    return per_order.merge(_first_item_seller(items), on="order_id", how="left")


def flag_sellers(
    order_table: pd.DataFrame,
    *,
    alpha: float = DEFAULT_ALPHA,
    min_orders: int = DEFAULT_MIN_ORDERS,
) -> pd.DataFrame:
    """Per seller with enough orders: n orders, k conflicts, threshold, flagged.

    The threshold is the (1 - alpha) Poisson quantile of n * base rate, where the
    base rate is the conflict rate over all orders. A seller is flagged when its
    conflicts exceed it. With many sellers, about alpha * sellers flags are
    expected by chance alone.
    """
    _check_alpha(alpha)
    base_rate = float(order_table["conflict"].mean())
    per_seller = order_table.groupby("seller_id").agg(
        n=("conflict", "size"), k=("conflict", "sum")
    )
    per_seller = per_seller[per_seller["n"] >= min_orders]
    thresholds = per_seller["n"].map(
        lambda n: poisson_upper_quantile(n * base_rate, alpha)
    )
    return per_seller.assign(threshold=thresholds, flagged=per_seller["k"] > thresholds)


def classify_claims(
    order_table: pd.DataFrame, flagged_sellers: Collection[str]
) -> pd.Series:
    """One class per order that has a "not received" review."""
    claims = order_table[order_table["has_claim"]]
    at_flagged_seller = claims["seller_id"].isin(flagged_sellers)
    label = pd.Series(INDETERMINATE, index=claims.index)
    label = label.mask(~claims["conflict"], LATE)
    return label.mask(claims["conflict"] & at_flagged_seller, FLAGGED)


@dataclass(frozen=True)
class Summary:
    orders: int
    claims: int
    late: int
    conflicts: int
    flagged_conflicts: int
    sellers_checked: int
    sellers_flagged: int
    flagged_orders: int
    chance_bound: float
    alpha: float
    min_orders: int

    @property
    def indeterminate(self) -> int:
        return self.conflicts - self.flagged_conflicts

    @property
    def conflict_rate(self) -> float:
        return self.conflicts / self.orders


def summarize(
    order_table: pd.DataFrame,
    *,
    alpha: float = DEFAULT_ALPHA,
    min_orders: int = DEFAULT_MIN_ORDERS,
) -> Summary:
    if order_table.empty:
        raise ValueError("no delivered orders with a review were found")
    sellers = flag_sellers(order_table, alpha=alpha, min_orders=min_orders)
    flagged = set(sellers.index[sellers["flagged"]])
    counts = classify_claims(order_table, flagged).value_counts()
    return Summary(
        orders=len(order_table),
        claims=int(order_table["has_claim"].sum()),
        late=int(counts.get(LATE, 0)),
        conflicts=int(order_table["conflict"].sum()),
        flagged_conflicts=int(counts.get(FLAGGED, 0)),
        sellers_checked=len(sellers),
        sellers_flagged=len(flagged),
        flagged_orders=int(sellers.loc[sellers["flagged"], "n"].sum()),
        chance_bound=len(sellers) * alpha,
        alpha=alpha,
        min_orders=min_orders,
    )


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "n/a"


def format_summary(summary: Summary) -> str:
    s = summary
    classes = (
        (LATE, s.late, "review written on or before the recorded delivery day"),
        (FLAGGED, s.flagged_conflicts, "records disagree, seller above the chance threshold"),
        (INDETERMINATE, s.indeterminate, "records disagree, no sign of where: standard policy, logged"),
    )
    lines = [
        f"Delivered orders with a review: {s.orders:,}",
        f'Orders with a "not received" review: {s.claims:,}',
    ]
    lines += [
        f"  {name:<24}{count:>7,}  {_share(count, s.claims):>6}  {meaning}"
        for name, count, meaning in classes
    ]
    lines += [
        f"Conflict between records: {s.conflicts:,} orders "
        f"({s.conflict_rate:.2%} of delivered orders with a review)",
        f"Sellers with at least {s.min_orders} orders: {s.sellers_checked:,}",
        f"Sellers above the {1 - s.alpha:.0%} Poisson quantile of the base rate: "
        f"{s.sellers_flagged:,} (at most {s.chance_bound:.1f} expected by chance)",
        f"  they hold {s.flagged_conflicts:,} of {s.conflicts:,} conflicts "
        f"({_share(s.flagged_conflicts, s.conflicts)}) with {s.flagged_orders:,} "
        f"of {s.orders:,} orders ({_share(s.flagged_orders, s.orders)})",
    ]
    return "\n".join(lines)


def load_tables(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    missing = [n for n in TABLE_COLUMNS if not (data_dir / n).is_file()]
    if missing:
        raise FileNotFoundError(f"missing in {data_dir}: {', '.join(missing)}")
    orders, reviews, items = (
        pd.read_csv(data_dir / name, dtype=str, usecols=columns, encoding="utf-8")
        for name, columns in TABLE_COLUMNS.items()
    )
    return orders, reviews, items


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description='Triage "not received" reviews on orders marked delivered.'
    )
    parser.add_argument("data_dir", type=Path, help="folder with the Olist CSV files")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--min-orders", type=int, default=DEFAULT_MIN_ORDERS)
    args = parser.parse_args(argv)
    try:
        order_table = build_order_table(*load_tables(args.data_dir))
        summary = summarize(order_table, alpha=args.alpha, min_orders=args.min_orders)
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(format_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
