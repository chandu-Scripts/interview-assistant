# Step 1 — Project setup
Built: src layout, pyproject (PySide6, pydantic-settings, dev tools), venv, Settings (SecretStr keys),
logging, hello-world PySide6 window, smoke tests, scripts/check.py (ruff + mypy + pytest), README, .gitignore, .env.example.
Tested: check.py passes (3 tests); app launched and window "Interview Assistant" confirmed visible.
Notes: Python 3.14.5, PySide6 6.11.2. Set QT_QPA_PLATFORM=offscreen for headless test runs if needed.
