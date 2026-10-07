# =============================================================================
# VIPORDER.COM.VN — application image
# =============================================================================
# Build from the REPOSITORY ROOT (the front end lives there too):
#   docker build -f deploy/Dockerfile -t viporder-web:0.1.0 .
#
# The image serves the API only. Static files are served by Nginx from the
# repository checkout (or from a separate image), because a public website
# should not need an application restart to publish a CSS change.
# =============================================================================

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# --- dependencies ----------------------------------------------------------
FROM base AS deps
WORKDIR /install
COPY backend/requirements.txt ./requirements.txt
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# --- runtime ---------------------------------------------------------------
FROM base AS runtime

RUN groupadd --system --gid 10001 viporder \
 && useradd --system --uid 10001 --gid viporder --home /srv/viporder viporder \
 && mkdir -p /srv/viporder/app /var/lib/viporder /var/log/viporder \
 && chown -R viporder:viporder /var/lib/viporder /var/log/viporder

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /srv/viporder/app
COPY --chown=viporder:viporder backend/ /srv/viporder/app/

USER viporder

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else 1)"

# Migrations must run before this, as a separate step:
#   docker run --rm --env-file /etc/viporder/viporder.env viporder-web:0.1.0 \
#     alembic upgrade head
#
# --workers 1 IS DELIBERATE. The rate limiter keeps counters in process memory,
#   so each worker enforces the limit independently and N workers make the
#   effective limit N x RATE_LIMIT_ATTEMPTS. Raise this only after moving the
#   limiter to a shared store.
#
# --forwarded-allow-ips=* is acceptable HERE and only here, because the compose
#   service does not publish this port to the host: the sole peer is the nginx
#   container on the private compose network. The systemd unit, which is
#   reachable on loopback, pins this to 127.0.0.1 instead.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--proxy-headers", \
     "--forwarded-allow-ips", "*", \
     "--no-server-header", \
     "--workers", "1"]
