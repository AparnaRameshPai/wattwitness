"""FastAPI app: serves the website and streams agent progress to it with Server-Sent Events."""
import json
import re
import shutil
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, receipts, telemetry
from .attacks import ATTACKS, list_attacks, run_attack, run_red_team
from .orchestrator import run_analysis
from .sandbox import STATS, docker_available, memory_limit_mb


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await telemetry.load()  # Vultr model prices + VM facts for the sidebar
    yield


app = FastAPI(title="Receipts", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")

HEX12 = re.compile(r"^[a-f0-9]{12}$")

# Public-demo protection: one live run (analysis, attack or red team) per visitor, MAX_ACTIVE_RUNS in total.
ACTIVE_RUNS: dict[str, int] = {}


def _client(request: Request) -> str:
    # Caddy sets X-Forwarded-For to the real client IP (it ignores client-supplied values by default).
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


def _sse(request: Request, make_events):
    client = _client(request)
    if ACTIVE_RUNS.get(client):
        raise HTTPException(429, "You already have a run in progress. Wait for it to finish.")
    if sum(ACTIVE_RUNS.values()) >= config.MAX_ACTIVE_RUNS:
        raise HTTPException(429, "The demo is busy right now. Try again in a minute.")
    ACTIVE_RUNS[client] = 1
    events = make_events()

    async def stream():
        try:
            async for event in events:
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:  # also runs when the visitor closes the page: free the slot and stop the run
            ACTIVE_RUNS.pop(client, None)
            await events.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _dataset_path(dataset_id: str):
    if not HEX12.match(dataset_id):
        raise HTTPException(400, "Invalid dataset id")
    path = config.DATA_DIR / f"{dataset_id}.csv"
    if not path.exists():
        raise HTTPException(404, "Dataset not found")
    return path


@app.get("/")
def index():
    return FileResponse(config.FRONTEND_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"ok": True, "docker": docker_available(), "model_configured": bool(config.LLM_MODEL and config.LLM_API_KEY),
            "model": config.LLM_MODEL, "verifier_model": config.VERIFIER_MODEL, "vision_model": config.VISION_MODEL,
            "runtime": config.SANDBOX_RUNTIME or "runc", "sandboxes": STATS.to_dict(),
            "active_runs": sum(ACTIVE_RUNS.values()), "max_active_runs": config.MAX_ACTIVE_RUNS,
            "max_parallel_sandboxes": config.MAX_PARALLEL_SANDBOXES,
            "receipt_key_fingerprint": receipts.fingerprint()}


@app.get("/api/vultr")
def vultr():
    """Everything the website's Vultr sidebar shows: the VM, Serverless Inference usage and the sandbox fleet."""
    models = {"coder": config.LLM_MODEL, "verifier": config.VERIFIER_MODEL, "vision": config.VISION_MODEL or None,
              "red_team": config.REDTEAM_MODEL}
    used = {m for m in models.values() if m} | set(telemetry.TOTALS["by_model"])
    return {
        "vm": {"product": "Vultr Cloud Compute", **telemetry.VM},
        "inference": {
            "product": "Vultr Serverless Inference", "endpoint": config.LLM_BASE_URL, "models": models,
            "prices_per_million_tokens": {m: telemetry.price_per_million(m) for m in sorted(used)},
            "totals": telemetry.TOTALS,
        },
        "sandboxes": {
            "engine": "Docker", "runtime": config.SANDBOX_RUNTIME or "runc",
            "isolation": "gVisor user-space kernel" if config.SANDBOX_RUNTIME == "runsc" else "standard container",
            "created": STATS.created, "destroyed": STATS.destroyed, "running": STATS.created - STATS.destroyed,
            "max_parallel": config.MAX_PARALLEL_SANDBOXES,
            "limits": {"memory_mb": memory_limit_mb(), "cpus": config.SANDBOX_CPUS, "processes": config.SANDBOX_PIDS,
                       "timeout_s": config.SANDBOX_TIMEOUT, "network": "none", "filesystem": "read-only",
                       "user": "non-root", "secrets": 0},
        },
        "runs": {"active": sum(ACTIVE_RUNS.values()), "max": config.MAX_ACTIVE_RUNS},
        "web": {"server": "FastAPI on the VM, behind Caddy", "https": "Let's Encrypt (automatic, via Caddy)"},
    }


@app.post("/api/datasets")
async def upload_dataset(file: UploadFile):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(400, "Please upload a .csv file")
    data = await file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB")
    dataset_id = uuid.uuid4().hex[:12]
    (config.DATA_DIR / f"{dataset_id}.csv").write_bytes(data)
    return {"dataset_id": dataset_id, "filename": file.filename, "size_kb": round(len(data) / 1024, 1)}


@app.post("/api/datasets/demo")
def demo_dataset():
    src = config.DEMO_DIR / "brf_heat_pump_meter.csv"
    dataset_id = uuid.uuid4().hex[:12]
    shutil.copy(src, config.DATA_DIR / f"{dataset_id}.csv")
    return {"dataset_id": dataset_id, "filename": src.name, "size_kb": round(src.stat().st_size / 1024, 1)}


@app.get("/api/runs")
async def run(request: Request, dataset_id: str, question: str):
    if not question.strip():
        raise HTTPException(400, "Question is empty")
    path = _dataset_path(dataset_id)
    return _sse(request, lambda: run_analysis(path, question.strip()[:1000]))


@app.get("/api/receipts/public-key", response_class=PlainTextResponse)
def public_key():
    return receipts.public_key_pem()


@app.get("/api/receipts/{run_id}")
def receipt(run_id: str):
    if not HEX12.match(run_id):
        raise HTTPException(400, "Invalid run id")
    path = receipts.RECEIPT_DIR / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(404, "Receipt not found")
    return FileResponse(path, media_type="application/json", filename=f"receipt-{run_id}.json")


@app.get("/api/attacks")
def attacks():
    return list_attacks()


@app.get("/api/attacks/{attack_id}/run")
async def attack(request: Request, attack_id: str):
    if attack_id not in ATTACKS:
        raise HTTPException(404, "Unknown attack")
    return _sse(request, lambda: run_attack(attack_id))


@app.get("/api/redteam/run")
async def red_team(request: Request):
    return _sse(request, run_red_team)
