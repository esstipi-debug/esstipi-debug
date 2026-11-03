"""Tests for which_record_lies on small synthetic marketplaces (no Olist data)."""

from __future__ import annotations

import math
import random

import pandas as pd
import pytest

import which_record_lies as wrl

DELIVERED_AT = "2018-01-10 12:00:00"
SURVEY_AT = "2018-01-01 00:00:00"   # survey sent before every delivery in these tests
BENIGN_AT = "2018-01-15 00:00:00"
LATE_AT = "2018-01-05 00:00:00"
CONFLICT_AT = "2018-01-20 00:00:00"


def spec(order_id, *, status="delivered", delivered_at=DELIVERED_AT,
         reviews=(("Tudo certo", BENIGN_AT),), sellers=("s0",)):
    return {"id": order_id, "status": status, "delivered_at": delivered_at,
            "reviews": reviews, "sellers": sellers}


def tables(specs):
    """Build the three Olist-shaped tables (with an extra column each) from specs."""
    orders = pd.DataFrame(
        [(s["id"], f"c-{s['id']}", s["status"], s["delivered_at"]) for s in specs],
        columns=["order_id", "customer_id", "order_status",
                 "order_delivered_customer_date"],
    )
    reviews = pd.DataFrame(
        [(f"r-{s['id']}-{i}", s["id"], None, text, SURVEY_AT, at)
         for s in specs for i, (text, at) in enumerate(s["reviews"])],
        columns=["review_id", "order_id", "review_comment_title",
                 "review_comment_message", "review_creation_date",
                 "review_answer_timestamp"],
    )
    items = pd.DataFrame(
        [(s["id"], str(n), seller)
         for s in specs for n, seller in enumerate(s["sellers"], start=1)],
        columns=["order_id", "order_item_id", "seller_id"],
    )
    return orders, reviews, items


def marketplace(plan, late_claims=2):
    """plan: seller -> (orders, conflicts). Each seller also gets `late_claims`
    claims written BEFORE the recorded delivery, and benign reviews for the rest."""
    specs = []
    for seller, (n_orders, conflicts) in plan.items():
        for i in range(n_orders):
            if i < conflicts:
                reviews = (("Não recebi o produto", CONFLICT_AT),)
            elif i < conflicts + late_claims:
                reviews = (("Ainda não recebi", LATE_AT),)
            else:
                reviews = (("Tudo certo", BENIGN_AT),)
            specs.append(spec(f"{seller}-{i:03d}", reviews=reviews, sellers=(seller,)))
    return tables(specs)


CLUSTER_PLAN = {"s00": (100, 10), **{f"s{i:02d}": (100, 1) for i in range(1, 30)}}


def order_table(specs):
    return wrl.build_order_table(*tables(specs))


# --- text -----------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("Não recebi", "nao recebi"), ("NUNCA RECEBI", "nunca recebi"), ("ação", "acao"),
])
def test_normalize_text_strips_accents_and_case(raw, expected):
    assert wrl.normalize_text(raw) == expected


def mentions(message, title=None):
    return bool(wrl.mentions_not_received(pd.Series([title]), pd.Series([message])).iloc[0])


@pytest.mark.parametrize("message", [
    "Não recebi o produto", "nao chegou", "NUNCA RECEBI nada", "Ainda não recebi",
    "o pedido não foi entregue", "Produto não me foi entregue", "não recebemos a encomenda",
])
def test_mentions_not_received_matches_claims(message):
    assert mentions(message)


@pytest.mark.parametrize("message", [
    "Recebi antes do prazo", "Chegou rápido", "Produto bom, recebi tudo", "", None,
    float("nan"),
])
def test_mentions_not_received_ignores_other_reviews(message):
    assert not mentions(message)


def test_mentions_not_received_reads_the_title_too():
    assert mentions("", title="Não recebi")


# --- Poisson quantile ---------------------------------------------------------

@pytest.mark.parametrize("mean, expected", [(0.0, 0), (0.5, 3), (1.0, 4), (1.3, 5)])
def test_poisson_upper_quantile_matches_hand_computed_values(mean, expected):
    assert wrl.poisson_upper_quantile(mean, 0.01) == expected


def poisson_cdf(k, mean):
    return sum(math.exp(-mean) * mean**i / math.factorial(i) for i in range(k + 1))


