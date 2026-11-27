"""How Vultr is used, measured live: every Vultr Serverless Inference call (model, role, tokens, time, cost) and the
facts of the Vultr VM this runs on. Feeds the website's "Vultr" sidebar.

- Prices come from Vultr's public model catalog (per-token USD), loaded once at startup.
- VM facts come from the Vultr metadata service (reachable from the VM only; sandboxes are blocked from it).
- The running analysis/attack registers its event emitter in a context variable, so llm.chat can stream each call
  to the right browser without every caller passing it along.
"""
import contextvars
import os
import platform
from pathlib import Path

import httpx

from . import config

EMIT: contextvars.ContextVar = contextvars.ContextVar("receipts_emit", default=None)

PRICES: dict[str, tuple[float, float]] = {}  # model -> (USD per prompt token, USD per completion token)
VM: dict = {}
TOTALS: dict = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0, "seconds": 0.0, "by_model": {}}

REGION_NAMES = {"STO": "Stockholm", "AMS": "Amsterdam", "FRA": "Frankfurt", "LHR": "London", "CDG": "Paris",
                "WAW": "Warsaw", "MAD": "Madrid", "EWR": "New Jersey", "ORD": "Chicago", "LAX": "Los Angeles",
                "SJC": "Silicon Valley", "SEA": "Seattle", "DFW": "Dallas", "ATL": "Atlanta", "MIA": "Miami",
                "NRT": "Tokyo", "ICN": "Seoul", "SGP": "Singapore", "SYD": "Sydney", "BOM": "Mumbai"}


def _token_price(modalities, kind: str) -> float | None:
    for mod in modalities or []:
        if mod.get("type") != "text":
            continue
        for price in mod.get("pricing") or []:
            if price.get("type") == kind and price.get("unit") == "token":
                return float(price["cost_usd"])
    return None


def _host_facts() -> dict:
    facts = {"cpus": os.cpu_count(), "python": platform.python_version()}
    try:
        kb = int(next(l for l in Path("/proc/meminfo").read_text().splitlines() if l.startswith("MemTotal")).split()[1])
        facts["memory_gb"] = round(kb / 1024**2, 1)
    except (OSError, StopIteration, ValueError):
        pass
    try:
        rel = dict(l.split("=", 1) for l in Path("/etc/os-release").read_text().splitlines() if "=" in l)
        facts["os"] = rel.get("PRETTY_NAME", "").strip('"')
    except OSError:
        pass
    return facts


async def load() -> None:
    """Called once at startup. Failures are fine: the sidebar just shows less."""
    VM.update(_host_facts(), plan=config.VULTR_PLAN or None)
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            catalog = (await client.get(f"{config.LLM_BASE_URL}/models")).json()
            for model in catalog.get("data", []):
                prompt = _token_price(model.get("input_modalities"), "prompt")
                completion = _token_price(model.get("output_modalities"), "completion")
                if prompt is not None or completion is not None:
                    PRICES[model["id"]] = (prompt or 0.0, completion or 0.0)
        except (httpx.HTTPError, ValueError, KeyError):
            pass
        try:
            meta = (await client.get("http://169.254.169.254/v1.json", timeout=2)).json()
            code = (meta.get("region") or {}).get("regioncode")
            VM.update(hostname=meta.get("hostname"), region_code=code,
                      region=REGION_NAMES.get(code or "", code), country=(meta.get("region") or {}).get("countrycode"))
        except (httpx.HTTPError, ValueError):
            pass  # not on a Vultr VM (local run)


def record(model: str, role: str, prompt_tokens: int, completion_tokens: int, seconds: float, ok: bool) -> float:
    """Add one inference call to the server-wide totals and return its cost in USD."""
    p, c = PRICES.get(model, (0.0, 0.0))
    usd = prompt_tokens * p + completion_tokens * c
    for bucket in (TOTALS, TOTALS["by_model"].setdefault(model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                                                    "cost_usd": 0.0, "seconds": 0.0, "roles": []})):
        bucket["calls"] += 1
        bucket["prompt_tokens"] += prompt_tokens
        bucket["completion_tokens"] += completion_tokens
        bucket["cost_usd"] += usd
        bucket["seconds"] += seconds
    roles = TOTALS["by_model"][model]["roles"]
    if role not in roles:
        roles.append(role)
    return usd


async def emit(event: dict) -> None:
    """Send an event to the browser of the run in progress (no-op outside a run)."""
    fn = EMIT.get()
    if fn is not None:
        await fn(event)


def price_per_million(model: str) -> dict | None:
    if model not in PRICES:
        return None
    p, c = PRICES[model]
    return {"input": round(p * 1e6, 4), "output": round(c * 1e6, 4)}
