import asyncio

from app.config import settings
from scripts.check_enterprise_stack import _check_config, _redact


def test_enterprise_config_check_reports_missing_real_llm_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "langchain-openai")
    monkeypatch.setattr(settings, "llm_api_key", None)

    result = asyncio.run(_check_config())

    assert result.status == "FAIL"
    assert "LLM_API_KEY" in result.detail


def test_enterprise_stack_redacts_configured_secrets(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_api_key", "secret-value")

    assert _redact("token=secret-value") == "token=***"
