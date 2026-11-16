"""Run systems on the synthetic sites.

  uv run python scripts/run_experiments.py --systems handwritten --fetch http --smoke
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from wxeval.runner import run
from wxeval.sites.variants import SMOKE_VARIANTS, VARIANTS
from wxeval.systems import registry

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", default=",".join(registry.DEFAULT))
    ap.add_argument("--kinds", default="shop,jobs")
    ap.add_argument("--seeds", default="1,2,3")
    ap.add_argument("--variants", default=",".join(VARIANTS))
    ap.add_argument("--fetch", default="browser", choices=["browser", "http"])
    ap.add_argument("--smoke", action="store_true", help="few variants, one seed")
    ap.add_argument("--results", default=str(ROOT / "results"))
    ap.add_argument("--budget", type=float,
                    default=float(os.environ.get("WXEVAL_BUDGET_USD", "20")))
    args = ap.parse_args()
    variants = SMOKE_VARIANTS if args.smoke else args.variants.split(",")
    seeds = [1] if args.smoke else [int(s) for s in args.seeds.split(",")]
    systems = [registry.load(name) for name in args.systems.split(",")]
    run(systems, args.kinds.split(","), variants, seeds, ROOT / "build", Path(args.results),
        ROOT / ".cache", args.budget, args.fetch)


if __name__ == "__main__":
    main()
