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

# Sentry release tag. Empty on a laptop image build. Actions passes the SHA.
ARG GH_SHA=
ENV GH_SHA=${GH_SHA}

EXPOSE 8080

# Proven alc tourniquet: 4 workers × 8 threads, timeout 120, plus the
# socket bounds. Same text as fly.toml [processes].app and
# serve_capacity.image_gunicorn_command(). Do not revert to 1 worker × 2 threads.
# create_app sheds /state for a few seconds after this process loads so a
# restart backlog cannot pin every thread. Prefer a fresh machine launch
# over `fly machines restart` while polls are attached.
CMD ["gunicorn", "--worker-class", "gthread", "--workers", "4", "--threads", "8", "--worker-connections", "32", "--keep-alive", "1", "--timeout", "120", "--graceful-timeout", "30", "--backlog", "64", "--bind", "0.0.0.0:8080", "--chdir", "lms", "app:create_app()"]
