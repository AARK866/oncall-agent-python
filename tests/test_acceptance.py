import argparse
import json

from app.config import settings
from scripts.run_acceptance import (
    AcceptanceCheck,
    _alertmanager_payload,
    _apply_runtime_overrides,
    _print_summary,
)


def test_alertmanager_payload_contains_required_routing_fields() -> None:
    payload = _alertmanager_payload("acceptance-fingerprint")

    assert payload["receiver"] == "oncall-agent"
    assert payload["commonLabels"]["service"] == "payment-api"
    assert payload["commonLabels"]["severity"] == "critical"
    assert payload["alerts"][0]["fingerprint"] == "acceptance-fingerprint"
    assert payload["alerts"][0]["status"] == "firing"


def test_default_runtime_overrides_are_local_safe() -> None:
    args = argparse.Namespace(
        client_timeout=60,
        real_env=False,
        real_tools=False,
        webhook_secret="acceptance-secret",
    )

    settings.llm_provider = "langchain-openai"
    settings.embedding_provider = "openai-compatible"
    settings.knowledge_vector_store = "milvus"
    settings.ops_tool_mode = "real"

    _apply_runtime_overrides(args)

    assert settings.app_env == "local"
    assert settings.api_auth_enabled is False
    assert settings.llm_provider == "mock"
    assert settings.embedding_provider == "hash"
    assert settings.knowledge_vector_store == "in_memory"
    assert settings.knowledge_retriever_mode == "hybrid"
    assert settings.ops_tool_mode == "mock"
    assert settings.webhook_secret == "acceptance-secret"


def test_real_tools_override_keeps_external_ops_enabled() -> None:
    args = argparse.Namespace(
        client_timeout=60,
        real_env=False,
        real_tools=True,
        webhook_secret="acceptance-secret",
    )

    _apply_runtime_overrides(args)

    assert settings.llm_provider == "mock"
    assert settings.embedding_provider == "hash"
    assert settings.knowledge_vector_store == "in_memory"
    assert settings.ops_tool_mode == "real"


def test_print_summary_can_emit_json(capsys) -> None:
    _print_summary(
        [
            AcceptanceCheck(
                name="health",
                status="PASS",
                detail="ok",
                metadata={"version": "0.1.0"},
            )
        ],
        as_json=True,
    )

    output = json.loads(capsys.readouterr().out)
    assert output == [
        {
            "name": "health",
            "status": "PASS",
            "detail": "ok",
            "metadata": {"version": "0.1.0"},
        }
    ]
