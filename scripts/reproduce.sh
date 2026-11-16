#!/usr/bin/env bash
# Rebuild the sites, run every system and write the tables.
# With a filled .cache folder this makes no API calls. Without it, it needs
# ANTHROPIC_API_KEY and spends a few dollars (see README).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
uv sync --all-extras
uv run playwright install chromium
uv run python scripts/run_experiments.py
uv run python scripts/report.py
uv run python scripts/live_quotes.py
