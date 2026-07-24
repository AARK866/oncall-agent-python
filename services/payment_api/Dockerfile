FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY services/payment_api/requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

COPY services/__init__.py /app/services/__init__.py
COPY services/payment_api /app/services/payment_api

RUN addgroup --system app \
    && adduser --system --ingroup app app \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app

EXPOSE 8010

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8010/ready', timeout=3).read()"

CMD ["python", "-m", "uvicorn", "services.payment_api.main:app", "--host", "0.0.0.0", "--port", "8010"]
