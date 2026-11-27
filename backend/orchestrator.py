"""The orchestrator. Zero-trust pipeline for every question:

  1 profile   read the CSV inside a sandbox (the host never parses untrusted data with code)
  2 plan      planner splits the question into independent tasks
  3 execute   each task -> policy gate -> its own sandbox, in parallel, with self-healing
  4 assure    in parallel: independent verification of every key number (tie-breaker on mismatch),
              replay of every task in a fresh sandbox (reproducibility), vision critique of every chart
  5 write     answer from verified evidence only, with assumptions and caveats
  6 seal      the whole run is signed into a tamper-evident Ed25519 receipt

`run_analysis` is an async generator of event dicts; the API streams them to the browser live.
"""
import asyncio
import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config, llm, policy, prompts, receipts, telemetry
from .sandbox import STATS, memory_limit_mb, run_code_async

PROFILE_SCRIPT = r'''
import json, pandas as pd
df = pd.read_csv("data.csv")
def cut(v):
    s = str(v)
    return s if len(s) <= 200 else s[:200] + "..."
profile = {
    "rows": int(len(df)),
    "columns": [{"name": str(c), "dtype": str(df[c].dtype), "nulls": int(df[c].isna().sum()),
                 "unique": int(df[c].nunique())} for c in df.columns],
    "sample_rows": [{str(k): cut(v) for k, v in r.items()} for r in df.head(5).to_dict("records")],
    "numeric_summary": json.loads(df.describe().round(4).to_json()) if len(df.select_dtypes("number").columns) else {},
}
print(json.dumps(profile))
'''

RESULT_PREFIX = "RESULT_JSON:"


def parse_result(stdout: str, prefix: str = RESULT_PREFIX) -> dict | None:
    for line in reversed(stdout.strip().splitlines()):
        if line.startswith(prefix):
            try:
                return json.loads(line[len(prefix):].strip())
            except json.JSONDecodeError:
                return None
    return None


def numbers_agree(a, b) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=1e-3, abs_tol=1e-6)
    except (TypeError, ValueError):
        return False


def containment_snapshot(counts: dict | None = None) -> dict:
    run = counts or {}
    return {"sandboxes": {"created": run.get("created", 0), "destroyed": run.get("destroyed", 0)},
            "server_sandboxes": STATS.to_dict(), "policy_blocks": run.get("policy_blocks", 0),
            "network": "none", "secrets_in_sandbox": 0, "memory": config.SANDBOX_MEMORY,
            "memory_mb": memory_limit_mb(), "timeout_s": config.SANDBOX_TIMEOUT,
            "runtime": config.SANDBOX_RUNTIME or "runc"}


class Run:
    """Shared state of one analysis run: the event queue and per-run containment counters."""

    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.queue: asyncio.Queue = asyncio.Queue()
        self.counts = {"created": 0, "destroyed": 0, "policy_blocks": 0}

    async def emit(self, event: dict):
        kind = event.get("type")
        if kind == "sandbox_start":
            self.counts["created"] += 1
        elif kind == "sandbox_end":
            self.counts["destroyed"] += 1
        elif kind == "policy_block":
            self.counts["policy_blocks"] += 1
        await self.queue.put(event)

    def lane(self, label: str, task_id: int):
        """Telemetry callback for one sandbox, tagged with what it is doing."""
        async def forward(ev: dict):
            await self.emit({**ev, "label": label, "task_id": task_id})
        return forward


