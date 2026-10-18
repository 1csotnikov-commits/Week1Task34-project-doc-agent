"""Тесты провайдеров без сети."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from project_doc_agent.config import load_providers
from project_doc_agent.errors import ProviderError, ProviderResponseError
from project_doc_agent.models import FactsPayload
from project_doc_agent.providers.cache import ResponseCache
from project_doc_agent.providers.openai_compat import OpenAICompatibleProvider


class FakeCompletions:
    def __init__(self, contents: list[str]) -> None:
        self.contents = list(contents)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        content = self.contents.pop(0) if self.contents else ""
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
            model=kwargs.get("model", "fake"),
        )


class FakeClient:
    def __init__(self, contents: list[str]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(contents))


def _provider(name: str, contents: list[str], cache: ResponseCache | None = None):
    config = load_providers().by_name(name)
    client = FakeClient(contents)
    provider = OpenAICompatibleProvider(config, client=client, api_key="test-key", cache=cache)
    return provider, client


def test_all_cloud_providers_use_expected_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    import project_doc_agent.providers.openai_compat as module

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        module,
        "OpenAI",
        lambda **kwargs: captured.update(kwargs) or FakeClient([json.dumps({"facts": []})]),
    )
    for name in ("deepseek", "openrouter", "mistral"):
        config = load_providers().by_name(name)
        OpenAICompatibleProvider(config, api_key="test-key")
        assert captured["base_url"] == config.base_url
        assert captured["api_key"] == "test-key"


def test_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    config = load_providers().by_name("deepseek")
    monkeypatch.delenv(config.api_key_env, raising=False)
    with pytest.raises(ProviderError):
        OpenAICompatibleProvider(config, api_key="")


def test_invalid_json_triggers_one_repair() -> None:
    provider, client = _provider("deepseek", ["not json", json.dumps({"facts": []})])
    payload, response = provider.complete_json(
        system="system",
        user="user",
        schema=FactsPayload,
        purpose="test",
    )
    assert payload.facts == []
    assert len(client.chat.completions.calls) == 2
    assert "JSON" in client.chat.completions.calls[1]["messages"][-1]["content"]


def test_invalid_json_twice_raises() -> None:
    provider, client = _provider("mistral", ["not json", "still not json"])
    with pytest.raises(ProviderResponseError):
        provider.complete_json(system="s", user="u", schema=FactsPayload, purpose="test")
    assert len(client.chat.completions.calls) == 2


def test_cache_hit_avoids_second_call(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path / "cache")
    content = json.dumps({"facts": []})
    provider, client = _provider("openrouter", [content, content], cache=cache)
    first, first_response = provider.complete_json(system="s", user="u", schema=FactsPayload, purpose="test")
    second, second_response = provider.complete_json(system="s", user="u", schema=FactsPayload, purpose="test")
    assert first.facts == second.facts == []
    assert first_response.cached is False
    assert second_response.cached is True
    assert len(client.chat.completions.calls) == 1
    assert list((tmp_path / "cache").rglob("*.json"))
