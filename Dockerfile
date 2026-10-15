FROM python:3.13.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.lock .
# yolab-quote installs from a git tag, and slim images ship without git.
# Install it only for this step and remove it in the same layer.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir --requirement requirements.lock \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

COPY --chown=app:app . .

USER app
EXPOSE 10000

CMD ["python", "bot.py"]
