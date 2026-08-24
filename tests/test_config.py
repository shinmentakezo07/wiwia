"""Config loader tests."""

from textwrap import dedent

import pytest

from wiwi.config import ConfigError, load_config, load_config_from_string


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
    p = tmp_path / "wiwi.yaml"
    p.write_text("model_list:\n  - model_name: gpt-4o\n"
                 "    wiwi_params: {provider: nope, model: gpt-4o}\n")
    # fail fast at load with a clean CLI error (no traceback from router build)
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config(p)


def test_provider_requires_keys(tmp_path):
    p = tmp_path / "wiwi.yaml"
    p.write_text("providers:\n  - name: x\n    provider: openai\n    keys: []\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_load_config_from_string(monkeypatch):
    monkeypatch.setenv("TEST_KEY", "sk-123")
    raw = dedent("""
    providers:
      - name: openai-main
        provider: openai
        keys: [{label: main, key: os.environ/TEST_KEY}]
    model_list:
      - model_name: gpt-4o
        wiwi_params: {provider: openai-main, model: gpt-4o}
    general_settings:
      master_key: os.environ/TEST_KEY
    """)
    cfg = load_config_from_string(raw)
    assert cfg.providers[0].keys[0].key == "sk-123"
    assert cfg.general_settings.master_key == "sk-123"


def test_load_config_from_string_invalid():
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_config_from_string("{not: valid: yaml")
    with pytest.raises(ConfigError, match="YAML mapping"):
        load_config_from_string("- just\n- a\n- list")


def test_load_config_from_string_unknown_provider():
    raw = "model_list:\n  - model_name: gpt-4o\n" \
          "    wiwi_params: {provider: nope, model: gpt-4o}\n"
    with pytest.raises(ConfigError, match="unknown provider"):
        load_config_from_string(raw)
