"""PyInstaller entry point (the package's __main__ uses relative-free absolute imports)."""

import sys

from interview_assistant.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
