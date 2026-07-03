from fastapi import APIRouter, HTTPException, Query

from app.agents import OpsAgent
from app.schemas import ChatRequest, ChatResponse, IncidentDetailResponse, IncidentRecord
from app.storage import SQLiteIncidentStore

router = APIRouter(prefix="/api/incidents", tags=["incidents"])


@router.post("/analyze", response_model=ChatResponse)
async def analyze_incident(request: ChatRequest) -> ChatResponse:
    return await OpsAgent.create_default(incident_store=_incident_store()).analyze(
        question=request.message,
        session_id=request.session_id,
    )


@router.get("", response_model=list[IncidentRecord])
async def list_incidents(limit: int = Query(default=20, ge=1, le=100)) -> list[IncidentRecord]:
    return _incident_store().list_incidents(limit=limit)


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
async def get_incident(incident_id: str) -> IncidentDetailResponse:
    incident_store = _incident_store()
    incident = incident_store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    return IncidentDetailResponse(
        incident=incident,
        latest_diagnosis=incident_store.get_latest_diagnosis(incident_id),
    )


def _incident_store() -> SQLiteIncidentStore:
    return SQLiteIncidentStore.from_settings()
