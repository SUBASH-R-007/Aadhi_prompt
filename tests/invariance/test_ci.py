"""The container gate exists and blocks: the Docker ``test`` target and the CI job that runs the container suites
(docs/DYNAMIC_VIDEO_DESIGN.md sections 3.6, 7.9 and 10.2)."""

from __future__ import annotations

import yaml

from aadhi.config import ROOT_DIR


def _ci() -> dict:
    return yaml.safe_load((ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))


def test_container_job_runs_every_container_suite_and_blocks() -> None:
    ci = _ci()
    on = ci.get("on", ci.get(True))  # PyYAML reads the bare key `on` as True
    assert "pull_request" in on and "push" in on
    job = ci["jobs"]["container"]
    assert "if" not in job and not job.get("continue-on-error")  # every PR and push, and a failure fails CI
    steps = job["steps"]
    assert not any(s.get("continue-on-error") for s in steps)
    assert steps[0]["uses"].startswith("actions/checkout") and steps[0]["with"]["fetch-depth"] == 0
    build = next(s for s in steps if str(s.get("uses", "")).startswith("docker/build-push-action"))
    assert build["with"]["target"] == "test" and build["with"]["load"] is True and build["with"]["push"] is False
    runs = "\n".join(str(s.get("run", "")) for s in steps)
    assert 'python -m pytest -m "not slow and not network"' in runs  # fast suite (classic goldens included)
    assert 'python -m pytest -m "container and not network"' in runs  # the ffmpeg 5.1 checks (marker `container`)
    assert 'python -m pytest -m "slow and not network and not container"' in runs  # the rest of the slow suite
    assert "tests/invariance/differential.py --base-root /base --head-root /src" in runs
    assert "git merge-base" in runs and "git archive" in runs
    assert "seccomp=docker/app/chromium-seccomp.json" in runs and "apparmor_restrict_unprivileged_userns=0" in runs
    assert "classic-change" in str(job)  # the declared-change escape hatch is a reviewed label


def test_the_classic_change_label_is_read_live() -> None:
    """The label works on re-runs and on the push after a merge: it is read at run time (step ``declared``), never
    from the event payload, and adding it starts a fresh run."""
    ci = _ci()
    on = ci.get("on", ci.get(True))
    assert {"labeled", "unlabeled", "opened", "synchronize", "reopened"} <= set(on["pull_request"]["types"])
    job = ci["jobs"]["container"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    steps = job["steps"]
    ids = [s.get("id") for s in steps]
    assert ids.index("base") < ids.index("declared")
    declared = steps[ids.index("declared")]
    assert "gh api" in declared["run"] and "Classic-Change" in declared["run"] and "classic-change" in declared["run"]
    assert declared["env"]["GH_TOKEN"] == "${{ github.token }}"
    differential = next(s for s in steps if "differential.py" in str(s.get("run", "")))
    assert differential["env"]["DECLARED"] == "${{ steps.declared.outputs.declared }}"
    assert "steps.declared.outputs.declared == 'true'" in next(s for s in steps if s.get("name") == "Upload the invariance report")["if"]
    assert "github.event.pull_request.labels" not in str(job)  # the payload is stale on re-runs and absent on pushes


def test_burned_captions_are_compared_when_their_path_changes() -> None:
    steps = _ci()["jobs"]["container"]["steps"]
    base = next(s for s in steps if s.get("id") == "base")
    assert "burn=true" in base["run"] and "aadhi/compose/captions.py" in base["run"]
    burn = next(s for s in steps if "--burn-captions" in str(s.get("run", "")))
    assert "differential.py" in burn["run"] and "steps.base.outputs.burn == 'true'" in burn["if"]
    assert burn["env"]["DECLARED"] == "${{ steps.declared.outputs.declared }}"
    assert not burn.get("continue-on-error")


def test_dockerfile_test_target() -> None:
    text = (ROOT_DIR / "Dockerfile").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert text.rstrip().splitlines()[-1] == "FROM runtime AS app"  # still the default target
    start = text.index("FROM runtime AS test\n")
    stage = text[start:text.index("FROM runtime AS app")]
    assert "pip install -c /tmp/requirements.lock -r /tmp/requirements-dev.txt" in stage
    assert "AADHI_CONTAINER_TESTS=1" in stage and 'STORAGE_LOCAL_DIR=""' in stage and "HOME=/tmp" in stage
    assert "AADHI_EXPECT_FFMPEG=5.1" in stage  # the container checks pin Debian bookworm's ffmpeg
    assert 'ENTRYPOINT ["/usr/bin/tini", "--"]' in stage and "USER aadhi" in stage
    assert "requirements-dev" not in text[:start]  # the deployable stages never see the dev tools
