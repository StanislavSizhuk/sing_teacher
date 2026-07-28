# syntax=docker/dockerfile:1

# Debian, not Alpine (unlike api/Dockerfile): torch/demucs/whisper ship
# manylinux wheels built against glibc, not musl -- Alpine would mean
# building them from source, which is what image bloat and CI minutes look
# like. ffmpeg is pinned to the exact bookworm package version, mirroring
# how api/Dockerfile pins Alpine's.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
ARG FFMPEG_VERSION=7:5.1.9-0+deb12u1
ARG UV_VERSION=0.11.33

FROM ${PYTHON_IMAGE} AS dev
ARG FFMPEG_VERSION
ARG UV_VERSION
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends "ffmpeg=${FFMPEG_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-groups
COPY . .
ENV PATH="/src/.venv/bin:${PATH}"
CMD ["python", "-m", "vocalcoach"]

FROM ${PYTHON_IMAGE} AS builder
ARG UV_VERSION
WORKDIR /src
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
# Dependencies first, source after -- pyproject.toml/uv.lock change far less
# often than src/, so this layer is cached across most rebuilds.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
RUN uv sync --frozen --no-dev

FROM ${PYTHON_IMAGE}
ARG FFMPEG_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends "ffmpeg=${FFMPEG_VERSION}" \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r vocalcoach && useradd -r -g vocalcoach vocalcoach \
    && mkdir -p /data/audio-tmp /data/song-stems /data/model-weights \
    && chown -R vocalcoach:vocalcoach /data
COPY --from=builder /src/.venv /src/.venv
COPY --from=builder /src/src /src/src
ENV PATH="/src/.venv/bin:${PATH}" \
    PYTHONPATH="/src/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TORCH_HOME=/data/model-weights \
    XDG_CACHE_HOME=/data/model-weights \
    NUMBA_CACHE_DIR=/data/model-weights
USER vocalcoach:vocalcoach
WORKDIR /src
ENTRYPOINT ["python", "-m", "vocalcoach"]
