"""App-level handler that renders CheckoutError as the §3 ``{ "error": <token> }`` envelope."""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from gateway.payments.domain.errors import CheckoutError


def register_checkout_error_handler(app: FastAPI) -> None:
    @app.exception_handler(CheckoutError)
    async def on_checkout_error(  # pyright: ignore[reportUnusedFunction]
        _request: Request, exc: CheckoutError
    ) -> Response:
        return JSONResponse(status_code=exc.status, content={"error": exc.error})
