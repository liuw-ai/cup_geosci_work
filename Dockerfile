FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV APP_DATA_DIR=/var/lib/job-hub

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system jobhub \
    && adduser --system --ingroup jobhub --home /app jobhub

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=jobhub:jobhub . .
RUN mkdir -p /var/lib/job-hub \
    && chown -R jobhub:jobhub /var/lib/job-hub

USER jobhub

EXPOSE 8080

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--threads", "4", "job_hub.wsgi:app"]
