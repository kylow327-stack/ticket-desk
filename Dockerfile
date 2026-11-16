# Demo image for Cloud Run. Do not bake secrets into this image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8080

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
COPY data/tickets.json data/orders.json data/policies.json ./data/
COPY evals ./evals

EXPOSE 8080
CMD ["sh", "-c", "uvicorn ticket_agent.app:app --host 0.0.0.0 --port ${PORT}"]
