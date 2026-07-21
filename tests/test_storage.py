from app.schemas import (
    AlertGroupStatus,
    AlertSeverity,
    ChatMode,
    ChatResponse,
    DiagnosisTaskEventType,
    HumanReviewStatus,
    SourceDocument,
)
from app.storage import SQLiteIncidentStore, SQLiteTaskStore


def test_sqlite_incident_store_saves_incident_and_diagnosis(tmp_path) -> None:
    store = SQLiteIncidentStore(tmp_path / "incidents.db")
    incident = store.create_incident(
        title="payment 5xx",
        service="payment-api",
        question="payment service 5xx is increasing",
        session_id="storage-test",
    )
    response = ChatResponse(
        session_id="storage-test",
        answer="Check the latest deployment and database connection pool.",
        mode=ChatMode.ops,
        sources=[
            SourceDocument(
                doc_id="payment_5xx.md#chunk-0",
                title="Payment runbook",
                content="Check 5xx and deployment.",
                source="payment_5xx.md",
                score=0.9,
            )
        ],
        metadata={
            "service": "payment-api",
            "tool_results": [],
            "react_steps": [],
        },
    )

    diagnosis = store.save_diagnosis(incident_id=incident.incident_id, response=response)

    loaded_incident = store.get_incident(incident.incident_id)
    latest_diagnosis = store.get_latest_diagnosis(incident.incident_id)

    assert loaded_incident is not None
    assert loaded_incident.service == "payment-api"
    assert latest_diagnosis is not None
    assert latest_diagnosis.diagnosis_id == diagnosis.diagnosis_id
    assert latest_diagnosis.sources[0].title == "Payment runbook"
    assert store.list_incidents()[0].incident_id == incident.incident_id


def test_sqlite_task_store_records_task_events(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        source="alertmanager",
        question="payment service 5xx is high",
        session_id="task-storage-test",
        service="payment-api",
        severity=AlertSeverity.critical,
    )

    assert task.thread_id is not None
    assert task.thread_id.startswith("thread_")
    assert task.run_id is not None
    assert task.run_id.startswith("run_")
    store.mark_running(task.task_id)
    store.append_event(
        task_id=task.task_id,
        event_type=DiagnosisTaskEventType.tool_result,
        message="Tool query_metrics succeeded.",
        data={"tool_name": "query_metrics", "success": True},
    )
    response = ChatResponse(
        session_id="task-storage-test",
        answer="diagnosis",
        mode=ChatMode.ops,
        metadata={
            "incident_id": "inc_test",
            "diagnosis_id": "diag_test",
        },
    )
    store.mark_succeeded(task.task_id, response)

    loaded_task = store.require_task(task.task_id)
    events = store.list_events(task.task_id)

    assert loaded_task.status == "succeeded"
    assert loaded_task.incident_id == "inc_test"
    assert [event.event_type for event in events] == [
        DiagnosisTaskEventType.queued,
        DiagnosisTaskEventType.running,
        DiagnosisTaskEventType.tool_result,
        DiagnosisTaskEventType.succeeded,
    ]


def test_sqlite_task_store_records_graph_checkpoints(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        source="alertmanager",
        question="payment service 5xx is high",
        session_id="checkpoint-storage-test",
        service="payment-api",
        severity=AlertSeverity.critical,
    )

    store.save_graph_checkpoint(
        task_id=task.task_id,
        thread_id=task.thread_id,
        run_id=task.run_id,
        node_name="infer_service",
        status="started",
        state={"session_id": "checkpoint-storage-test"},
    )
    store.save_graph_checkpoint(
        task_id=task.task_id,
        thread_id=task.thread_id,
        run_id=task.run_id,
        node_name="infer_service",
        status="completed",
        state={"service": "payment-api"},
    )

    checkpoints = store.list_graph_checkpoints(task.task_id)

    assert [checkpoint.status for checkpoint in checkpoints] == ["started", "completed"]
    assert checkpoints[0].thread_id == task.thread_id
    assert checkpoints[0].run_id == task.run_id
    assert checkpoints[0].node_name == "infer_service"
    assert checkpoints[1].state["service"] == "payment-api"


def test_sqlite_task_store_records_human_review_decisions(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.db")
    task = store.create_task(
        source="alertmanager",
        question="payment service 5xx is high",
        session_id="review-storage-test",
        service="payment-api",
        severity=AlertSeverity.critical,
    )
    review = store.create_human_review_request(
        task_id=task.task_id,
        service="payment-api",
        proposed_actions=["Rollback payment-api to the previous stable version."],
        risk_reasons=["Rollback is a high-risk production operation."],
        metadata={"source": "test"},
    )

    approved = store.decide_human_review_request(
        review_id=review.review_id,
        status=HumanReviewStatus.approved,
        reviewer="alice",
        reason="Confirmed deployment regression.",
    )
    events = store.list_events(task.task_id)

    assert approved.status == HumanReviewStatus.approved
    assert approved.reviewer == "alice"
    assert approved.decided_at is not None
    assert events[-1].event_type == DiagnosisTaskEventType.human_review_approved


def test_sqlite_task_store_aggregates_alert_groups_by_dedupe_key(tmp_path) -> None:
    store = SQLiteTaskStore(tmp_path / "tasks.db")

    first_group = store.upsert_alert_group(
        dedupe_key="alertmanager:fingerprint:test",
        source="alertmanager",
        title="High5xxRate",
        service="payment-api",
        severity=AlertSeverity.critical,
        labels={"alertname": "High5xxRate", "service": "payment-api"},
    )
    second_group = store.upsert_alert_group(
        dedupe_key="alertmanager:fingerprint:test",
        source="alertmanager",
        title="High5xxRate",
        service="payment-api",
        severity=AlertSeverity.critical,
        labels={"alertname": "High5xxRate", "service": "payment-api"},
    )
    task = store.create_task(
        source="alertmanager",
        question="payment service 5xx is high",
        session_id="group-storage-test",
        alert_group_id=first_group.group_id,
        service="payment-api",
        severity=AlertSeverity.critical,
    )
    diagnosed_group = store.attach_task_to_alert_group(first_group.group_id, task.task_id)
    resolved_group = store.resolve_alert_group("alertmanager:fingerprint:test")

    assert first_group.group_id == second_group.group_id
    assert second_group.trigger_count == 2
    assert diagnosed_group.latest_task_id == task.task_id
    assert resolved_group is not None
    assert resolved_group.status == AlertGroupStatus.resolved
    assert resolved_group.trigger_count == 3
