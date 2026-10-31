FROM python:3.12-slim
WORKDIR /srv
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY app ./app
COPY web ./web
ENV PATH="/srv/.venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
