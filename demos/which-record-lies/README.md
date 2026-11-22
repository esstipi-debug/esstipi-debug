# Which record lies?

When a customer says "I never received it" and the system says "delivered", the records disagree. Before judging the customer, two questions come first:

1. **When did the customer write, relative to the recorded delivery?** A claim written before the delivery was recorded describes a late order, not a conflict.
2. **Does what is left concentrate at one origin** more than the base rate allows?

This demo runs both on a public marketplace dataset. It is a triage, not a verdict: "indeterminate" is a valid outcome.

Each order marked delivered with a "not received" review gets one class, by two questions in this order:

| Question | If yes | If no |
|---|---|---|
| Was the review written after the recorded delivery day? | next question | `late_not_a_conflict`: the order was still on its way |
| Is the seller above the 99% Poisson quantile of the base conflict rate? | `conflict_flagged_seller`: look at that origin | `conflict_indeterminate`: standard policy, logged, no penalty |

## Result on a public marketplace

Olist, Brazil, 2016-2018. Run on 2026-11-03 with the three files listed under [Data](#data), default settings.

| | Orders | Share of claims |
|---|---:|---:|
| Delivered orders with a review | 95,824 | |
| ...with a review that says "not received" | 2,708 | 100% |
| `late_not_a_conflict`: written on or before the recorded delivery day | 1,542 | 56.9% |
| `conflict_flagged_seller`: written after, seller above the chance threshold | 73 | 2.7% |
| `conflict_indeterminate`: written after, nothing says where | 1,093 | 40.4% |

What it shows:

- **Most "not received" claims on delivered orders predate the recorded delivery.** 57% are late orders, not suspect customers. A rule that flags "delivered + not received" would have flagged all 1,542 for the wrong reason.
- **The rest, 1,166 orders (1.22%), is a conflict between records, but a crude one.** Hand check of random samples: of 40 conflict messages, 22 say the order did not arrive; 18 are partial deliveries (9), a wrong item or variant (4), an invoice or other matter (4) or a late delivery (1). Of 15 "late" messages, all 15 say the order had not arrived. After the recorded delivery, the free text mixes "nothing arrived" with "something did, but not all of it".
- **Sellers explain little.** 8 of 413 sellers with at least 50 orders sit above the 99% Poisson quantile of the base rate, against at most about 4 expected by chance. Together they hold 73 of the 1,166 conflicts (6.3%) with 2.0% of the orders. A small excess, not enough to blame any seller.

## How it works

1. Keep orders with status `delivered`, a delivery date and at least one review.
2. A review "says not received" if its title or message matches a Portuguese pattern (`NOT_RECEIVED_PATTERN`, accents stripped).
3. The customer's words are dated by `review_answer_timestamp` and compared, at day level, with `order_delivered_customer_date`. On or before the delivery day: late. After it: conflict.
4. Base conflict rate = conflicts / orders. For each seller with at least 50 orders, the threshold is the 99% Poisson quantile of `orders x base rate`. A seller above it is flagged. The seller is the one of the order's first item.
5. With many sellers, `alpha x sellers` flags are expected by chance alone; the script prints that bound next to the count.

## Run it

```bash
pip install -r requirements.txt
python which_record_lies.py path/to/olist
python -m pytest
```

Options: `--alpha 0.01` (flag level) and `--min-orders 50` (sellers with fewer orders are not assessed). Tested with Python 3.13; 68 tests of the triage on synthetic data (`test_which_record_lies.py`), no Olist files needed. The pre-registered audit below adds 38 more (`test_n0.py`, `test_gestalt_b.py`), and 3 more (`test_mutants.py`) catch the three one-line mutants that survived the run of 2026-11-21 (day vs timestamp, default alpha, base rate per seller).

## Pre-registered audit: before and after, in orders and BRL

`run_n0.py` runs the audit pre-registered as PRE-N0-01 on the same three Olist files: the naive rule (every "not received" claim on a delivered order is a delivery dispute) against this triage, valued in BRL, plus two seeded samples (100 late claims and 100 conflicts) exported blind for hand labeling, a negative control (texts shuffled among reviews of the same delivery month) and the sensitivity to alpha.

```bash
pip install -r requirements.lock
python run_n0.py --data path/to/olist --verify       # the 64 characters of the 3 SHA-256
python run_n0.py --data path/to/olist --out out      # about 2 minutes, no network
python verify_n0.py out                              # re-hash every output against MANIFEST.sha256
python evaluate_labels.py out labels.csv             # shares and Wilson bounds from the blind labels
python gestalt_b.py --data path/to/olist --predictions predictions.json   # Annex B: predict first, compute after
```

The seed, the sample size, the thresholds and the file hashes live in `prereg.py` and nowhere else. The run refuses to start if an input file differs from its hash. The output holds no date, path or library version, so two environments give byte-identical files (`python verify_n0.py out_a out_b`).

### Result of PRE-N0-01 (2026-11-21)

Overall verdict: **FAIL (3 of 4 hypotheses passed; `H-N2` failed)**. Published as measured, without moving any threshold.

| Check | Outcome | Measurement |
|---|---|---|
| **H-N3** (effect size) | **PASS** | 1,542 of 2,708 claims avoided (56.94%) and 273,480.76 of 538,266.79 BRL avoided (50.81%), both ≥ 40%. Negative control (same delivery month shuffle): 7.97% late vs 56.94% observed. |
| **H-N4** (reproducibility) | **PASS** | System Python 3.13.12 vs virgin venv built from `requirements.lock`: `verify_n0.py out_a out_b` printed `identical and intact`. |
| **H-N1** (late sample, n=100) | **PASS** | `N = 96`, `P = 0`, `O = 4`; lower 95% Wilson bound **0.9016** (≥ 0.80). |
| **H-N2** (conflict sample, n=100) | **FAIL** | `N = 42`, `P = 48`, `O = 10`; lower 95% Wilson bound **0.3280** (< 0.40). Nearly half of post-delivery "not received" reviews are partial, wrong, or damaged deliveries (`P`), including 5 of 6 in `conflict_flagged_seller`, confirming the free-text limit below. |
| **Annex B** (`gestalt_b.py`, blind predictions) | **FAIL** | **0 of 4** hits against owner predictions sealed before running (`approval`, `approval`, `2.0%`, `SP` vs measured `transit` median 7.10 d, `transit` variance share 85.0%, `6.77%` late, `SC` slowest among 6 states with ≥ 1,000 orders). |
| **Mutants** (5 one-line changes) | Reported | 2 of 5 caught at run time (106 tests); 5 of 5 caught after adding `test_mutants.py` (109 tests). |

**Disclosure on `labels.csv` (Amendment 1, 2026-11-21):** The 200 blind labels published in `labels.csv` (`order_id,label,by` only; no Olist review text) were generated by an isolated blind model (`model_blind_subagent_v1`) without access to `clave_muestras.csv` and audited on 30 random orders by a second isolated blind model (`29/30` agreement, 96.7%, Cohen's kappa 0.931; replacing those 30 labels with the auditor's yields the same verdicts: `H-N1 = true`, `H-N2 = false` at `41/100`). Cross-model agreement checks rubric stability and is **not** a human-owner audit; anyone can relabel `muestra_tardio.csv` and `muestra_conflicto.csv` and rerun `evaluate_labels.py`.

## Data

Not included in this repository. Download the Brazilian E-Commerce Public Dataset by Olist from [Kaggle](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (licence CC BY-NC-SA 4.0: attribution, non-commercial, share alike) and point the script at the folder with these files. The numbers above come from:

| File | Rows | SHA-256 |
|---|---:|---|
| `olist_orders_dataset.csv` | 99,441 | `8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf` |
| `olist_order_reviews_dataset.csv` | 99,224 | `012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7` |
| `olist_order_items_dataset.csv` | 112,650 | `0bc4d068c4fe38cbb01bd90e8746e3c613fe7b4baef75fab7b0e329701c3e279` |

## Limits

- **The text filter is crude.** It cannot tell "nothing arrived" from a partial delivery, a wrong item or a message about an invoice (see the hand check above). Treat the conflict count as an upper bound for "delivered but never arrived". A reliable version needs a claim-type field (nothing arrived / partial / wrong item / damaged), not free text.
- **Which date.** `review_creation_date` is not used: in this data it looks like the day the survey went out (the day after the recorded delivery for 89% of reviews, and never later than the answer). Using it instead would label 44 of the 1,166 conflicts as late. The Kaggle page does not document the columns, so this is read from the data.
- **Day granularity.** A claim on the delivery day counts as late, not as a conflict.
- **Several sellers in one order.** The order is attributed to the seller of its first item.
- **A flagged seller is not a guilty seller.** Product mix and destinations differ, and the Poisson rule assumes the same base rate for everyone.
- **No carrier, route, driver, packing weight or GPS in this dataset.** The carrier and warehouse checks below cannot be tested here.
- **One marketplace, one period.** Nothing here says anything about another company.

## What a company's own data would add

| Record | Fields | Question |
|---|---|---|
| Warehouse | weight at packing closure vs master weight, with a tolerance | Did it leave the building complete? |
| Carrier | delivery scan, route, driver, GPS; claims per route vs its own baseline | Is the route above its normal rate? |
| Claim | type, date, evidence | Late order, partial delivery, empty box, wrong item? |
| Contract | liability limits, deadlines, proof required | What can be recovered, and how? |

## What this is not

- Not a customer score. No per-customer reputation is built or used.
- Not a fraud detector. It separates "late order" from "records disagree" and says where the disagreement concentrates; "indeterminate" means standard policy and no penalty.
- Not evidence about any company's operations.

## Licence

Code: MIT, see [LICENSE](LICENSE). Data: not included; Olist, *Brazilian E-Commerce Public Dataset by Olist*, Kaggle, CC BY-NC-SA 4.0.
