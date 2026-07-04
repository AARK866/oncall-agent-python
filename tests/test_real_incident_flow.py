from app.config import settings
from scripts.check_real_incident_flow import _is_success, _required_tools, _summarize_response


def test_required_tools_include_gitlab_only_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gitlab_base_url", None)
    monkeypatch.setattr(settings, "gitlab_project_id", None)

    assert _required_tools() == ["query_metrics", "query_logs", "query_recent_commits"]

    monkeypatch.setattr(settings, "gitlab_base_url", "https://gitlab.example.com")
    monkeypatch.setattr(settings, "gitlab_project_id", "123")

    assert _required_tools() == [
        "query_metrics",
        "query_logs",
        "query_recent_commits",
        "query_deployments",
    ]


def test_real_incident_flow_summary_marks_failed_required_tools(monkeypatch) -> None:
    monkeypatch.setattr(settings, "gitlab_base_url", None)
    monkeypatch.setattr(settings, "gitlab_project_id", None)
    metadata = {
        "service": "payment-api",
        "runbook_retrieved_count": 2,
        "tool_results": [
            {
                "tool_name": "query_metrics",
                "success": True,
                "data": {"provider": "prometheus"},
            },
            {
                "tool_name": "query_logs",
                "success": False,
                "error": "loki unavailable",
                "data": {},
            },
            {
                "tool_name": "query_recent_commits",
                "success": True,
                "data": {"provider": "github"},
            },
        ],
    }

    summary = _summarize_response(metadata, "payment 5xx")

    assert summary["failed_required_tools"] == ["query_logs"]
    assert _is_success(summary, allow_tool_failures=False) is False
    assert _is_success(summary, allow_tool_failures=True) is True
