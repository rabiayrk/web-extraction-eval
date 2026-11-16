"""Run generated scraper code in a separate process: python -I sandbox_runner.py code.py"""

import json
import sys
from pathlib import Path


def main() -> None:
    source = Path(sys.argv[1]).read_text()
    payload = json.load(sys.stdin)
    namespace: dict = {}
    exec(compile(source, sys.argv[1], "exec"), namespace)
    out = namespace["extract"](payload["html"], payload["url"])
    json.dump(out, sys.stdout, default=str)


if __name__ == "__main__":
    main()
