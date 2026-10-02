# Build context is the REPO ROOT (see fly.toml: dockerfile = "server/Dockerfile"), so this can
# reach both the main project's src/ (reused for cost.pricing / llm.sse) and server/ itself.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip  # old pip can't do PEP 660 editable installs

# Reuse cost.pricing / llm.sse from the main project, --no-deps so none of its desktop-only
# dependencies (PySide6, faster-whisper, pyaudiowpatch - Windows GUI/audio, meaningless here and
# partly unavailable on Linux anyway) get pulled in. Metadata only: this container never touches
# PySide6/faster-whisper, just two small provider-agnostic modules.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e . --no-deps

# The relay service. Editable install (not a wheel): app.py finds admin_templates/ via
# Path(__file__).parent, so the source tree stays on disk exactly as laid out here.
COPY server/pyproject.toml ./server/pyproject.toml
COPY server/interview_relay ./server/interview_relay
RUN pip install --no-cache-dir -e ./server

# Metadata-only SQLite db (requests/friends/spend - never a prompt or an answer); the fly.toml
# volume mount keeps it alive across restarts and deploys.
ENV RELAY_DB_PATH=/data/relay.db

EXPOSE 8000
CMD ["uvicorn", "interview_relay.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
