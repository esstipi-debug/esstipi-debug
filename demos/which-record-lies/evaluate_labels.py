"""Turn the blind labels into the verdict on H-N1 and H-N2 of PRE-N0-01.

    python evaluate_labels.py out labels.csv

labels.csv has the columns order_id, label (N, P or O) and by (who labeled). Anyone can relabel the two
muestra_*.csv files of a run and compare: the shares and the Wilson bounds are recomputed from the file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

import sample


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out_dir, labels_path = Path(args[0]), Path(args[1])
    try:
        key = pd.read_csv(out_dir / "clave_muestras.csv", dtype=str)
        labels = pd.read_csv(labels_path, dtype=str)
        result = sample.evaluate(labels, key)
    except (FileNotFoundError, ValueError, KeyError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 3
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
