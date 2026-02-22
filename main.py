from __future__ import annotations

import sys
from pathlib import Path


def _bootstrap_src_path() -> None:
    repo_root = Path(__file__).resolve().parent
    src_path = repo_root / "src"
    src_text = str(src_path)
    if src_text not in sys.path:
        sys.path.insert(0, src_text)


def main() -> None:
    _bootstrap_src_path()
    from amiagi.main import main as app_main

    app_main(sys.argv[1:])


if __name__ == "__main__":
    main()
