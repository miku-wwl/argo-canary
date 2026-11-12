FROM python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535

WORKDIR /app

COPY requirements.txt .
COPY requirements-lock.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src ./src

EXPOSE 5000

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 10001:10001
STOPSIGNAL SIGTERM
CMD ["gunicorn", "--bind=0.0.0.0:5000", "--workers=1", "--threads=8", "--keep-alive=5", "--graceful-timeout=10", "--worker-tmp-dir=/tmp", "src.app:app"]
