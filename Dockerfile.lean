# Lean Dockerfile for the Reverb fork (BubbyWoodz/swingmusic).
# Skips the Nuitka "premium" compilation stage (fragile, not needed for
# the RAM-optimization work) and installs the package directly.
#
# Multi-stage: first builds the Reverb web client from BubbyWoodz/webclient,
# then bundles it as client.zip so the backend serves our UI, not upstream's.

# ---- Stage 1: Build the web client ----
# Uses $BUILDPLATFORM (not the target platform) because the output is
# architecture-independent static files. This avoids slow/fragile QEMU
# emulation when building for arm64.
FROM --platform=$BUILDPLATFORM node:20-slim AS webclient-build

WORKDIR /webclient

RUN apt-get update && \
    apt-get install -y --no-install-recommends git zip && \
    rm -rf /var/lib/apt/lists/*

# Clone the Reverb web client (fork, not upstream)
RUN git clone --depth 1 https://github.com/BubbyWoodz/webclient.git .

RUN npm ci --no-audit --no-fund
RUN npm run build

# Package the built files as client.zip (files at zip root, not nested)
RUN cd dist && zip -qr /client.zip .

# ---- Stage 2: Python backend ----
FROM python:3.11-slim

WORKDIR /app

LABEL "author"="BubbyWoodz"
EXPOSE 1970/tcp
VOLUME /music
VOLUME /config

ENV PYTHONUNBUFFERED=1
ENV SWINGMUSIC_IN_DOCKER=1
ENV HOME=/music

RUN mkdir -p /config/backups /music && ln -sfn /config/backups /music/swingmusic.backup
RUN ln -sfn /music /root/music

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libev-dev \
        ffmpeg \
        libavcodec-extra && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY version.txt /app/version.txt
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY pyproject.toml ./
COPY src/ ./src/

# Bundle the Reverb web UI so the backend serves our client, not upstream's
COPY --from=webclient-build /client.zip /app/src/swingmusic/client.zip

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-deps -e .

ENTRYPOINT ["python", "-m", "swingmusic", "--host", "0.0.0.0", "--config", "/config"]
