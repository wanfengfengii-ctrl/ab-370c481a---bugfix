"""FastAPI entrypoint for the triple-mask assignment workbench."""

from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .solver import SolverError, solve_mask_assignment
from .validation import validate_payload

app = FastAPI(title="Triple-Mask Layout Assignment Workbench")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_request, _exc):
    return JSONResponse(
        status_code=400,
        content={
            "status": "invalid",
            "errors": [{"loc": "body", "message": "请求体必须是 JSON 对象"}],
        },
    )


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/solve")
def solve(payload: dict = Body(...)):
    errors, model = validate_payload(payload)
    if errors:
        return JSONResponse(
            status_code=400, content={"status": "invalid", "errors": errors}
        )
    try:
        return solve_mask_assignment(**model)
    except SolverError as exc:
        return JSONResponse(
            status_code=500, content={"status": "error", "message": str(exc)}
        )


# Serve the built frontend (produced by the Docker multi-stage build) when
# it is present; API routes above keep precedence over the mount.
_STATIC_DIR = Path(__file__).resolve().parents[1] / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