@pytest.mark.parametrize("mean", [0.2, 1.0, 3.7, 12.5, 40.0])
@pytest.mark.parametrize("alpha", [0.05, 0.01, 0.001])
def test_poisson_upper_quantile_is_the_smallest_k_reaching_one_minus_alpha(mean, alpha):
    q = wrl.poisson_upper_quantile(mean, alpha)
    assert poisson_cdf(q, mean) >= 1 - alpha
    assert q == 0 or poisson_cdf(q - 1, mean) < 1 - alpha


@pytest.mark.parametrize("alpha", [0.0, -0.1, 1.0, 2.0, 1e-15])
def test_poisson_upper_quantile_rejects_bad_alpha(alpha):
    with pytest.raises(ValueError, match="alpha"):
        wrl.poisson_upper_quantile(1.0, alpha)


@pytest.mark.parametrize("mean", [-1.0, float("nan"), float("inf")])
def test_poisson_upper_quantile_rejects_bad_mean(mean):
    with pytest.raises(ValueError, match="mean"):
        wrl.poisson_upper_quantile(mean, 0.01)


# --- order table ------------------------------------------------------------------

def flags(table, order_id):
    row = table.set_index("order_id").loc[order_id]
    return bool(row["has_claim"]), bool(row["conflict"])


def test_claim_before_the_recorded_delivery_is_not_a_conflict():
    table = order_table([spec("a", reviews=(("Não recebi", "2018-01-09 00:00:00"),))])
    assert flags(table, "a") == (True, False)


def test_claim_on_the_delivery_day_is_not_a_conflict():
    table = order_table([spec("a", reviews=(("Não recebi", "2018-01-10 00:00:00"),))])
    assert flags(table, "a") == (True, False)


def test_claim_after_the_recorded_delivery_is_a_conflict():
    table = order_table([spec("a", reviews=(("Não recebi", "2018-01-11 00:00:00"),))])
    assert flags(table, "a") == (True, True)


def test_the_answer_date_counts_not_the_survey_date():
    # The survey went out (SURVEY_AT) before the delivery, but the customer wrote
    # after it: the words are dated by the answer, so this is a conflict.
    table = order_table([spec("a", reviews=(("Não recebi", CONFLICT_AT),))])
    assert flags(table, "a") == (True, True)


def test_review_without_a_claim_is_neither():
    assert flags(order_table([spec("a")]), "a") == (False, False)


def test_orders_that_were_not_delivered_are_left_out():
    table = order_table([
        spec("kept"),
        spec("shipped", status="shipped", delivered_at=None),
        spec("canceled", status="canceled"),
        spec("no-date", delivered_at=None),
    ])
    assert list(table["order_id"]) == ["kept"]


def test_orders_without_a_review_are_left_out():
    table = order_table([spec("kept"), spec("silent", reviews=())])
    assert list(table["order_id"]) == ["kept"]


def test_order_with_two_reviews_appears_once_and_any_late_claim_counts():
    table = order_table([
        spec("both", reviews=(("Não recebi", LATE_AT), ("Não recebi", CONFLICT_AT))),
        spec("early-only", reviews=(("Tudo certo", BENIGN_AT), ("Não recebi", LATE_AT))),
    ])
    assert table["order_id"].is_unique
    assert flags(table, "both") == (True, True)
    assert flags(table, "early-only") == (True, False)


def test_multi_seller_order_is_attributed_to_the_seller_of_its_first_item():
    orders, reviews, items = tables([spec("m", sellers=("first", "second"))])
    shuffled = items.iloc[::-1]
    table = wrl.build_order_table(orders, reviews, shuffled)
    assert table.set_index("order_id").loc["m", "seller_id"] == "first"


def test_order_without_items_stays_in_the_base_but_belongs_to_no_seller():
    table = order_table([spec("a", sellers=()), spec("b")])
    assert len(table) == 2
    assert table.set_index("order_id").loc["a", "seller_id"] != "s0"
    assert table["seller_id"].isna().sum() == 1


def test_an_unparseable_date_fails_loudly():
    with pytest.raises(ValueError):
        order_table([spec("a", delivered_at="not-a-date")])


# --- sellers --------------------------------------------------------------------------

def test_a_cluster_at_one_seller_is_flagged_and_the_background_is_not():
    table = wrl.build_order_table(*marketplace(CLUSTER_PLAN))
    sellers = wrl.flag_sellers(table, alpha=0.01, min_orders=50)
    assert list(sellers.index[sellers["flagged"]]) == ["s00"]
    assert sellers.loc["s00", "k"] == 10
    assert sellers.loc["s00", "threshold"] == 5


def test_a_uniform_conflict_rate_flags_nobody():
    plan = {f"s{i:02d}": (100, 1) for i in range(30)}
    table = wrl.build_order_table(*marketplace(plan))
    assert not wrl.flag_sellers(table)["flagged"].any()


