from scripts.check_real_api_flow import _is_success, _summarize_api_response


def test_real_api_flow_summary_requires_real_connector() -> None:
    health = {"ready": True, "tools": ["query_metrics"]}
    response = {
        "mode": "ops",
        "answer": "ok",
        "metadata": {
            "service": "payment-api",
            "runbook_retrieved_count": 2,
            "tool_connector": {"mode": "mock", "connector_name": "mock_ops"},
            "tool_results": [],
        },
    }

    summary = _summarize_api_response(health, response)

    assert summary["connector_mode"] == "mock"
    assert _is_success(summary, allow_tool_failures=False) is False


def test_real_api_flow_summary_passes_when_required_tools_succeed() -> None:
    health = {"ready": True, "tools": ["query_metrics", "query_logs", "query_recent_commits"]}
    response = {
        "mode": "ops",
        "answer": "diagnosis",
        "metadata": {
            "service": "payment-api",
            "runbook_retrieved_count": 2,
            "tool_connector": {"mode": "real", "connector_name": "real_ops"},
            "tool_results": [
                {"tool_name": "query_metrics", "success": True, "data": {"provider": "prometheus"}},
                {"tool_name": "query_logs", "success": True, "data": {"provider": "loki"}},
                {"tool_name": "query_recent_commits", "success": True, "data": {"provider": "github"}},
            ],
        },
    }

    summary = _summarize_api_response(health, response)

    assert summary["failed_required_tools"] == []
    assert _is_success(summary, allow_tool_failures=False) is True
