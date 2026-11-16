"""Tests for the PRE-N0-01 harness on synthetic marketplaces (no Olist data)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

import control
import evaluate_labels
import prereg
import run_n0
import sample
import valuation
import verify_n0
import which_record_lies as wrl
from test_which_record_lies import CLUSTER_PLAN, marketplace


def olist_files(folder: Path, plan=None, late_claims: int = 2) -> dict[str, str]:
    """Write a synthetic marketplace as the three Olist CSV files and return their SHA-256."""
    orders, reviews, items = marketplace(plan or CLUSTER_PLAN, late_claims=late_claims)
    items = items.assign(price="10.00", freight_value="2.50")
    frames = {
        "olist_orders_dataset.csv": orders,
        "olist_order_reviews_dataset.csv": reviews,
        "olist_order_items_dataset.csv": items,
    }
    for name, frame in frames.items():
        frame.to_csv(folder / name, index=False)
    return {name: run_n0.sha256_file(folder / name) for name in frames}


@pytest.fixture()
def claims_and_items():
    orders, reviews, items = marketplace(CLUSTER_PLAN)
    items = items.assign(price="10.00", freight_value="2.50")
    table = wrl.build_order_table(orders, reviews, items)
    return valuation.claim_classes(table), items, reviews


# ---------------------------------------------------------------- Wilson bound

def test_wilson_lower_matches_the_values_the_pre_registration_quotes():
    assert sample.wilson_lower(90, 100) == pytest.approx(0.826, abs=5e-4)
    assert sample.wilson_lower(50, 100) == pytest.approx(0.404, abs=5e-4)


def test_wilson_lower_is_zero_with_no_successes_and_below_one_with_all():
    assert sample.wilson_lower(0, 10) == pytest.approx(0.0, abs=1e-12)
    assert sample.wilson_lower(10, 10) < 1.0


def test_wilson_lower_rejects_impossible_counts():
    for bad in ((1, 0), (11, 10), (-1, 10)):
        with pytest.raises(ValueError):
            sample.wilson_lower(*bad)


# ---------------------------------------------------------------- sampling

def test_draw_is_deterministic_and_ignores_the_input_order():
    ids = [f"o{i:03d}" for i in range(50)]
    first = sample.draw(ids, 10, prereg.SEED)
    assert first == sample.draw(list(reversed(ids)), 10, prereg.SEED)
    assert first != sample.draw(ids, 10, prereg.SEED + 1)
    assert len(set(first)) == 10


def test_draw_refuses_a_sample_larger_than_the_pool():
    with pytest.raises(ValueError):
        sample.draw(["a", "b"], 3, 1)


def test_the_two_samples_are_disjoint_and_come_from_their_classes(claims_and_items):
    claims, _, _ = claims_and_items
    late = sample.late_sample(claims, 10, prereg.SEED)
    conflict = sample.conflict_sample(claims, 10, prereg.SEED)
    klass = dict(zip(claims["order_id"], claims["klass"]))
    assert {klass[o] for o in late} == {wrl.LATE}
    assert wrl.LATE not in {klass[o] for o in conflict}
    assert not set(late) & set(conflict)


def test_conflict_sample_keeps_the_flagged_share_and_is_deterministic(claims_and_items):
    claims, _, _ = claims_and_items
    klass = dict(zip(claims["order_id"], claims["klass"]))
    picked = sample.conflict_sample(claims, 10, prereg.SEED)
    flagged_pool = (claims["klass"] == wrl.FLAGGED).sum()
    conflict_pool = (claims["klass"] != wrl.LATE).sum()
    assert sum(klass[o] == wrl.FLAGGED for o in picked) == round(10 * flagged_pool / conflict_pool)
    assert picked == sample.conflict_sample(claims, 10, prereg.SEED)


def test_blind_export_has_the_text_and_no_class(claims_and_items):
    claims, _, reviews = claims_and_items
    ids = sample.late_sample(claims, 5, prereg.SEED)
    exported = sample.blind_export(ids, reviews)
    assert list(exported.columns) == ["order_id", "review_comment_title", "review_comment_message"]
    assert list(exported["order_id"]) == ids
    assert exported["review_comment_message"].str.contains("recebi").all()


def test_blind_export_refuses_an_order_without_a_claim(claims_and_items):
    _, _, reviews = claims_and_items
    benign = reviews.loc[~wrl.mentions_not_received(
        reviews["review_comment_title"], reviews["review_comment_message"]), "order_id"].iloc[0]
    with pytest.raises(ValueError):
        sample.blind_export([benign], reviews)


# ---------------------------------------------------------------- valuation

def test_order_values_sum_price_and_freight_by_order():
    items = pd.DataFrame({"order_id": ["a", "a", "b"], "price": ["10.00", "5.00", "7.50"],
                          "freight_value": ["1.00", "1.00", "2.50"]})
    assert {k: float(v) for k, v in valuation.order_values(items).items()} == {"a": 17.0, "b": 10.0}


def test_order_values_raise_on_a_value_that_is_not_a_number():
    items = pd.DataFrame({"order_id": ["a"], "price": ["abc"], "freight_value": ["1.00"]})
    with pytest.raises(ValueError):
        valuation.order_values(items)


def test_counterfactual_counts_and_values_each_class(claims_and_items):
    claims, items, _ = claims_and_items
    table = valuation.counterfactual(claims, items).set_index("row")
    late = int((claims["klass"] == wrl.LATE).sum())
    conflicts = len(claims) - late
    assert table.loc["R0_naive_disputes", "orders"] == len(claims)
    assert table.loc["R1_conflicts_after_triage", "orders"] == conflicts
    assert table.loc["avoided_R0_minus_R1", "orders"] == late
    assert table.loc[list(valuation.CLASSES), "orders"].sum() == len(claims)
    assert table.loc["R0_naive_disputes", "value_brl"] == pytest.approx(12.5 * len(claims))
    assert table.loc["avoided_R0_minus_R1", "share_of_value"] == pytest.approx(late / len(claims))


def test_counterfactual_refuses_to_value_no_claims():
    with pytest.raises(ValueError):
        valuation.counterfactual(pd.DataFrame({"order_id": [], "klass": []}), pd.DataFrame(
            {"order_id": [], "price": [], "freight_value": []}))


# ---------------------------------------------------------------- the run

def test_run_writes_every_file_and_a_manifest_that_verifies(tmp_path):
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    hashes = olist_files(data)
    result = run_n0.run(data, out, expected=hashes, n=10)
    names = sorted(p.name for p in out.iterdir())
    assert names == ["MANIFEST.sha256", "clave_muestras.csv", "contrafactual.csv", "muestra_conflicto.csv",
                     "muestra_tardio.csv", "resultado.json"]
    assert verify_n0.verify(out) == []
    assert json.loads((out / "resultado.json").read_text()) == result
    assert result["claims_R0"] == result["late"] + result["conflicts_R1"]
    assert result["H-N1"] is None and result["H-N2"] is None and result["H-N4"] is None
    assert result["H-N3"] is True and result["avoided_orders"] == result["late"]


def test_h_n3_is_false_when_the_triage_avoids_less_than_the_threshold(tmp_path):
    """30 late claims against 126 conflicts: 19% of the claims are avoided, under the 40% line."""
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    plan = {"s00": (100, 10), **{f"s{i:02d}": (100, 4) for i in range(1, 30)}}
    result = run_n0.run(data, out, expected=olist_files(data, plan, late_claims=1), n=10)
    assert result["avoided_share_of_claims"] < prereg.H_N3_MIN_SHARE
    assert result["H-N3"] is False


def test_the_output_has_no_date_path_or_carriage_return(tmp_path):
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    run_n0.run(data, out, expected=olist_files(data), n=10)
    for path in out.iterdir():
        raw = path.read_bytes()
        assert b"\r" not in raw, path.name
        assert str(tmp_path).encode() not in raw, path.name


def test_two_runs_are_byte_identical(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    hashes = olist_files(data)
    run_n0.run(data, tmp_path / "a", expected=hashes, n=10)
    run_n0.run(data, tmp_path / "b", expected=hashes, n=10)
    assert verify_n0.compare(tmp_path / "a", tmp_path / "b") == []
    assert verify_n0.main([str(tmp_path / "a"), str(tmp_path / "b")]) == 0


def test_the_run_refuses_inputs_that_differ_from_the_pre_registered_hash(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    hashes = olist_files(data)
    hashes["olist_orders_dataset.csv"] = "0" * 64
    with pytest.raises(ValueError, match="differ"):
        run_n0.run(data, tmp_path / "out", expected=hashes, n=10)
    assert not (tmp_path / "out").exists()


def test_verify_inputs_reports_a_missing_file(tmp_path):
    problems = run_n0.verify_inputs(tmp_path, {"olist_orders_dataset.csv": "0" * 64})
    assert problems == ["olist_orders_dataset.csv: missing"]


def test_verify_flags_a_changed_file_an_extra_file_and_a_different_run(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    hashes = olist_files(data)
    run_n0.run(data, tmp_path / "a", expected=hashes, n=10)
    run_n0.run(data, tmp_path / "b", expected=hashes, n=10)
    (tmp_path / "a" / "resultado.json").write_text("{}\n")
    (tmp_path / "a" / "extra.txt").write_text("x")
    problems = verify_n0.verify(tmp_path / "a")
    assert any("resultado.json" in p and "differs" in p for p in problems)
    assert any("extra.txt" in p and "not in the manifest" in p for p in problems)
    assert verify_n0.main([str(tmp_path / "a")]) == 1


def test_the_pre_registered_hashes_are_the_ones_of_the_readme():
    readme = (Path(__file__).parent / "README.md").read_text(encoding="utf-8")
    for digest in prereg.EXPECTED_SHA256.values():
        assert digest in readme


# ---------------------------------------------------------------- negative control

def test_permuting_texts_keeps_the_texts_of_each_month_and_is_deterministic():
    orders, reviews, _ = marketplace(CLUSTER_PLAN)
    first = control.permute_texts(orders, reviews, prereg.SEED)
    again = control.permute_texts(orders, reviews, prereg.SEED)
    assert first.equals(again)
    assert not first.equals(control.permute_texts(orders, reviews, prereg.SEED + 1))
    pairs = lambda frame: sorted(zip(frame["review_comment_title"].fillna(""),   # noqa: E731
                                     frame["review_comment_message"]))
    assert pairs(first) == pairs(reviews)             # one delivery month: the multiset of texts is the same
    assert list(first["order_id"]) == sorted(reviews["order_id"])   # the dates stay with their orders


def test_the_negative_control_reports_the_shares_a_random_pairing_gives(tmp_path):
    orders, reviews, items = marketplace(CLUSTER_PLAN)
    items = items.assign(price="10.00", freight_value="2.50")
    result = control.negative_control(orders, reviews, items, seed=prereg.SEED)
    assert result["claims"] > 0
    assert result["late_share"] + result["conflict_share"] == pytest.approx(1.0, abs=1e-6)


def test_the_run_reports_the_control_and_the_alpha_sensitivity(tmp_path):
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    result = run_n0.run(data, out, expected=olist_files(data), n=10)
    assert set(result["negative_control"]) == {"claims", "late_share", "conflict_share"}
    flagged = [result["alpha_sensitivity"][a]["sellers_flagged"] for a in ("0.001", "0.01", "0.05")]
    assert flagged == sorted(flagged)         # a looser alpha never flags fewer sellers


# ---------------------------------------------------------------- labels

def labeled(key, n_late_n, n_conflict_n):
    """Label the first n_late_n late orders and n_conflict_n conflict orders N and the rest O."""
    rows = []
    for sample_name, n_n in (("late", n_late_n), ("conflict", n_conflict_n)):
        ids = list(key.loc[key["sample"] == sample_name, "order_id"])
        rows += [(o, "N" if i < n_n else "O", "owner") for i, o in enumerate(ids)]
    return pd.DataFrame(rows, columns=["order_id", "label", "by"])


@pytest.fixture()
def key(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    run_n0.run(data, tmp_path / "out", expected=olist_files(data), n=10)
    return pd.read_csv(tmp_path / "out" / "clave_muestras.csv", dtype=str)


def test_evaluate_reports_the_shares_and_the_wilson_bound(key):
    result = sample.evaluate(labeled(key, 10, 10), key)
    assert result["late"]["n"] == 10 and result["late"]["N"] == 10
    assert result["late"]["wilson_lower_N"] == pytest.approx(sample.wilson_lower(10, 10), abs=1e-6)
    assert result["H-N1"] is (result["late"]["wilson_lower_N"] >= prereg.H_N1_MIN_WILSON)
    assert result["H-N2"] is (result["conflict"]["wilson_lower_N"] >= prereg.H_N2_MIN_WILSON)


def test_evaluate_fails_h_n1_when_late_claims_are_not_nothing_arrived(key):
    result = sample.evaluate(labeled(key, 3, 10), key)
    assert result["H-N1"] is False


def test_evaluate_rejects_missing_duplicated_foreign_and_out_of_rubric_labels(key):
    good = labeled(key, 10, 10)
    with pytest.raises(ValueError, match="exactly"):
        sample.evaluate(good.iloc[1:], key)
    with pytest.raises(ValueError, match="more than one"):
        sample.evaluate(pd.concat([good, good.iloc[:1]]), key)
    with pytest.raises(ValueError, match="rubric"):
        sample.evaluate(good.assign(label="X"), key)
    foreign = good.copy()
    foreign.loc[0, "order_id"] = "not-sampled"
    with pytest.raises(ValueError, match="exactly"):
        sample.evaluate(foreign, key)


def test_evaluate_labels_cli_prints_the_verdict(tmp_path, capsys):
    data, out = tmp_path / "data", tmp_path / "out"
    data.mkdir()
    run_n0.run(data, out, expected=olist_files(data), n=10)
    key = pd.read_csv(out / "clave_muestras.csv", dtype=str)
    labeled(key, 10, 10).to_csv(tmp_path / "labels.csv", index=False)
    assert evaluate_labels.main([str(out), str(tmp_path / "labels.csv")]) == 0
    verdict = json.loads(capsys.readouterr().out)
    assert set(verdict) == {"H-N1", "H-N2", "conflict", "late"}
    assert math.isfinite(verdict["late"]["wilson_lower_N"])
    assert evaluate_labels.main([str(out), str(tmp_path / "no-such.csv")]) == 3
