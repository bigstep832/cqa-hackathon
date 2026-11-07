"""Run locally with uvicorn app.main:app --app-dir backend."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.errors import ApiError
from app.api.routes import router
from app.contracts.models import ErrorDetail, ErrorField, ErrorResponse

app = FastAPI(
    title="CQA API",
    version="0.1.0",
    description=(
        "Этап 0: health и синтетическое demo реализованы. Остальные маршруты описывают "
        "согласованный контракт и возвращают HTTP 501 NOT_IMPLEMENTED."
    ),
    separate_input_output_schemas=True,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(error=exc.error).model_dump(mode="json"),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Do not return error.input/ctx: they may contain sensitive transcripts or URLs.
    fields = tuple(
        ErrorField(field=".".join(str(part) for part in error["loc"]), message=error["msg"])
        for error in exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error=ErrorDetail(
                code="VALIDATION_ERROR", message="Проверьте поля запроса.", details=fields
            )
        ).model_dump(mode="json"),
    )


app.include_router(router)
