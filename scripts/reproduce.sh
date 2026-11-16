#!/usr/bin/env bash
# Rebuild the sites, run every system and write the tables.
# LLM answers are cached in .cache, so a second run on the same machine makes no API
# calls. A fresh clone has no cache: it needs ANTHROPIC_API_KEY and spends a few dollars
# (see README). To only rebuild the tables from the stored raw outputs, run
# scripts/rescore.sh instead.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
uv sync --all-extras
uv run playwright install chromium
uv run python scripts/run_experiments.py
uv run python scripts/report.py
uv run python scripts/live_quotes.py
