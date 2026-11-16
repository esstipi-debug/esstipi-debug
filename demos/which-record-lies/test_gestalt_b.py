"""Tests for Annex B (blind predictions about delivery times) on a small synthetic marketplace."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

import gestalt_b
import prereg
import run_n0

PURCHASE = datetime(2018, 1, 1)


def marketplace():
    """20 delivered orders: 10 from an SP seller (transit 5 days), 10 from an RJ seller (transit 9 days).

    Approval takes 1 day and the carrier handoff 2 days for everyone; the estimate is purchase + 10 days.
    So: transit has the largest median (7 days) and all the variance, RJ is slower, 50% arrive late."""
    rows, items = [], []
    for i in range(20):
        state_is_rj = i >= 10
        approved = PURCHASE + timedelta(days=1)
        carrier = approved + timedelta(days=2)
        customer = carrier + timedelta(days=9 if state_is_rj else 5)
        order_id = f"o{i:02d}"
        rows.append((order_id, "delivered", PURCHASE, approved, carrier, customer, PURCHASE + timedelta(days=10)))
        items.append((order_id, "1", "s_rj" if state_is_rj else "s_sp"))
    orders = pd.DataFrame(rows, columns=["order_id", "order_status", *gestalt_b.TIMESTAMPS]).astype(
        {c: "string" for c in gestalt_b.TIMESTAMPS})
    items = pd.DataFrame(items, columns=["order_id", "order_item_id", "seller_id"])
    sellers = pd.DataFrame({"seller_id": ["s_sp", "s_rj"], "seller_state": ["SP", "RJ"]})
    return orders, items, sellers


@pytest.fixture()
def small_threshold(monkeypatch):
    monkeypatch.setattr(prereg, "GESTALT_MIN_ORDERS", 5)


def test_compute_finds_the_stage_the_share_the_percentage_and_the_state(small_threshold):
    result = gestalt_b.compute(*marketplace())
    assert result["B1"] == "transit"
    assert result["B2"] == "transit"
    assert result["B3"] == 50.0
    assert result["B4"] == "RJ"
    assert result["k_states"] == 2
    assert result["medians_days"] == {"approval": 1.0, "handoff": 2.0, "transit": 7.0}
    assert sum(result["variance_shares"].values()) == pytest.approx(1.0, abs=1e-3)


def test_states_below_the_order_threshold_are_not_compared():
    with pytest.raises(ValueError, match="no seller state"):
        gestalt_b.compute(*marketplace())          # 10 orders per state, the real threshold is 1,000


def test_compute_refuses_data_with_no_delivered_order_with_all_timestamps():
    orders, items, sellers = marketplace()
    with pytest.raises(ValueError, match="four timestamps"):
        gestalt_b.compute(orders.assign(order_status="shipped"), items, sellers)


def test_score_counts_hits_and_passes_with_three_of_four():
    result = {"B1": "transit", "B2": "transit", "B3": 50.0, "B4": "RJ"}
    perfect = gestalt_b.score({"B1": "transit", "B2": "transit", "B3": 50.0, "B4": "RJ"}, result)
    assert perfect["hit_count"] == 4 and perfect["passes"] is True
    three = gestalt_b.score({"B1": "transit", "B2": "approval", "B3": 51.9, "B4": "RJ"}, result)
    assert three["hits"] == {"B1": True, "B2": False, "B3": True, "B4": True} and three["passes"] is True
    two = gestalt_b.score({"B1": "approval", "B2": "approval", "B3": 50.0, "B4": "RJ"}, result)
    assert two["hit_count"] == 2 and two["passes"] is False


def test_the_b3_window_is_two_points_inclusive():
    result = {"B1": "transit", "B2": "transit", "B3": 50.0, "B4": "RJ"}
    base = {"B1": "approval", "B2": "approval", "B4": "SP"}
    assert gestalt_b.score({**base, "B3": 52.0}, result)["hits"]["B3"] is True
    assert gestalt_b.score({**base, "B3": 52.01}, result)["hits"]["B3"] is False
    assert gestalt_b.score({**base, "B3": 48.0}, result)["hits"]["B3"] is True


def test_a_missing_or_malformed_prediction_is_an_error_not_a_miss():
    result = {"B1": "transit", "B2": "transit", "B3": 50.0, "B4": "RJ"}
    good = {"B1": "transit", "B2": "transit", "B3": 50.0, "B4": "RJ"}
    for broken in ({k: v for k, v in good.items() if k != "B3"}, {**good, "B1": "shipping"},
                   {**good, "B3": "13"}, {**good, "B3": True}, {**good, "B4": 5}):
        with pytest.raises(ValueError):
            gestalt_b.score(broken, result)


def write_olist(folder):
    orders, items, sellers = marketplace()
    names = {"olist_orders_dataset.csv": orders, "olist_order_items_dataset.csv": items,
             "olist_sellers_dataset.csv": sellers}
    for name, frame in names.items():
        frame.to_csv(folder / name, index=False)
    return {name: run_n0.sha256_file(folder / name) for name in names}


def test_the_script_refuses_to_run_without_predictions(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prereg, "EXPECTED_SHA256_B", write_olist(tmp_path))
    assert gestalt_b.main(["--data", str(tmp_path)]) == 2
    assert "--predictions is required" in capsys.readouterr().err


def test_the_script_refuses_inputs_that_differ_from_the_pre_registered_hash(tmp_path, capsys):
    write_olist(tmp_path)
    (tmp_path / "predictions.json").write_text("{}")
    assert gestalt_b.main(["--data", str(tmp_path), "--predictions", str(tmp_path / "predictions.json")]) == 3
    assert "differ" in capsys.readouterr().err


def test_the_script_prints_the_result_and_the_verdict(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prereg, "EXPECTED_SHA256_B", write_olist(tmp_path))
    monkeypatch.setattr(prereg, "GESTALT_MIN_ORDERS", 5)
    predictions = {"B1": "transit", "B2": "transit", "B3": 49.0, "B4": "SP"}
    (tmp_path / "predictions.json").write_text(json.dumps(predictions))
    assert gestalt_b.main(["--data", str(tmp_path), "--predictions", str(tmp_path / "predictions.json")]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["hit_count"] == 3 and printed["passes"] is True
    assert printed["result"]["B4"] == "RJ"


def test_the_sellers_hash_is_pre_registered_with_the_other_two():
    assert set(prereg.EXPECTED_SHA256_B) == {
        "olist_orders_dataset.csv", "olist_order_items_dataset.csv", "olist_sellers_dataset.csv"}
    assert all(len(h) == 64 for h in prereg.EXPECTED_SHA256_B.values())
