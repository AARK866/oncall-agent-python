from fastapi import APIRouter, HTTPException, Query

from app.agents import OpsAgent
from app.schemas import ChatRequest, ChatResponse, IncidentDetailResponse, IncidentRecord
from app.storage import SQLiteIncidentStore

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

incident_store = SQLiteIncidentStore.from_settings()
ops_agent = OpsAgent.create_default(incident_store=incident_store)


@router.post("/analyze", response_model=ChatResponse)
async def analyze_incident(request: ChatRequest) -> ChatResponse:
    return await ops_agent.analyze(
        question=request.message,
        session_id=request.session_id,
    )


@router.get("", response_model=list[IncidentRecord])
async def list_incidents(limit: int = Query(default=20, ge=1, le=100)) -> list[IncidentRecord]:
    return incident_store.list_incidents(limit=limit)


@router.get("/{incident_id}", response_model=IncidentDetailResponse)
async def get_incident(incident_id: str) -> IncidentDetailResponse:
    incident = incident_store.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")

    return IncidentDetailResponse(
        incident=incident,
        latest_diagnosis=incident_store.get_latest_diagnosis(incident_id),
    )
