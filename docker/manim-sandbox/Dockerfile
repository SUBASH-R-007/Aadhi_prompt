# Aadhi EduEngine - Manim sandbox image.
#
# Runs scene scripts produced by aadhi.manim (templates or AST-guarded free-form code). The image
# itself is not trusted to contain them: aadhi.manim.sandbox.DockerRunner always starts it with
#   --network none --read-only --tmpfs /tmp --memory 2g --cpus 2 --pids-limit 256
#   --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges
# and mounts only the per-render work directory at /work.
#
# Build:  docker build -t aadhi-manim-sandbox:latest docker/manim-sandbox
# Keep MANIM_VERSION in step with the manim version pinned in requirements.txt (cache keys include it).

ARG MANIM_VERSION=v0.21.0
FROM manimcommunity/manim:${MANIM_VERSION}

USER root

# Noto fonts for every script the lectures use (Latin, Tamil, Devanagari, Telugu, Kannada,
# Malayalam, Bengali, Gujarati, Gurmukhi, Odia). Text labels pick them by Unicode script.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-core fonts-noto-ui-core fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

# The DockerRunner wraps the render in `timeout -s KILL <deadline>` (coreutils) so an orphaned sibling
# container dies even if the worker's docker handle is lost. Fail the build if it is ever missing.
RUN command -v timeout >/dev/null || { echo "coreutils 'timeout' is required in the sandbox image" >&2; exit 1; }

# Unprivileged runtime user 1000:1000 (the official image already ships uid 1000 as "manimuser").
RUN (getent group 1000 >/dev/null || groupadd -g 1000 aadhi) \
    && (getent passwd 1000 >/dev/null || useradd -m -u 1000 -g 1000 aadhi) \
    && mkdir -p /work && chown 1000:1000 /work

# The root filesystem is mounted read-only at runtime: everything writable lives in /tmp (tmpfs)
# or /work (the bind-mounted render directory).
ENV HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    MPLCONFIGDIR=/tmp \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NO_COLOR=1 \
    AADHI_TEX_FLAGS=-no-shell-escape

WORKDIR /work
USER 1000:1000

ENTRYPOINT []
CMD ["python", "-m", "manim", "--version"]
