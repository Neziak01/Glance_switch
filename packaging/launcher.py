"""PyInstaller entry script (the package uses relative imports)."""

import sys

from glance_switch.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
