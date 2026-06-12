"""RFC 9457 problem+json errors with machine-readable ERR_* codes (CONVENTIONS.md)."""

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        title: str,
        detail: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.headers = headers or {}


def problem_response(status: int, code: str, title: str, detail: str | None = None) -> JSONResponse:
    body: dict[str, object] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "code": code,
    }
    if detail is not None:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CONTENT_TYPE)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def on_problem(_request: Request, exc: ProblemError) -> Response:  # pyright: ignore[reportUnusedFunction]  — registered via decorator; not called directly
        resp = problem_response(exc.status, exc.code, exc.title, exc.detail)
        for key, val in exc.headers.items():
            resp.headers[key] = val
        return resp

    @app.exception_handler(RequestValidationError)
    async def on_validation(_request: Request, _exc: RequestValidationError) -> Response:  # pyright: ignore[reportUnusedFunction]  — registered via decorator; not called directly
        return problem_response(422, "ERR_PAYLOAD_INVALID", "Request payload failed validation")