async def execute_with_healing(run: Run, task: dict, profile_text: str, csv_path: Path, *, label="task",
                               model=None, system=prompts.CODER_SYSTEM, prefix=RESULT_PREFIX,
                               role="coder") -> dict:
    """Write code -> policy gate -> sandbox. On a block or failure, feed the problem back for a fix."""
    user = f"Dataset profile:\n{profile_text}\n\nTask: {task['title']}\n{task['instructions']}\n" \
           f"Chart requested: {bool(task.get('chart'))}"
    code = llm.extract_code(await llm.chat(system, user, model, role=role))
    attempts, attempt = [], {}
    for n in range(1, config.MAX_ATTEMPTS + 1):
        gate = policy.check(code)
        if gate["verdict"] == "blocked":
            await run.emit({"type": "policy_block", "label": label, "task_id": task["id"], "n": n,
                            "violations": gate["violations"]})
            problem = "Blocked by the security policy BEFORE execution: " + "; ".join(gate["violations"])
            attempt = {"n": n, "code": code, "ok": False, "status": "policy_blocked", "sandbox_id": None,
                       "stdout": "", "stderr": problem, "duration_s": 0, "parsed": None, "policy": gate}
        else:
            result = await run_code_async(code, csv_path, run.lane(label, task["id"]))
            parsed = parse_result(result.stdout, prefix) if result.status == "ok" else None
            attempt = {"n": n, "code": code, "ok": parsed is not None, "parsed": parsed, "policy": gate,
                       **result.to_dict()}
            problem = result.stderr or f"The script ran but did not print the {prefix} line."
        attempts.append({k: attempt.get(k) for k in ("n", "status", "sandbox_id", "duration_s", "ok", "peak_mem_mb")})
        await run.emit({"type": "attempt", "label": label, "task_id": task["id"],
                        **{k: v for k, v in attempt.items() if k not in ("chart_b64", "samples")}})
        if attempt["ok"] or n == config.MAX_ATTEMPTS:
            break
        fix = f"{user}\n\nPrevious script:\n```python\n{code}\n```\n\nIt failed with:\n{problem[-3000:]}"
        # Fix with the SAME rules that wrote the script (a verifier fix must keep the verifier's output format).
        code = llm.extract_code(await llm.chat(system + prompts.FIX_NOTE, fix, model, role=f"{role} fix"))
        await run.emit({"type": "healing", "label": label, "task_id": task["id"], "attempt": n + 1})
    attempt["history"] = attempts
    return attempt


async def _run_task(run: Run, task: dict, profile_text: str, csv_path: Path) -> dict:
    await run.emit({"type": "task_start", "task_id": task["id"], "title": task["title"]})
    final = await execute_with_healing(run, task, profile_text, csv_path)
    parsed = final.get("parsed") or {}
    done = {"task_id": task["id"], "title": task["title"], "ok": final.get("ok", False),
            "attempts": final.get("history", []), "code": final.get("code", ""), "stdout": final.get("stdout", ""),
            "stdout_sha256": final.get("stdout_sha256", ""), "sandbox_id": final.get("sandbox_id"),
            "duration_s": final.get("duration_s"), "peak_mem_mb": final.get("peak_mem_mb", 0),
            "chart_b64": final.get("chart_b64"), "metrics": parsed.get("metrics", []),
            "assumptions": parsed.get("assumptions", [])}
    await run.emit({"type": "task_done", **done})
    return done


async def _independent_value(run: Run, metric: dict, profile_text: str, csv_path: Path, idx: int, tag: str):
    task = {"id": idx, "title": f"{tag}: {metric['name']}",
            "instructions": f"Metric: {metric['name']}\nDefinition: {metric['description']}", "chart": False}
    final = await execute_with_healing(run, task, profile_text, csv_path, label=tag,
                                       model=config.VERIFIER_MODEL, system=prompts.VERIFIER_SYSTEM,
                                       role="verifier" if tag == "verify" else "tie-breaker")
    return (final.get("parsed") or {}).get("value"), final


async def _verify_metric(run: Run, metric: dict, profile_text: str, csv_path: Path, idx: int) -> dict:
    """Recompute independently; on disagreement run a tie-breaker and take the majority."""
    claimed = metric.get("value")
    check, first = await _independent_value(run, metric, profile_text, csv_path, idx, "verify")
    record = {"name": metric["name"], "description": metric.get("description", ""), "task_id": metric["task_id"],
              "claimed": claimed, "recomputed": check, "verifier_sandbox": first.get("sandbox_id"),
              "verifier_code": first.get("code", "")}
    if check is not None and numbers_agree(claimed, check):
        record.update(status="verified", value=claimed)
    else:
        await run.emit({"type": "verify_mismatch", "name": metric["name"], "claimed": claimed, "recomputed": check})
        tie, second = await _independent_value(run, metric, profile_text, csv_path, idx, "tie-breaker")
        record.update(tiebreak=tie, tiebreak_sandbox=second.get("sandbox_id"))
        if tie is not None and numbers_agree(claimed, tie):
            record.update(status="verified", value=claimed)
        elif tie is not None and check is not None and numbers_agree(check, tie):
            record.update(status="corrected", value=check)
        else:
            record.update(status="unverified", value=claimed)
    await run.emit({"type": "verify_result", **record})
    return record


