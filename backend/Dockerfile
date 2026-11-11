FROM python:3.13-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt opentelemetry-exporter-otlp-proto-http
COPY . .
RUN useradd -m careflow && chown -R careflow /app
USER careflow
EXPOSE 8000
# PORT is set by hosts such as Render; locally and in compose it defaults to 8000.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
