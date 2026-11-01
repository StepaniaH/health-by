# health-by — container image (self-hosted fitness data service: Web + CLI)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install the package (hatchling build; README is referenced by pyproject metadata)
COPY pyproject.toml README.md ./
COPY health_by ./health_by
RUN pip install .

# Run as a non-root user; /data is the persistent volume mount point.
# If the host UID is not 1000, override with `user: "UID:GID"` in compose or chown ./data.
RUN useradd --uid 1000 --create-home healthby \
 && mkdir -p /data \
 && chown healthby:healthby /data
USER healthby

# Default ./data resolves to /data; either generation of DATA env vars can override it.
WORKDIR /

EXPOSE 39441

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:39441/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["health-by", "serve", "--host", "0.0.0.0", "--port", "39441"]
