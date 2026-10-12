"""Never apply the maintained client patch to an unsupported upstream revision."""

import importlib.util
from pathlib import Path

import pytest


@pytest.mark.parametrize("drifted", ["a.js", "b.js"])
def test_client_patch_rejects_upstream_drift_before_writing(tmp_path, drifted):
    script = Path(__file__).resolve().parents[1] / "integrations/anythingllm/build.py"
    spec = importlib.util.spec_from_file_location("anythingllm_build", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    builder.FILES = dict.fromkeys(
        ["a.js", "b.js"], "4141c9b832751f516aa6c616f2e02745f380dfa71dbdadcb388cc4a3eca4ff39"
    )
    original = {}
    for relative in builder.FILES:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        original[relative] = (
            b"unsupported upstream source\n"
            if relative == drifted
            else b"supported upstream source\n"
        )
        target.write_bytes(original[relative])
    with pytest.raises(ValueError, match="Unsupported AnythingLLM source"):
        builder.apply(tmp_path)
    assert all(
        (tmp_path / relative).read_bytes() == original[relative] for relative in builder.FILES
    )
