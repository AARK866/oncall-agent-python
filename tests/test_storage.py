from app.schemas import (
    AlertSeverity,
    ChatMode,
    ChatResponse,
    DiagnosisTaskEventType,
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
