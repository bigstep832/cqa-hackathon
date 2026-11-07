"""CQA HTTP API using the shared domain contracts."""

from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query

from app.contracts.models import (
    Appeal,
    AppealCreateRequest,
    AppealList,
    Channel,
    ChecklistCreateRequest,
    ChecklistList,
    ChecklistVersion,
    ChecklistVersionCreateRequest,
    ConversationImportRequest,
    ConversationList,
    ConversationVersion,
    DatasetImportRequest,
    DecisionCreateRequest,
    DecisionResponse,
    DemoPayload,
    ErrorResponse,
    Evaluation,
    EvaluationCreateRequest,
    HealthResponse,
    ImportResponse,
    Language,
)
from app.evaluation.demo import build_demo_payload
from app.services import appeals, imports, jobs
from app.storage import conversations, workflow

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Entity not found"},
    409: {"model": ErrorResponse, "description": "Version or idempotency conflict"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    403: {"model": ErrorResponse, "description": "Browser origin or operation denied"},
    413: {"model": ErrorResponse, "description": "Request body too large"},
    502: {"model": ErrorResponse, "description": "Upstream source or provider failed"},
    503: {"model": ErrorResponse, "description": "Source or workflow unavailable"},
}
router = APIRouter(responses=ERRORS)
Identifier = Annotated[str, Path(min_length=1, max_length=200)]


@router.get("/health", response_model=HealthResponse, tags=["bootstrap"])
def health() -> HealthResponse:
    return HealthResponse()


@router.get("/api/v1/demo", response_model=DemoPayload, tags=["bootstrap"])
async def demo() -> DemoPayload:
    """Explicit synthetic fixture; never a fallback for real evaluations."""
    return await build_demo_payload()


@router.post("/api/v1/datasets/import", response_model=ImportResponse, tags=["datasets"])
def import_dataset(request: DatasetImportRequest) -> ImportResponse:
    return imports.import_dataset(request)


@router.post("/api/v1/conversations/import", response_model=ImportResponse, tags=["datasets"])
def import_conversations(request: ConversationImportRequest) -> ImportResponse:
    return conversations.import_conversations(request)


@router.get("/api/v1/conversations", response_model=ConversationList, tags=["conversations"])
@router.get("/api/v1/conversations/", include_in_schema=False)
def list_conversations(
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    language: Language | None = None,
    channel: Channel | None = None,
) -> ConversationList:
    return conversations.list_conversations(offset, limit, language, channel)


@router.get(
    "/api/v1/conversations/{conversation_id:path}",
    response_model=ConversationVersion,
    tags=["conversations"],
)
def get_conversation(conversation_id: Annotated[str, Path(min_length=1)]) -> ConversationVersion:
    return conversations.get_conversation(conversation_id)


@router.get(
    "/api/v1/conversation-versions/{version_id}",
    response_model=ConversationVersion,
    tags=["conversations"],
)
def get_conversation_version(version_id: Identifier) -> ConversationVersion:
    return conversations.get_version(version_id)


@router.post(
    "/api/v1/checklists", response_model=ChecklistVersion, status_code=201, tags=["checklists"]
)
def create_checklist(request: ChecklistCreateRequest) -> ChecklistVersion:
    return workflow.save_checklist(request)


@router.get("/api/v1/checklists", response_model=ChecklistList, tags=["checklists"])
def list_checklists() -> ChecklistList:
    return workflow.list_checklists()


@router.post(
    "/api/v1/checklists/{checklist_id}/versions",
    response_model=ChecklistVersion,
    status_code=201,
    tags=["checklists"],
)
def create_checklist_version(
    checklist_id: Identifier, request: ChecklistVersionCreateRequest
) -> ChecklistVersion:
    return workflow.save_checklist(request, checklist_id)


@router.get(
    "/api/v1/checklist-versions/{version_id}",
    response_model=ChecklistVersion,
    tags=["checklists"],
)
def get_checklist_version(version_id: Identifier) -> ChecklistVersion:
    return workflow.get_checklist_version(version_id)


@router.post(
    "/api/v1/evaluations", response_model=Evaluation, status_code=202, tags=["evaluations"]
)
def create_evaluation(
    request: EvaluationCreateRequest,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=200)] = None,
) -> Evaluation:
    return jobs.create_evaluation(request, idempotency_key)


@router.get(
    "/api/v1/evaluations/{evaluation_id}", response_model=Evaluation, tags=["evaluations"]
)
def get_evaluation(evaluation_id: Identifier) -> Evaluation:
    return workflow.get_evaluation(evaluation_id)


@router.post(
    "/api/v1/evaluations/{evaluation_id}/appeals",
    response_model=Appeal,
    status_code=201,
    tags=["appeals"],
)
def create_appeal(evaluation_id: Identifier, request: AppealCreateRequest) -> Appeal:
    return appeals.create_appeal(evaluation_id, request)


@router.get(
    "/api/v1/evaluations/{evaluation_id}/appeals", response_model=AppealList, tags=["appeals"]
)
def list_appeals(evaluation_id: Identifier) -> AppealList:
    return appeals.list_appeals(evaluation_id)


@router.post(
    "/api/v1/appeals/{appeal_id}/decision", response_model=DecisionResponse, tags=["appeals"]
)
def decide_appeal(appeal_id: Identifier, request: DecisionCreateRequest) -> DecisionResponse:
    return appeals.decide_appeal(appeal_id, request)
