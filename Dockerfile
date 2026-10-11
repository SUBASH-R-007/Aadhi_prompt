# syntax=docker/dockerfile:1.7
# Aadhi EduEngine v2 image: API, job worker and render worker (one image, different commands).
#
#   docker build -t aadhi-eduengine .                         # default target: app
#   docker build --target worker-docker -t aadhi-eduengine:docker-cli .   # + docker CLI (MANIM_SANDBOX=docker)
#   docker build --target test -t aadhi-eduengine:test .      # + test tools: CI's container suites (never deployed)
#   docker compose up -d                                      # see docker-compose.yml, docs/OPERATIONS.md
#
# Stages: web (npm ci -> web/vendor) | pydeps (runtime venv: requirements.txt pinned by
#         requirements.lock, build tools discarded) | runtime (ffmpeg, cairo/pango, TeX Live for
#         MathTex, Noto + Indic fonts, Chromium) | app | test (runtime + test tools, CI only)
# The render worker's Chromium keeps its sandbox: run it with docker/app/chromium-seccomp.json
# (see docker-compose.yml `render-worker` and docs/OPERATIONS.md section 4.2).

ARG PYTHON_VERSION=3.11
ARG NODE_VERSION=22
ARG DOCKER_CLI_IMAGE=docker:27-cli

# ---- 1. self-hosted frontend libraries and fonts --------------------------------------------------
FROM node:${NODE_VERSION}-slim AS web
WORKDIR /src
COPY package.json package-lock.json ./
COPY scripts/vendor.mjs scripts/vendor.mjs
# `npm ci` runs the postinstall hook (scripts/vendor.mjs), which fills web/vendor.
RUN npm ci --no-audit --no-fund && test -f web/vendor/manifest.json

# ---- 2. python dependencies (compilers stay in this stage) ----------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS pydeps
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential pkg-config libcairo2-dev libpango1.0-dev \
 && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
# Runtime packages only (no pytest/ruff), versions pinned by the lock used as a constraints file;
# Linux-only dependencies such as uvloop (uvicorn[standard]) are pinned there with markers.
COPY requirements.txt requirements.lock /tmp/
RUN pip install -c /tmp/requirements.lock -r /tmp/requirements.txt boto3

# ---- 3. runtime ------------------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH=/opt/venv/bin:$PATH \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    HOME=/home/aadhi \
    APP_ENV=production \
    WORKER_MODE=external \
    DATA_DIR=/data/app \
    STORAGE_LOCAL_DIR=/data/storage

# ffmpeg (renders), cairo/pango (manim), minimal TeX Live for MathTex, Noto fonts incl. Tamil,
# Devanagari, Telugu, Kannada and Malayalam (manim labels + caption burn-in), tini as PID 1.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libcairo2 libpango-1.0-0 libpangocairo-1.0-0 \
      texlive-latex-base texlive-latex-extra texlive-fonts-recommended texlive-science dvisvgm cm-super \
      fonts-noto-core fonts-noto-ui-core fonts-noto-mono \
      ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=pydeps /opt/venv /opt/venv
# Chromium for the render worker (render-mode screenshots); --with-deps installs its system libraries.
RUN python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/* /tmp/*

RUN groupadd --gid 1000 aadhi \
 && useradd --uid 1000 --gid 1000 --create-home --home-dir /home/aadhi --shell /usr/sbin/nologin aadhi \
 && mkdir -p /data/app /data/storage \
 && chown -R aadhi:aadhi /data

WORKDIR /app
# Application code is owned by root and read-only for the service user. Allow-list only: nothing
# else from the checkout (v1 leftovers, docs, tests, evals, local data) can end up in the image.
COPY aadhi/ /app/aadhi/
COPY alembic/ /app/alembic/
COPY alembic.ini server.py /app/
COPY web/ /app/web/
COPY docker/app/ /app/docker/app/
COPY video_template/aadhi_*.mp4 video_template/no_aadhi*.mp4 video_template/logo_animation*.mp4 \
     video_template/bgm.mp3 video_template/static_background.png /app/video_template/
# Mascot poster frames (/branding/posters/<clip>.jpg): the live player's still fallback for a clip.
COPY video_template/posters/ /app/video_template/posters/
COPY --from=web /src/web/vendor /app/web/vendor
RUN sed -i 's/\r$//' docker/app/entrypoint.sh \
 && chmod 0755 docker/app/entrypoint.sh \
 && python -m compileall -q aadhi

USER aadhi
EXPOSE 8000
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker/app/entrypoint.sh"]
CMD ["uvicorn", "aadhi.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# ---- optional: worker that can start sibling sandbox containers (MANIM_SANDBOX=docker) ---------------
FROM ${DOCKER_CLI_IMAGE} AS dockercli

FROM runtime AS worker-docker
USER root
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
USER aadhi

# ---- test: the runtime image plus the test tools (CI's container suites; never deployed) -------------------
# The fast and slow suites, the ffmpeg 5.1 feature checks and the classic invariance harness (tests/invariance:
# goldens and the differential pixel test) run in here with the production tools (Debian's ffmpeg 5.1, the pinned
# Chromium, TeX Live, the fonts), against a checkout mounted at /src: tests, docs and evals stay out of every image
# (.dockerignore) and `npm ci` on the host provides web/vendor. The stages above stay free of pytest
# (docs/DYNAMIC_VIDEO_DESIGN.md sections 3.6, 7.9 and 10.2; .github/workflows/ci.yml job `container`):
#   docker build --target test -t aadhi-eduengine:test .
#   docker run --rm --shm-size=1g --security-opt seccomp=docker/app/chromium-seccomp.json \
#     --user "$(id -u):$(id -g)" -v "$PWD:/src" aadhi-eduengine:test python -m pytest -m "not slow and not network"
FROM runtime AS test
USER root
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.lock requirements-dev.txt /tmp/
RUN pip install -c /tmp/requirements.lock -r /tmp/requirements-dev.txt \
 && python -c "import pytest, pytest_asyncio, respx, yaml, packaging" \
 && rm -f /tmp/requirements.lock /tmp/requirements-dev.txt
# The tests choose their own data dirs (tests/conftest.py): the production paths of the image must not reach
# them, and any uid (CI runs as the runner's own) needs a writable HOME. AADHI_EXPECT_FFMPEG pins the ffmpeg
# version the `container` checks expect (tests/compose/test_ffmpeg_features.py): a base-image change that moves
# Debian's ffmpeg off 5.1 fails CI instead of passing unnoticed.
ENV APP_ENV=test \
    STORAGE_LOCAL_DIR="" \
    HOME=/tmp \
    AADHI_CONTAINER_TESTS=1 \
    AADHI_EXPECT_FFMPEG=5.1
WORKDIR /src
USER aadhi
HEALTHCHECK NONE
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "pytest", "-m", "not slow and not network"]

# ---- default target ---------------------------------------------------------------------------------
FROM runtime AS app
