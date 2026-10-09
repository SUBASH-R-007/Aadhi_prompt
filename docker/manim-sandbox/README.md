# Manim sandbox image

Container used by `aadhi.manim.sandbox.DockerRunner` to render Manim scenes in production
(`MANIM_SANDBOX=docker`). Free-form (LLM-written) Manim code is refused in production unless this
sandbox is used (`Settings.validate_for_runtime`).

## Build

```bash
docker build -t aadhi-manim-sandbox:latest docker/manim-sandbox
# pin a different Manim release (keep it equal to the app's manim version):
docker build --build-arg MANIM_VERSION=v0.21.0 -t aadhi-manim-sandbox:0.21.0 docker/manim-sandbox
```

Configure the app with:

```
MANIM_SANDBOX=docker
MANIM_DOCKER_IMAGE=aadhi-manim-sandbox:latest
```

The worker that renders Manim needs access to a Docker daemon (`docker` CLI on PATH, or
`DOCKER_HOST`). Only `DOCKER_*` variables and a small allow-list (PATH, HOME, ...) are passed to the
CLI; API keys never reach the container.

## How it is run

For every render the app creates a private temp directory (mode 0700) with a world-writable `w/`
leaf (so the container's uid 1000 can write regardless of the worker's uid, while other host users
cannot reach it), writes `w/scene.py`, and runs:

```
docker run --rm --name aadhi-manim-<random>
  --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=512m
  --memory 2g --memory-swap 2g --cpus 2 --pids-limit 256
  --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges
  -v <tmpdir>/w:/work:rw -w /work
  -e HOME=/tmp -e AADHI_TEX_FLAGS=-no-shell-escape ...
  aadhi-manim-sandbox:latest
  python -m manim render -q<l|m|h> --format mp4 --disable_caching --media_dir /work/media -o out
         --progress_bar none -v WARNING --silent /work/scene.py <SceneClass>
```

On timeout (`MANIM_TIMEOUT_SECONDS`) the container is `docker kill`ed and the CLI process tree is
terminated. The output video is moved out of `/work/media/videos/**/out.mp4`, the temp directory is
deleted, and the video is re-timed to the exact scene duration with ffmpeg on the host (frozen last
frame / trim). The `docker` CLI itself only receives `DOCKER_*` and an allow-list of variables.

## Defence in depth

1. `aadhi.manim.guard.check_code` - AST allow-list: imports, builtins, dunder/underscore access,
   module objects (deny by default: `np`, `math`, `rate_functions`... expose only allow-listed
   numeric attributes and cannot be passed around), file/sound/image APIs, config mutation, TeX
   file macros. Template output is checked with `check_template_source` (`PARAMS` is a validated
   literal, the static scene file gets the full rules).
2. The injected runtime (`aadhi/manim/runtime/scene_runtime.py`) narrows `from manim import *` to
   an allow-list (mobjects, animations, scenes, colours, constants, maths helpers; no
   `capture`/`guarantee_empty_existence`/`open_file`/`SVGMobject`/`ImageMobject`/`Typst`..., and no
   module objects besides `np` and `rate_functions`), refuses `Code(code_file=...)` and
   `add_sound`, re-checks every TeX expression before LaTeX runs and refuses custom TeX templates.
3. This container: no network, read-only root, no capabilities, unprivileged user, resource limits.

With `MANIM_SANDBOX=subprocess` there is no layer 3: only layers 1 and 2 stand between model-written
code and the files of the user running the app. Use docker (or `MANIM_ALLOW_FREEFORM=false`)
anywhere other than a developer machine.

## Smoke test

```bash
docker run --rm --network none --read-only --tmpfs /tmp --user 1000:1000 aadhi-manim-sandbox:latest
# -> Manim Community v0.21.0
docker run --rm aadhi-manim-sandbox:latest fc-list | grep -E "Noto Sans (Tamil|Devanagari)"
```

## Local development without Docker

`MANIM_SANDBOX=subprocess` (default in development) runs Manim from the app's virtualenv with an
environment allow-list, a temp working directory, a timeout with process-tree kill and a memory
ceiling. On Windows with MiKTeX, LaTeX runs with `-disable-installer` and dvisvgm with
`--miktex-disable-installer`, so a missing package fails immediately instead of opening the
"install package?" dialog (or stalling until `MANIM_TIMEOUT_SECONDS`); templates then fall back to
plain-text maths, and the LaTeX version is rendered again automatically once the package is
installed. Install missing packages with the MiKTeX console, or make "never install" the default
for every MiKTeX program with `initexmf --set-config-value=[MPM]AutoInstall=0`.
