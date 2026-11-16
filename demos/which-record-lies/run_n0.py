"""PRE-N0-01: the pre-registered Level-0 audit on the public Olist data, with before and after.

    python run_n0.py --data <Olist folder> --verify       # the 64 characters of the 3 SHA-256
    python run_n0.py --data <Olist folder> --out out      # about 2 minutes, no network
    python verify_n0.py out                               # re-hash every output against MANIFEST.sha256

The run refuses to start if any input file differs from the pre-registered SHA-256. The output holds no
date, time, path or library version, uses LF line endings, sorts by order_id and rounds BRL to 2 decimals
and shares to 6, so two environments give byte-identical files.

Do not run it before the pre-registration commit is published, the labeler is chosen and the predictions of
Annex B exist: those three steps are in docs/plans/2026-11-15-preregistro-n0-01.md of the author's corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path

import pandas as pd

import control
import prereg
import sample
import valuation
import which_record_lies as wrl

USECOLS = {
    "olist_orders_dataset.csv": ["order_id", "order_status", "order_delivered_customer_date"],
    "olist_order_reviews_dataset.csv": [
        "review_id", "order_id", "review_comment_title", "review_comment_message",
        "review_answer_timestamp",
    ],
    "olist_order_items_dataset.csv": [
        "order_id", "order_item_id", "seller_id", "price", "freight_value",
    ],
}
MANIFEST = "MANIFEST.sha256"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs(data_dir: Path, expected: Mapping[str, str] = prereg.EXPECTED_SHA256) -> list[str]:
    """One message per input file that is missing or differs from the pre-registered hash."""
    problems = []
    for name, want in expected.items():
        path = data_dir / name
        if not path.is_file():
            problems.append(f"{name}: missing")
            continue
        got = sha256_file(path)
        if got != want:
            problems.append(f"{name}: sha256 {got} differs from the pre-registered {want}")
    return problems


def load(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    orders, reviews, items = (
        pd.read_csv(data_dir / name, dtype=str, usecols=columns, encoding="utf-8")
        for name, columns in USECOLS.items()
    )
    return orders, reviews, items


def _write(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def _csv(frame: pd.DataFrame) -> str:
    return frame.to_csv(index=False, lineterminator="\n")


def run(
    data_dir: Path,
    out_dir: Path,
    *,
    expected: Mapping[str, str] = prereg.EXPECTED_SHA256,
    seed: int = prereg.SEED,
    n: int = prereg.SAMPLE_SIZE,
    alpha: float = wrl.DEFAULT_ALPHA,
    min_orders: int = wrl.DEFAULT_MIN_ORDERS,
) -> dict:
    problems = verify_inputs(data_dir, expected)
    if problems:
        raise ValueError("inputs differ from the pre-registration:\n  " + "\n  ".join(problems))
    orders, reviews, items = load(data_dir)
    order_table = wrl.build_order_table(orders, reviews, items)
    summary = wrl.summarize(order_table, alpha=alpha, min_orders=min_orders)
    claims = valuation.claim_classes(order_table, alpha=alpha, min_orders=min_orders)
    table = valuation.counterfactual(claims, items)
    by_row = table.set_index("row")

    late_ids = sample.late_sample(claims, n, seed)
    conflict_ids = sample.conflict_sample(claims, n, seed)
    key = pd.concat([
        pd.DataFrame({"order_id": late_ids, "sample": "late"}),
        pd.DataFrame({"order_id": conflict_ids, "sample": "conflict"}),
    ]).merge(claims, on="order_id", how="left")

    avoided = by_row.loc["avoided_R0_minus_R1"]
    result = {
        "preregistration": "PRE-N0-01",
        "seed": seed,
        "sample_size": n,
        "alpha": alpha,
        "min_orders": min_orders,
        "input_sha256": dict(sorted(expected.items())),
        "delivered_orders_with_review": summary.orders,
        "claims_R0": summary.claims,
        "late": summary.late,
        "conflicts_R1": summary.conflicts,
        "flagged_conflicts": summary.flagged_conflicts,
        "indeterminate_conflicts": summary.indeterminate,
        "sellers_checked": summary.sellers_checked,
        "sellers_flagged": summary.sellers_flagged,
        "avoided_orders": int(avoided["orders"]),
        "avoided_share_of_claims": round(float(avoided["share_of_claims"]), 6),
        "avoided_value_brl": round(float(avoided["value_brl"]), 2),
        "avoided_share_of_value": round(float(avoided["share_of_value"]), 6),
        "H-N3": bool(avoided["share_of_claims"] >= prereg.H_N3_MIN_SHARE
                     and avoided["share_of_value"] >= prereg.H_N3_MIN_SHARE),
        "negative_control": control.negative_control(
            orders, reviews, items, seed=seed, alpha=alpha, min_orders=min_orders),
        "alpha_sensitivity": {
            str(a): {"sellers_flagged": s.sellers_flagged, "flagged_conflicts": s.flagged_conflicts}
            for a in (0.001, 0.01, 0.05)
            for s in [wrl.summarize(order_table, alpha=a, min_orders=min_orders)]
        },
        "H-N1": None,   # they need the labels: python evaluate_labels.py
        "H-N2": None,
        "H-N4": None,   # it needs a second environment: compare the two MANIFEST.sha256
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    shown = table.assign(
        share_of_claims=table["share_of_claims"].map("{:.6f}".format),
        value_brl=table["value_brl"].map("{:.2f}".format),
        share_of_value=table["share_of_value"].map("{:.6f}".format),
    )
    _write(out_dir / "contrafactual.csv", _csv(shown))
    _write(out_dir / "resultado.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
    _write(out_dir / "muestra_tardio.csv", _csv(sample.blind_export(late_ids, reviews)))
    _write(out_dir / "muestra_conflicto.csv", _csv(sample.blind_export(conflict_ids, reviews)))
    _write(out_dir / "clave_muestras.csv", _csv(key[["order_id", "sample", "klass"]]))
    write_manifest(out_dir)
    return result


def write_manifest(out_dir: Path) -> None:
    lines = [f"{sha256_file(p)}  {p.name}\n"
             for p in sorted(out_dir.iterdir()) if p.is_file() and p.name != MANIFEST]
    _write(out_dir / MANIFEST, "".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PRE-N0-01: the pre-registered Level-0 audit.")
    parser.add_argument("--data", type=Path, required=True, help="folder with the Olist CSV files")
    parser.add_argument("--verify", action="store_true", help="only check the input SHA-256")
    parser.add_argument("--out", type=Path, help="folder for the output files")
    args = parser.parse_args(argv)
    problems = verify_inputs(args.data)
    if args.verify:
        for name in prereg.EXPECTED_SHA256:
            state = "MISMATCH" if any(p.startswith(name) for p in problems) else "ok"
            print(f"{state:8} {name}")
        for problem in problems:
            print(problem, file=sys.stderr)
        return 3 if problems else 0
    if args.out is None:
        parser.error("--out is required unless --verify is given")
    try:
        result = run(args.data, args.out)
    except (FileNotFoundError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
