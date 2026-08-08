import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import answerer, catalog, repair, report, risk, runner
from .database import initialize, reset_fixture


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize()
    yield


app = FastAPI(
    title="Contextprobe",
    description="Find the metadata that will break an AI agent, ranked by blast radius.",
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")],
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)


@app.exception_handler(sqlite3.Error)
async def catalog_unavailable(_: Request, __: sqlite3.Error) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "The catalog store is unavailable; no probe results were produced."},
    )


class ProbeRequest(BaseModel):
    mode: str = "auto"


class DescriptionUpdate(BaseModel):
    column_name: str | None = None
    description: str | None = Field(default=None, max_length=2000)


class RepairRequest(BaseModel):
    column_name: str
    strategy: str = "grounded"
    mode: str = "auto"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "llm_configured": answerer.llm_available()}


@app.get("/api/queue")
def metadata_risk_queue() -> dict:
    return {"engine_available": answerer.llm_available(), "queue": risk.risk_queue()}


@app.get("/api/assets/{asset_id}")
def asset_detail(asset_id: str) -> dict:
    asset = catalog.get_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return {
        "asset": {**asset, "certified": bool(asset["certified"]), "deprecated": bool(asset["deprecated"])},
        **catalog.coverage(asset_id),
        "downstream_assets": catalog.downstream_assets(asset_id),
        "columns": catalog.get_columns(asset_id),
        "probes": [
            {"id": probe["id"], "column_name": probe["column_name"], "question": probe["question"]}
            for probe in catalog.get_probes(asset_id)
        ],
        "column_breakdown": risk.column_breakdown(asset_id),
        "results": [item for item in risk.latest_results() if item["asset_id"] == asset_id],
    }


@app.post("/api/assets/{asset_id}/probe")
def probe_asset(asset_id: str, request: ProbeRequest) -> dict:
    if catalog.get_asset(asset_id) is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    try:
        return runner.run_probes(asset_id, request.mode)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/probe")
def probe_catalog(request: ProbeRequest) -> dict:
    try:
        outcome = runner.run_probes(None, request.mode)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {**outcome, "queue": risk.risk_queue()}


@app.patch("/api/assets/{asset_id}/description")
def edit_description(asset_id: str, update: DescriptionUpdate) -> dict:
    try:
        catalog.update_description(asset_id, update.column_name, update.description)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"asset_id": asset_id, "column_name": update.column_name, "saved": True}


@app.post("/api/assets/{asset_id}/repair")
def propose_repair(asset_id: str, request: RepairRequest) -> dict:
    """Propose a rewrite and report whether it clears the gate. Never commits."""
    try:
        return repair.repair_column(
            asset_id, request.column_name, request.strategy, request.mode
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/repair")
def repair_catalog(request: ProbeRequest) -> dict:
    try:
        return repair.repair_all(request.mode)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.get("/api/report")
def coverage_report() -> dict:
    return {"coverage_vs_risk": report.coverage_vs_risk(), "paired_comparison": report.paired_comparison()}


@app.post("/api/reset")
def reset() -> dict:
    reset_fixture()
    return {"reset": True}
