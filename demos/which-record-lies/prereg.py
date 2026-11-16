"""Constants fixed by the pre-registration PRE-N0-01 (signed 2026-11-15).

Everything the run needs to be the same run for anyone lives here and nowhere else.
Changing a value is an amendment of the pre-registration, not an edit.
"""

SEED = 20261115
SAMPLE_SIZE = 100

# SHA-256 of the three Olist files the pre-registration was written against.
EXPECTED_SHA256 = {
    "olist_orders_dataset.csv": "8df58ef3d2d7e9944010f7beecd9b75367f5588ec6e3c91cec19ae3345ef9ecf",
    "olist_order_reviews_dataset.csv": "012b61c7593e34f51fa614efdf802b9c7056ce6aae5307ddb93236e7cfc797d7",
    "olist_order_items_dataset.csv": "0bc4d068c4fe38cbb01bd90e8746e3c613fe7b4baef75fab7b0e329701c3e279",
}

# Annex B (blind predictions about delivery times) also reads the sellers file.
EXPECTED_SHA256_B = {
    "olist_orders_dataset.csv": EXPECTED_SHA256["olist_orders_dataset.csv"],
    "olist_order_items_dataset.csv": EXPECTED_SHA256["olist_order_items_dataset.csv"],
    "olist_sellers_dataset.csv": "1f643d2b950373b85735e7794b20986f528d7a000432e7c6f9bcbb44d0846a0e",
}
GESTALT_MIN_HITS = 3       # of 4 questions
GESTALT_WINDOW = 2.0       # B3 counts as a hit within 2 percentage points
GESTALT_MIN_ORDERS = 1000  # B4 only compares seller states with this many orders

# Label rubric for the blind samples. Unreadable counts as O.
LABELS = ("N", "P", "O")   # N: nothing arrived · P: arrived incomplete, different or damaged · O: other reason

# Pass criteria (section 6 of the pre-registration).
H_N1_MIN_WILSON = 0.80     # late sample: lower 95% Wilson bound of the share labeled N
H_N2_MIN_WILSON = 0.40     # conflict sample: same bound
H_N3_MIN_SHARE = 0.40      # avoided disputes, as a share of the naive count and of its BRL value
LABELER_AGREEMENT_MIN = 0.85   # model-assisted labeling needs this agreement with the owner on 30 random orders
