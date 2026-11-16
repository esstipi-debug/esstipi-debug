"""Seeded samples, blind export and label evaluation (PRE-N0-01, sections 5 and 6)."""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Iterable, Sequence

import pandas as pd

import prereg
import which_record_lies as wrl

Z95 = 1.959963984540054   # two-sided 95% normal quantile


def wilson_lower(successes: int, n: int, z: float = Z95) -> float:
    """Lower bound of the Wilson score interval for a proportion."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= successes <= n:
        raise ValueError(f"successes must be in [0, {n}], got {successes}")
    p = successes / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / (1 + z * z / n)


def draw(order_ids: Iterable[str], n: int, seed: int) -> list[str]:
    """n order ids drawn with random.Random(seed).sample over the sorted ids.

    The input order does not matter: the same set and seed give the same draw."""
    ordered = sorted(set(order_ids))
    if n > len(ordered):
        raise ValueError(f"asked for {n} orders from a pool of {len(ordered)}")
    return random.Random(seed).sample(ordered, n)


def late_sample(claims: pd.DataFrame, n: int, seed: int) -> list[str]:
    return draw(claims.loc[claims["klass"] == wrl.LATE, "order_id"], n, seed)


def conflict_sample(claims: pd.DataFrame, n: int, seed: int) -> list[str]:
    """Flagged and indeterminate conflicts in proportion to their counts, in shuffled order."""
    flagged = sorted(claims.loc[claims["klass"] == wrl.FLAGGED, "order_id"])
    indeterminate = sorted(claims.loc[claims["klass"] == wrl.INDETERMINATE, "order_id"])
    total = len(flagged) + len(indeterminate)
    if n > total:
        raise ValueError(f"asked for {n} conflicts from a pool of {total}")
    n_flagged = min(len(flagged), round(n * len(flagged) / total))
    n_indeterminate = n - n_flagged
    if n_indeterminate > len(indeterminate):
        n_indeterminate, n_flagged = len(indeterminate), n - len(indeterminate)
    rng = random.Random(seed)
    picked = rng.sample(flagged, n_flagged) + rng.sample(indeterminate, n_indeterminate)
    rng.shuffle(picked)
    return picked


def blind_export(order_ids: Sequence[str], reviews: pd.DataFrame) -> pd.DataFrame:
    """order_id and the claim's text, in the order given and with no class.

    An order with more than one "not received" review shows the earliest by answer time."""
    claim = reviews[wrl.mentions_not_received(
        reviews["review_comment_title"], reviews["review_comment_message"])]
    claim = claim.sort_values(["order_id", "review_answer_timestamp", "review_id"], kind="stable")
    first = claim.drop_duplicates("order_id").set_index("order_id")
    missing = [o for o in order_ids if o not in first.index]
    if missing:
        raise ValueError(f"no 'not received' review for {len(missing)} sampled orders, first {missing[0]}")
    out = first.loc[list(order_ids), ["review_comment_title", "review_comment_message"]].fillna("")
    return out.reset_index()


def evaluate(labels: pd.DataFrame, key: pd.DataFrame) -> dict:
    """Shares of N, P and O per sample, the Wilson bound of N and the verdict on H-N1 and H-N2.

    labels: order_id, label, by. key: order_id, sample ('late' or 'conflict'). Every sampled order
    needs exactly one label from the rubric and no label may be for an order outside the samples."""
    if labels["order_id"].duplicated().any():
        raise ValueError("an order has more than one label")
    bad = sorted(set(labels["label"]) - set(prereg.LABELS))
    if bad:
        raise ValueError(f"labels outside the rubric {prereg.LABELS}: {bad}")
    if set(labels["order_id"]) != set(key["order_id"]):
        raise ValueError("the labeled orders are not exactly the sampled orders")
    merged = key.merge(labels, on="order_id", how="inner")
    result: dict = {}
    for sample in ("late", "conflict"):
        counts = Counter(merged.loc[merged["sample"] == sample, "label"])
        n = sum(counts.values())
        result[sample] = {
            "n": n,
            **{label: counts.get(label, 0) for label in prereg.LABELS},
            "share_N": round(counts.get("N", 0) / n, 6),
            "wilson_lower_N": round(wilson_lower(counts.get("N", 0), n), 6),
        }
    result["H-N1"] = result["late"]["wilson_lower_N"] >= prereg.H_N1_MIN_WILSON
    result["H-N2"] = result["conflict"]["wilson_lower_N"] >= prereg.H_N2_MIN_WILSON
    return result
