FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

WORKDIR /opt/eternalai

# Match the operator-checked uv version and install only locked production dependencies.
RUN python -m pip install --no-cache-dir uv==0.12.13
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Both API and worker use the repository's explicit synthetic operator wiring.
COPY app/ ./app/
COPY scripts/manage_browser_synthetic_publication.py ./scripts/manage_browser_synthetic_publication.py
COPY scripts/run_browser_synthetic_api.py ./scripts/run_browser_synthetic_api.py
COPY scripts/run_browser_synthetic_worker.py ./scripts/run_browser_synthetic_worker.py

ENV PATH="/opt/eternalai/.venv/bin:${PATH}" \
    PYTHONPATH="/opt/eternalai" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER pwuser
ENTRYPOINT ["python", "-m", "scripts.run_browser_synthetic_worker"]