async def _replay(run: Run, task: dict, csv_path: Path) -> dict:
    """Run the exact same code again in a brand-new sandbox: identical output hash = reproducible."""
    result = await run_code_async(task["code"], csv_path, run.lane("replay", task["task_id"]))
    record = {"task_id": task["task_id"], "original_sha256": task["stdout_sha256"],
              "replay_sha256": result.stdout_sha256, "sandbox_id": result.sandbox_id,
              "reproducible": result.status == "ok" and result.stdout_sha256 == task["stdout_sha256"]}
    await run.emit({"type": "replay_result", **record})
    return record


async def _review_chart(run: Run, task: dict) -> dict:
    review = await llm.review_image(
        prompts.CHART_CRITIC_SYSTEM,
        f"Task: {task['title']}\nFindings printed by the analysis:\n{task['stdout'][-1500:]}",
        task["chart_b64"], config.VISION_MODEL)
    record = {"task_id": task["task_id"], "verdict": review.get("verdict", "issues"),
              "shows": review.get("shows", ""), "issues": review.get("issues", [])}
    await run.emit({"type": "chart_review", **record})
    return record


def _build_receipt(run: Run, question: str, csv_path: Path, profile: dict, results: list, verified: list,
                   replays: list, reviews: list, report: dict) -> dict:
    by_task = {r["task_id"]: r for r in replays}
    review_by_task = {r["task_id"]: r for r in reviews}
    return {
        "run_id": run.id,
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "question": question,
        "dataset": {"sha256": receipts.sha256(csv_path.read_bytes()), "rows": profile["rows"],
                    "columns": [c["name"] for c in profile["columns"]]},
        "models": {"coder": config.LLM_MODEL, "verifier": config.VERIFIER_MODEL, "vision": config.VISION_MODEL or None},
        "containment": containment_snapshot(run.counts),
        "tasks": [{
            "task_id": r["task_id"], "title": r["title"], "ok": r["ok"], "attempts": r["attempts"],
            "code": r["code"], "code_sha256": receipts.sha256(r["code"]), "stdout_sha256": r["stdout_sha256"],
            "chart_sha256": receipts.sha256(r["chart_b64"]) if r["chart_b64"] else None,
            "replay": by_task.get(r["task_id"]), "chart_review": review_by_task.get(r["task_id"]),
        } for r in results],
        "verification": [{k: v.get(k) for k in ("name", "description", "status", "claimed", "recomputed",
                                                "tiebreak", "verifier_sandbox", "tiebreak_sandbox")}
                         for v in verified],
        "answer": report,
    }


