import httpx

from scripts.run_full_payment_incident_drill import (
    ALERT_NAME,
    _latest_alert_tasks,
    _new_alert_group,
    _prometheus_alert_state,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_prometheus_alert_state_returns_target_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/alerts"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "alerts": [
                        {
                            "labels": {"alertname": "UnrelatedAlert"},
                            "state": "firing",
                        },
                        {
                            "labels": {"alertname": ALERT_NAME},
                            "state": "pending",
                        },
                    ]
                },
            },
        )

    with _client(handler) as client:
        assert (
            _prometheus_alert_state(
                client,
                "http://prometheus:9090",
            )
            == "pending"
        )


def test_prometheus_alert_state_returns_none_when_absent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "success", "data": {"alerts": []}},
        )

    with _client(handler) as client:
        assert (
            _prometheus_alert_state(
                client,
                "http://prometheus:9090",
            )
            is None
        )


def test_new_alert_group_ignores_baseline_task() -> None:
    groups = [
        {
            "group_id": "old-group",
            "labels": {"alertname": ALERT_NAME},
            "latest_task_id": "old-task",
        },
        {
            "group_id": "other-group",
            "labels": {"alertname": "UnrelatedAlert"},
            "latest_task_id": "new-task",
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/alerts/groups"
        return httpx.Response(200, json=groups)

    with _client(handler) as client:
        baseline = _latest_alert_tasks(
            client,
            "http://agent:8000",
            {},
        )
        assert baseline == {"old-task", "new-task"}
        assert (
            _new_alert_group(
                client,
                "http://agent:8000",
                {},
                {"old-task"},
            )
            is None
        )


def test_new_alert_group_returns_new_target_task() -> None:
    expected = {
        "group_id": "payment-group",
        "labels": {"alertname": ALERT_NAME},
        "latest_task_id": "new-payment-task",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "group_id": "old-group",
                    "labels": {"alertname": ALERT_NAME},
                    "latest_task_id": "old-task",
                },
                expected,
            ],
        )

    with _client(handler) as client:
        assert _new_alert_group(
            client,
            "http://agent:8000",
            {},
            {"old-task"},
        ) == expected
