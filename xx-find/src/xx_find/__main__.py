"""Entry point: `python -m xx_find --version`."""

from __future__ import annotations

import sys

from xx_find import __version__


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        print("xx-find %s" % __version__)
        return 0
    print("usage: python -m xx_find --version", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