def test_without_any_conflict_nobody_is_flagged():
    plan = {f"s{i:02d}": (100, 0) for i in range(5)}
    sellers = wrl.flag_sellers(wrl.build_order_table(*marketplace(plan)))
    assert not sellers["flagged"].any()
    assert (sellers["threshold"] == 0).all()


def test_sellers_below_the_minimum_are_not_assessed():
    plan = {"small": (10, 10), **{f"s{i:02d}": (100, 1) for i in range(1, 6)}}
    sellers = wrl.flag_sellers(wrl.build_order_table(*marketplace(plan)), min_orders=50)
    assert "small" not in sellers.index
    assert len(sellers) == 5


def test_noise_without_a_cluster_flags_no_more_than_chance_allows():
    rng = random.Random(7)
    rate = 0.0117
    plan = {
        f"s{i:03d}": (100, sum(rng.random() < rate for _ in range(100)))
        for i in range(400)
    }
    table = wrl.build_order_table(*marketplace(plan, late_claims=0))
    sellers = wrl.flag_sellers(table, alpha=0.01, min_orders=50)
    assert len(sellers) == 400
    assert sellers["flagged"].sum() <= 12   # chance gives at most ~4 (400 * 0.01)


# --- classification and summary ------------------------------------------------------------

def test_every_claim_gets_exactly_one_label():
    table = wrl.build_order_table(*marketplace(CLUSTER_PLAN))
    labels = wrl.classify_claims(table, flagged_sellers={"s00"})
    assert len(labels) == int(table["has_claim"].sum()) == 99
    assert labels.value_counts().to_dict() == {
        wrl.LATE: 60, wrl.FLAGGED: 10, wrl.INDETERMINATE: 29,
    }


def test_summary_counts_on_the_cluster_marketplace():
    table = wrl.build_order_table(*marketplace(CLUSTER_PLAN))
    s = wrl.summarize(table, alpha=0.01, min_orders=50)
    assert (s.orders, s.claims, s.late, s.conflicts) == (3000, 99, 60, 39)
    assert (s.flagged_conflicts, s.indeterminate) == (10, 29)
    assert (s.sellers_checked, s.sellers_flagged, s.flagged_orders) == (30, 1, 100)
    assert s.chance_bound == pytest.approx(0.3)
    assert s.conflict_rate == pytest.approx(39 / 3000)


def test_summary_of_nothing_is_an_error():
    table = order_table([spec("shipped", status="shipped", delivered_at=None)])
    with pytest.raises(ValueError, match="delivered"):
        wrl.summarize(table)


def test_format_summary_reports_every_class_and_the_chance_bound():
    table = wrl.build_order_table(*marketplace(CLUSTER_PLAN))
    text = wrl.format_summary(wrl.summarize(table, alpha=0.01, min_orders=50))
    for needle in ("3,000", wrl.LATE, wrl.FLAGGED, wrl.INDETERMINATE, "99%",
                   "at most 0.3 expected by chance"):
        assert needle in text


# --- command line ---------------------------------------------------------------------------------

def write_olist_files(folder, frames):
    names = ("olist_orders_dataset.csv", "olist_order_reviews_dataset.csv",
             "olist_order_items_dataset.csv")
    for name, frame in zip(names, frames):
        frame.to_csv(folder / name, index=False)


def test_main_prints_the_summary(tmp_path, capsys):
    write_olist_files(tmp_path, marketplace(CLUSTER_PLAN))
    code = wrl.main([str(tmp_path), "--min-orders", "50"])
    out = capsys.readouterr().out
    assert code == 0
    assert "3,000" in out and wrl.FLAGGED in out


def test_main_reports_missing_files(tmp_path, capsys):
    code = wrl.main([str(tmp_path)])
    assert code == 2
    assert "olist_orders_dataset.csv" in capsys.readouterr().err


def test_main_rejects_a_bad_alpha(tmp_path, capsys):
    write_olist_files(tmp_path, marketplace(CLUSTER_PLAN))
    assert wrl.main([str(tmp_path), "--alpha", "2"]) == 2
    assert "alpha" in capsys.readouterr().err


def test_main_reports_an_empty_dataset(tmp_path, capsys):
    frames = tables([spec("shipped", status="shipped", delivered_at=None)])
    write_olist_files(tmp_path, frames)
    assert wrl.main([str(tmp_path)]) == 2
    assert "delivered" in capsys.readouterr().err
