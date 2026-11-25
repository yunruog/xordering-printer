"""PyInstaller entry point: builds XOrderingPrinter.exe (see .github/workflows/release.yml)."""
import sys

from xordering_printer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
