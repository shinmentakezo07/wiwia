"""Round 166: the README's YAML blocks must load.

Two findings from the README sweep of 2026-10-20:

* **#505** — the annotated config's ``stream_resume: off`` is YAML 1.1's
  boolean ``False``, not the string ``"off"``. The field is typed
  ``Literal["off", "content_only", "enabled"]``, so the block that the
  Quickstart points at was rejected at startup with
  ``ConfigError: invalid config at 'router_settings.stream_resume'``.
* **#506** — the same block's ``model_list`` routed ``claude-sonnet`` at a
  provider account ``anthropic-main`` that the block's ``providers:`` list
  never defined, so the validator rejected it too
  (``model 'claude-sonnet' references unknown provider 'anthropic-main'``).

Both are copy-paste failures in the one file a reader is told to copy from.
The four "Recipes" blocks are held to the same bar: each must be a
standalone fragment that loads on its own.

The test extracts every ``yaml`` fence from ``README.md`` and feeds it to
``wiwi.config.load_config`` — the same loader the gateway uses at startup —
so a block that cannot start a gateway cannot land in the README.
"""

from __future__ import annotations

import os
import pathlib
import re
import tempfile

import pytest

from wiwi.config import load_config

README = pathlib.Path(__file__).resolve().parent.parent / "README.md"

# Every provider key the README's blocks interpolate. The loader drops a
# provider whose keys resolve to "", so the env must supply them for the
# blocks to validate.
_PROVIDER_ENV = {
    "OPENAI_API_KEY": "sk-test-openai",
    "OPENAI_API_KEY_2": "sk-test-openai-2",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "GEMINI_API_KEY": "test-gemini",
    "OPENROUTER_API_KEY": "sk-or-test",
    "GMI_API_KEY": "sk-gmi-test",
    "BAI_API_KEY": "sk-bai-test",
    "NVIDIA_NIM_API_KEY": "sk-nv-test",
    "OPENCODE_API_KEY": "sk-oc-test",
}


def _yaml_blocks() -> list[str]:
    """Every ```yaml fence in the README, in document order."""
    return re.findall(r"```yaml\n(.*?)```", README.read_text(), re.DOTALL)


def _config_blocks() -> list[str]:
    """The fences that are meant to be a whole config, not a fragment.

    A block qualifies when it declares providers or a model list — those are
    the ones a reader drops into ``wiwi.yaml``.
    """
    return [
        b
        for b in _yaml_blocks()
        if re.search(r"^\s*(providers|model_list):", b, re.MULTILINE)
    ]


def test_readme_has_yaml_blocks() -> None:
    """Guard against the extractor silently matching nothing."""
    blocks = _yaml_blocks()
    assert len(blocks) >= 5, f"expected the README's config blocks, found {len(blocks)}"
    assert len(_config_blocks()) >= 5


@pytest.mark.parametrize("index", range(10))
def test_every_readme_config_block_loads(
    index: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each block must pass wiwi's own config loader.

    A block that fails here is a block a reader cannot copy: the gateway
    refuses to start on it.
    """
    blocks = _config_blocks()
    if index >= len(blocks):
        pytest.skip(f"only {len(blocks)} config block(s) in the README")

    for name, value in _PROVIDER_ENV.items():
        monkeypatch.setenv(name, value)

    block = blocks[index]
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as fh:
        fh.write(block)
        path = fh.name
    try:
        config = load_config(path)
    finally:
        os.unlink(path)

    # The block is a usable config, not an empty one.
    assert config.providers, "the block declared no resolvable provider"
    assert config.model_list, "the block declared no model group"


def test_annotated_config_quotes_the_off_value() -> None:
    """`stream_resume: off` is YAML's boolean False; the schema wants a string.

    Regression for #505 — the fix is the quotes, and a future edit that
    "tidies" them away would silently re-break the block.
    """
    blocks = _config_blocks()
    annotated = next((b for b in blocks if "stream_resume" in b), None)
    assert annotated is not None, "the annotated config lost its stream_resume line"

    # Bare `off` / `no` / `yes` are YAML booleans, not the strings the
    # Literal schema accepts.
    for line in annotated.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        match = re.match(r"^stream_resume:\s*(\S+)", stripped)
        if not match:
            continue
        value = match.group(1)
        assert value not in ("off", "no", "yes", "false", "true", "on"), (
            f"stream_resume: {value} is a YAML boolean, not a string"
        )
        assert value in ('"off"', "'off'", "content_only", "enabled"), (
            f"stream_resume must be quoted or spelled out, got {value!r}"
        )


def test_annotated_config_resolves_every_provider_it_references() -> None:
    """Regression for #506 — every `wiwi_params.provider` must be declared.

    A reader copying the block gets a gateway that refuses to start if a
    model group points at an account the block never defined.
    """
    blocks = _config_blocks()
    annotated = next(
        (b for b in blocks if "model_list:" in b and "providers:" in b),
        None,
    )
    assert annotated is not None, "the annotated config lost a section"

    declared = set(re.findall(r"^\s*-\s*name:\s*(\S+)", annotated, re.MULTILINE))
    referenced = set(re.findall(r"provider:\s*([A-Za-z0-9_-]+)", annotated))
    # `provider:` also appears as the provider *type* inside `providers:`
    # entries; those are type names, not account names, so only complain
    # about the ones that are not known provider types.
    known_types = {
        "openai", "anthropic", "gemini", "openai-compatible", "openrouter",
        "gmicloud", "bai", "nvidia-nim", "cline", "workbuddy", "opencode",
        "hermes",
    }
    missing = referenced - declared - known_types
    assert not missing, (
        f"the config routes to undefined provider account(s): {sorted(missing)}"
    )
