"""PyInstaller entry point (the packages use relative imports, so cli.py cannot be the entry itself)."""
import sys

from vaultweb.cli import main

if __name__ == "__main__":
    sys.exit(main())
