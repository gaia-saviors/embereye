FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=1000

WORKDIR /srv

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install --default-timeout=1000 --retries 10 -r requirements.txt

COPY app/ ./app/

RUN useradd --create-home --shell /usr/sbin/nologin embereye \
    && chown -R embereye:embereye /srv
USER embereye

EXPOSE 8000

# Default: API. The seeder service overrides the command with `python -m app.seed_run`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