async def run_analysis(csv_path: Path, question: str):
    run = Run()

    async def pipeline():
        await run.emit({"type": "run", "run_id": run.id})

        # 1. Profile, inside a sandbox.
        await run.emit({"type": "stage", "stage": "profile", "message": "Profiling the dataset inside a sandbox"})
        prof = await run_code_async(PROFILE_SCRIPT, csv_path, run.lane("profile", 0))
        if prof.status != "ok":
            await run.emit({"type": "error", "message": "Could not read the CSV", "detail": prof.stderr[-1500:]})
            return
        profile = json.loads(prof.stdout)
        profile_text = json.dumps(profile, indent=1)[:12000]
        await run.emit({"type": "profile", "rows": profile["rows"], "columns": profile["columns"],
                        "sandbox_id": prof.sandbox_id})

        # 2. Plan.
        await run.emit({"type": "stage", "stage": "plan", "message": "Planning the analysis"})
        plan = await llm.chat_json(prompts.PLANNER_SYSTEM.format(max_tasks=config.MAX_TASKS),
                                   f"Dataset profile:\n{profile_text}\n\nQuestion: {question}", role="planner")
        tasks = (plan.get("tasks") if isinstance(plan, dict) else plan)[: config.MAX_TASKS]
        for i, t in enumerate(tasks, start=1):
            t["id"] = i
        await run.emit({"type": "plan", "tasks": tasks})

        # 3. Execute: every task in its own sandbox, in parallel, behind the policy gate, self-healing.
        await run.emit({"type": "stage", "stage": "execute",
                        "message": f"Dispatching {len(tasks)} tasks to parallel sandboxes"})
        results = await asyncio.gather(*(_run_task(run, t, profile_text, csv_path) for t in tasks))

        # 4. Assure: verify numbers, replay tasks and critique charts, all at once.
        metrics, seen = [], set()
        for r in results:
            for m in r["metrics"] if r["ok"] else []:
                if isinstance(m, dict) and "name" in m and "value" in m and m["name"] not in seen:
                    seen.add(m["name"])
                    metrics.append({**m, "task_id": r["task_id"]})
        metrics = metrics[:5]
        ok_tasks = [r for r in results if r["ok"]]
        charts = [r for r in ok_tasks if r["chart_b64"]] if config.VISION_MODEL else []
        n_replays = len(ok_tasks) if config.REPLAY else 0
        await run.emit({"type": "stage", "stage": "assure",
                        "message": f"Verifying {len(metrics)} numbers, replaying {n_replays} "
                                   f"tasks, reviewing {len(charts)} charts",
                        "numbers": len(metrics), "replays": n_replays, "charts": len(charts)})
        verified, replays, reviews = await asyncio.gather(
            asyncio.gather(*(_verify_metric(run, m, profile_text, csv_path, 100 + i) for i, m in enumerate(metrics))),
            asyncio.gather(*(_replay(run, r, csv_path) for r in ok_tasks)) if config.REPLAY else _empty(),
            asyncio.gather(*(_review_chart(run, r) for r in charts), return_exceptions=True),
        )
        reviews = [r for r in reviews if isinstance(r, dict)]

        # 5. Write the answer from verified evidence only.
        await run.emit({"type": "stage", "stage": "write", "message": "Writing the answer from verified evidence"})
        evidence = {
            "question": question,
            "tasks": [{"task_id": r["task_id"], "title": r["title"], "ok": r["ok"], "output": r["stdout"][-2500:],
                       "assumptions": r["assumptions"]} for r in results],
            "metrics": [{k: v[k] for k in ("name", "description", "task_id", "status", "value", "claimed")}
                        for v in verified],
            "chart_reviews": reviews,
            "replays": [{"task_id": r["task_id"], "reproducible": r["reproducible"]} for r in replays],
        }
        report = await llm.chat_json(prompts.WRITER_SYSTEM, json.dumps(evidence, indent=1), role="writer")
        await run.emit({"type": "report", "report": report, "metrics": verified,
                        "containment": containment_snapshot(run.counts)})

        # 6. Seal everything into a signed receipt.
        await run.emit({"type": "stage", "stage": "seal", "message": "Sealing a signed receipt"})
        sealed = receipts.seal(_build_receipt(run, question, csv_path, profile, results, verified, replays,
                                              reviews, report))
        await run.emit({"type": "sealed", "run_id": run.id, "signature": sealed["signature"][:32] + "…",
                        "key_fingerprint": sealed["key_fingerprint"], "download": f"/api/receipts/{run.id}"})

    async def runner():
        telemetry.EMIT.set(run.emit)  # model calls in this run (and its parallel tasks) stream to this browser
        try:
            await pipeline()
        except Exception as exc:  # surface any failure to the UI instead of a silent hang
            await run.emit({"type": "error", "message": type(exc).__name__, "detail": str(exc)[:1500]})
        finally:
            await asyncio.sleep(0.3)  # let telemetry from the last sandboxes arrive
            await run.emit({"type": "done", "containment": containment_snapshot(run.counts)})

    worker = asyncio.create_task(runner())
    try:
        while True:
            event = await run.queue.get()
            yield event
            if event["type"] == "done":
                break
        await worker
    finally:  # the visitor left mid-run: stop planning and LLM calls (running sandboxes still clean up)
        if not worker.done():
            worker.cancel()


async def _empty():
    return []
