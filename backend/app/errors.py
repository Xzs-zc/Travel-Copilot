from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def request_id_for(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or uuid.uuid4().hex)


def error_json_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool = False,
    headers: dict[str, str] | None = None,
    details: Any = None,
) -> JSONResponse:
    request_id = request_id_for(request)
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "retryable": retryable,
        },
        "detail": message,
        "request_id": request_id,
    }
    if details is not None:
        payload["error"]["details"] = jsonable_encoder(details)
    response_headers = {"X-Request-ID": request_id, **(headers or {})}
    return JSONResponse(payload, status_code=status_code, headers=response_headers)


def code_for_status(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        429: "rate_limited",
        503: "dependency_unavailable",
    }.get(status_code, "internal_error" if status_code >= 500 else "request_failed")


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "请求失败"
    headers = dict(exc.headers or {})
    headers.setdefault("X-Request-ID", request_id_for(request))
    return error_json_response(
        request,
        status_code=exc.status_code,
        code=code_for_status(exc.status_code),
        message=detail,
        retryable=exc.status_code in {429, 503},
        headers=headers,
        details=None if isinstance(exc.detail, str) else exc.detail,
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return error_json_response(
        request,
        status_code=422,
        code="validation_error",
        message="请求参数校验失败",
        details=exc.errors(),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = request_id_for(request)
    logger.exception("Unhandled request error request_id=%s", request_id, exc_info=exc)
    return error_json_response(
        request,
        status_code=500,
        code="internal_error",
        message="服务内部错误",
    )
