FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

WORKDIR /opt/eternalai

# Match the operator-checked uv version and install only locked production dependencies.
RUN python -m pip install --no-cache-dir uv==0.12.13
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Both API and worker use the repository's explicit synthetic operator wiring.
COPY app/ ./app/
COPY scripts/bootstrap_browser_synthetic.py ./scripts/bootstrap_browser_synthetic.py
COPY scripts/manage_browser_synthetic_publication.py ./scripts/manage_browser_synthetic_publication.py
COPY scripts/run_browser_synthetic_api.py ./scripts/run_browser_synthetic_api.py
COPY scripts/run_browser_synthetic_worker.py ./scripts/run_browser_synthetic_worker.py

# A future explicitly mounted task volume inherits this private parent ownership.
# Bootstrap alone creates the task directory; no keys or registrations enter the image.
RUN mkdir -p /run/browser-synthetic-secrets \
    && chown pwuser:pwuser /run/browser-synthetic-secrets \
    && chmod 0700 /run/browser-synthetic-secrets

ENV PATH="/opt/eternalai/.venv/bin:${PATH}" \
    PYTHONPATH="/opt/eternalai" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER pwuser
ENTRYPOINT ["python", "-m", "scripts.run_browser_synthetic_worker"]
