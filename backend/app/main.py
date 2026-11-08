"""Run locally with uvicorn app.main:app --app-dir backend."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.boundary import LOCAL_ORIGINS, RequestBoundary
from app.api.errors import ApiError
from app.api.media import router as media_router
from app.api.routes import router
from app.contracts.models import ErrorDetail, ErrorField, ErrorResponse
from app.services.jobs import workflow_lifespan

app = FastAPI(
    title="CQA API",
    version="0.1.0",
    description="Импорт, версии, задания оценки и апелляции. Demo использует синтетику.",
    separate_input_output_schemas=True,
    lifespan=workflow_lifespan,
)
app.add_middleware(RequestBoundary)
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(LOCAL_ORIGINS),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Idempotency-Key"],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.error).model_dump(mode="json"),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Do not return error.input/ctx: they may contain sensitive transcripts or URLs.
    fields = []
    for error in exc.errors()[:20]:
        location = error["loc"]
        if error["type"] == "extra_forbidden":
            location = location[:-1]  # An unexpected property name can itself contain private data.
        fields.append(
            ErrorField(
                field=".".join(map(str, location)) or "body",
                message="Недопустимое или отсутствующее значение.",
            )
        )
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error=ErrorDetail(
                code="VALIDATION_ERROR", message="Проверьте поля запроса.", details=tuple(fields)
            )
        ).model_dump(mode="json"),
    )


app.include_router(router)
app.include_router(media_router)
