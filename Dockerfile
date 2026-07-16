FROM python:3.13.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.lock .
RUN pip install --no-cache-dir --requirement requirements.lock

COPY --chown=app:app . .

USER app
EXPOSE 10000

CMD ["python", "bot.py"]
