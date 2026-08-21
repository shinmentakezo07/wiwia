"""Config loader tests."""

from textwrap import dedent

import pytest

from wiwi.config import ConfigError, load_config


def test_load_and_interpolate(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-123")
    p = tmp_path / "wiwi.yaml"
    p.write_text(dedent("""
    providers:
      - name: openai-main
        provider: openai
        keys: [{label: main, key: os.environ/TEST_KEY}]
    model_list:
      - model_name: gpt-4o
        wiwi_params: {provider: openai-main, model: gpt-4o}
    general_settings:
      master_key: os.environ/TEST_KEY
    """))
    cfg = load_config(p)
    assert cfg.providers[0].keys[0].key == "sk-123"
    assert cfg.general_settings.master_key == "sk-123"


def test_missing_env_var_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFINITELY_NOT_SET_XYZ", raising=False)
    p = tmp_path / "wiwi.yaml"
    p.write_text("providers:\n  - name: x\n    provider: openai\n"
                 "    keys: [{key: os.environ/DEFINITELY_NOT_SET_XYZ}]\n")
    with pytest.raises(ConfigError, match="DEFINITELY_NOT_SET_XYZ"):
        load_config(p)


def test_unknown_provider_reference(tmp_path):
    from wiwi.router.router import Router

    p = tmp_path / "wiwi.yaml"
    p.write_text("model_list:\n  - model_name: gpt-4o\n"
                 "    wiwi_params: {provider: nope, model: gpt-4o}\n")
    cfg = load_config(p)  # config loads fine; reference validated at router build
    with pytest.raises(ValueError, match="unknown provider"):
        Router(cfg)


def test_provider_requires_keys(tmp_path):
    p = tmp_path / "wiwi.yaml"
    p.write_text("providers:\n  - name: x\n    provider: openai\n    keys: []\n")
    with pytest.raises(ConfigError):
        load_config(p)
