FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV LLOVES_DB=/data/lloves.sqlite
ENV HOST=0.0.0.0
ENV PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY lms/requirements.txt lms/requirements.txt
RUN pip install --no-cache-dir -r lms/requirements.txt

COPY . .

# Science 11–12 + HPE 9–12 PDFs (not always in git). Skip if already copied.
RUN python lms/fetch_ontario_curriculum_pdfs.py

EXPOSE 8080

# 1 worker, 8 threads, bounded keepalive. See lms/gunicorn_conf.py.
CMD ["gunicorn", "--config", "lms/gunicorn_conf.py", "--chdir", "lms", "app:create_app()"]
