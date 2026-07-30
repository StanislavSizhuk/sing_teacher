# syntax=docker/dockerfile:1

# Debian, not Alpine (unlike api/Dockerfile): torch/demucs/whisper ship
# manylinux wheels built against glibc, not musl -- Alpine would mean
# building them from source, which is what image bloat and CI minutes look
# like. ffmpeg is pinned to the exact package version, mirroring how
# api/Dockerfile pins Alpine's.
#
# trixie (13), not bookworm (12): trixie is Debian's current stable and
# gets priority security backports; bookworm now trails it as the older
# release (verified: 67 unpatched CRITICAL/HIGH CVEs in bookworm's base
# packages vs 88 on the same day, `docker run python:3.12-slim-bookworm
# cat /etc/os-release` vs `python:3.12-slim`, the latter already
# repointed at trixie upstream). CI's Trivy step still runs
# --ignore-unfixed regardless -- neither release has zero *fixable*
# findings as of this writing, this is about carrying fewer currently-
# unpatched CVEs in the image, not about removing that flag.
ARG PYTHON_IMAGE=python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de
ARG FFMPEG_VERSION=7:7.1.5-0+deb13u1
ARG UV_VERSION=0.11.33

FROM ${PYTHON_IMAGE} AS dev
ARG FFMPEG_VERSION
ARG UV_VERSION
WORKDIR /src
RUN apt-get update && apt-get install -y --no-install-recommends "ffmpeg=${FFMPEG_VERSION}" \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock ./
# Split like the builder stage below: dependencies only (cached across most
# rebuilds, since pyproject.toml/uv.lock change far less often than src/),
# then the project itself once src/ exists. A single `uv sync` before COPY
# never installs vocalcoach's own editable package (there is no src/ yet to
# build it from), which left `python -m vocalcoach` failing with "No module
# named vocalcoach" even though every third-party dependency was present.
RUN uv sync --frozen --all-groups --no-install-project
COPY . .
RUN uv sync --frozen --all-groups
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
