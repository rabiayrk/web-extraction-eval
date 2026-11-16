#!/usr/bin/env bash
# Rebuild the sites and the tables from results/raw. No API calls, no browser.
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --extra report
uv run python -c "from pathlib import Path; from wxeval.runner import build_all; from wxeval.sites.variants import VARIANTS; build_all(Path('build'), ['shop', 'jobs'], list(VARIANTS), [1, 2, 3])"
uv run python scripts/report.py
