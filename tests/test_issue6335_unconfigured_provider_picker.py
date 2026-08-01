"""Regression tests for the model picker showing unconfigured providers (#6335).

Adopted with upstream PR 6338, which shipped the ``api/config.py`` fix without
tests. A ``providers:`` entry in ``config.yaml`` that carries only metadata
(``openai-api: {name: "OpenAI API"}``) used to be admitted into the picker
purely because its canonical id is a KNOWN provider — even after the API key
was deleted from the environment and from ``auth.json``. The user then saw an
``OpenAI`` group they cannot actually send to.

The fix admits a known-but-routeless config entry only when the provider was
independently detected from a credential source. These tests pin all three
arms: metadata-only + no credentials is rejected, metadata-only + a detected
credential is admitted, and a route-bearing config (``api_key``) is still
admitted on its own (the #604 behaviour the gate must not break).

Mocked at the WebUI boundary (stub ``hermes_cli``, no bundled ``agent``
import) following ``test_issue4324_photon_phantom_providers.py``.
"""

import json
import sys
import types

import api.config as config
import api.profiles as profiles


def _install_fake_hermes_cli(monkeypatch, *, logged_in=()):
    """Stub hermes_cli so provider detection is deterministic and offline.

    ``logged_in`` names the provider ids that hermes auth reports as
    authenticated — the credential-evidence source the #6338 gate consults.
    An empty tuple means no provider has credentials anywhere.
    """
    fake_pkg = types.ModuleType("hermes_cli")
    fake_pkg.__path__ = []

    _logged_in = list(logged_in)
    fake_models = types.ModuleType("hermes_cli.models")
    fake_models.list_available_providers = lambda: [
        {"id": pid, "authenticated": True} for pid in _logged_in
    ]
    fake_models.provider_model_ids = lambda pid: []

    fake_auth = types.ModuleType("hermes_cli.auth")
    # key_source must not read as "gh auth token" — that ambient source is
    # deliberately skipped by the detection loop.
    fake_auth.get_auth_status = lambda pid: (
        {"logged_in": True, "key_source": "env"} if pid in _logged_in else {}
    )

    monkeypatch.setitem(sys.modules, "hermes_cli", fake_pkg)
    monkeypatch.setitem(sys.modules, "hermes_cli.models", fake_models)
    monkeypatch.setitem(sys.modules, "hermes_cli.auth", fake_auth)
    monkeypatch.delitem(sys.modules, "agent.credential_pool", raising=False)
    monkeypatch.delitem(sys.modules, "agent", raising=False)


_CREDENTIAL_ENV_VARS = (
    "OPENAI_API_KEY",
    "HERMES_API_KEY",
    "HERMES_OPENAI_API_KEY",
    "LOCAL_API_KEY",
    "OPENROUTER_API_KEY",
    "API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
)


def _groups_for(monkeypatch, tmp_path, providers_cfg, *, logged_in=()):
    """Run get_available_models() with *providers_cfg* under ``cfg.providers``."""
    _install_fake_hermes_cli(monkeypatch, logged_in=logged_in)
    (tmp_path / "auth.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: tmp_path)

    for var in _CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    config.cfg.clear()
    config.cfg["model"] = {}
    config.cfg["providers"] = providers_cfg
    try:
        config._cfg_mtime = config.Path(config._get_config_path()).stat().st_mtime
    except Exception:
        config._cfg_mtime = 0.0

    config.invalidate_models_cache()
    try:
        result = config.get_available_models()
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()
    return [str(g.get("provider") or "") for g in result.get("groups", [])]


def _mentions_openai(groups):
    return any("openai" in name.lower() for name in groups)


def test_metadata_only_known_provider_is_hidden_without_credentials(
    monkeypatch, tmp_path
):
    """The report: `openai-api: {name: ...}` with no key must not show."""
    groups = _groups_for(
        monkeypatch,
        tmp_path,
        {"openai-api": {"name": "OpenAI API"}},
    )
    assert not _mentions_openai(groups), groups


def test_metadata_only_known_provider_is_shown_when_credential_detected(
    monkeypatch, tmp_path
):
    groups = _groups_for(
        monkeypatch,
        tmp_path,
        {"openai-api": {"name": "OpenAI API"}},
        logged_in=("openai-api",),
    )
    assert _mentions_openai(groups), groups


def test_route_bearing_provider_config_is_still_admitted(monkeypatch, tmp_path):
    """#604: an api_key in config.yaml alone is enough — the gate must not eat it."""
    groups = _groups_for(
        monkeypatch,
        tmp_path,
        {"openai-api": {"name": "OpenAI API", "api_key": "sk-from-config"}},
    )
    assert _mentions_openai(groups), groups
