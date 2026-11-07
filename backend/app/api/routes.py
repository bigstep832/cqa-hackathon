"""Full agreed API schema. Workflow routes explicitly remain participant C's tasks."""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query

from app.api.errors import not_implemented
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

ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Entity not found"},
    409: {"model": ErrorResponse, "description": "Version or idempotency conflict"},
    422: {"model": ErrorResponse, "description": "Request validation failed"},
    501: {"model": ErrorResponse, "description": "Contract only: workflow not implemented"},
    502: {"model": ErrorResponse, "description": "Upstream source or provider failed"},
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
    not_implemented("C-01")


@router.post("/api/v1/conversations/import", response_model=ImportResponse, tags=["datasets"])
def import_conversations(request: ConversationImportRequest) -> ImportResponse:
    not_implemented("C-01")


@router.get("/api/v1/conversations", response_model=ConversationList, tags=["conversations"])
def list_conversations(
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    language: Language | None = None,
    channel: Channel | None = None,
) -> ConversationList:
    not_implemented("C-01")


@router.get(
    "/api/v1/conversations/{conversation_id}",
    response_model=ConversationVersion,
    tags=["conversations"],
)
def get_conversation(conversation_id: Identifier) -> ConversationVersion:
    not_implemented("C-01")


@router.get(
    "/api/v1/conversation-versions/{version_id}",
    response_model=ConversationVersion,
    tags=["conversations"],
)
def get_conversation_version(version_id: Identifier) -> ConversationVersion:
    not_implemented("C-01")


@router.post(
    "/api/v1/checklists", response_model=ChecklistVersion, status_code=201, tags=["checklists"]
)
def create_checklist(request: ChecklistCreateRequest) -> ChecklistVersion:
    not_implemented("C-02")


@router.get("/api/v1/checklists", response_model=ChecklistList, tags=["checklists"])
def list_checklists() -> ChecklistList:
    not_implemented("C-02")


@router.post(
    "/api/v1/checklists/{checklist_id}/versions",
    response_model=ChecklistVersion,
    status_code=201,
    tags=["checklists"],
)
def create_checklist_version(
    checklist_id: Identifier, request: ChecklistVersionCreateRequest
) -> ChecklistVersion:
    not_implemented("C-02")


@router.get(
    "/api/v1/checklist-versions/{version_id}",
    response_model=ChecklistVersion,
    tags=["checklists"],
)
def get_checklist_version(version_id: Identifier) -> ChecklistVersion:
    not_implemented("C-02")


@router.post(
    "/api/v1/evaluations", response_model=Evaluation, status_code=202, tags=["evaluations"]
)
def create_evaluation(request: EvaluationCreateRequest) -> Evaluation:
    not_implemented("C-03")


@router.get(
    "/api/v1/evaluations/{evaluation_id}", response_model=Evaluation, tags=["evaluations"]
)
def get_evaluation(evaluation_id: Identifier) -> Evaluation:
    not_implemented("C-03")


@router.post(
    "/api/v1/evaluations/{evaluation_id}/appeals",
    response_model=Appeal,
    status_code=201,
    tags=["appeals"],
)
def create_appeal(evaluation_id: Identifier, request: AppealCreateRequest) -> Appeal:
    not_implemented("C-04")


@router.get(
    "/api/v1/evaluations/{evaluation_id}/appeals", response_model=AppealList, tags=["appeals"]
)
def list_appeals(evaluation_id: Identifier) -> AppealList:
    not_implemented("C-04")


@router.post(
    "/api/v1/appeals/{appeal_id}/decision", response_model=DecisionResponse, tags=["appeals"]
)
def decide_appeal(appeal_id: Identifier, request: DecisionCreateRequest) -> DecisionResponse:
    not_implemented("C-04")
