FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# V5 (Reliability): the stand-in payment gateway ships in the same image but
# runs as its own process (Compose service locally, ECS sidecar in AWS), so
# it can hang or be killed independently of the app. A real integration would
# drop this and point PAYMENT_GATEWAY_URL at the provider.
COPY fake_gateway ./fake_gateway

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
