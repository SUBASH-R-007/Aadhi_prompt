"""``scripts/dev_demo.py`` never uses a billable key: env keys are blanked and saved keys are off."""

from __future__ import annotations

import importlib.util

from aadhi.config import ROOT_DIR


def test_dev_demo_disables_every_key_source():
    spec = importlib.util.spec_from_file_location("dev_demo_under_test", ROOT_DIR / "scripts" / "dev_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # guarded by __main__: nothing starts
    forced = module.FORCED
    assert forced["STORED_API_KEYS_ENABLED"] == "false"
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEYS", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        assert forced[name] == ""
    assert forced["LLM_PROVIDER"] == "fake"
