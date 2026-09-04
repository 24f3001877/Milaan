# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

FROM python:3.12-slim
RUN useradd --create-home --shell /bin/bash milaan
WORKDIR /app
COPY --chown=milaan:milaan --from=builder /app /app
RUN chown -R milaan:milaan /app
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
USER milaan
EXPOSE 8000
CMD ["uvicorn", "milaan.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
