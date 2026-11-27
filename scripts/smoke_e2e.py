"""End-to-end demo check against a running Receipts server (local or the public URL). Standard library only.

    python -m scripts.smoke_e2e https://<demo-url>              # analyst run + receipt + every scripted attack
    python -m scripts.smoke_e2e https://<demo-url> --redteam    # ... plus the autonomous red team
    python -m scripts.smoke_e2e https://<demo-url> --analyst    # only the analyst run

It drives the same API the website uses, prints what happened at each stage, saves the signed receipt
(verify it with: python -m scripts.verify_receipt <file> public-key.pem) and exits non-zero on any failure.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

QUESTION = "The heat pump was installed 1-14 March 2025 and the contractor guaranteed 40% lower electricity use. Was the guarantee met after adjusting for weather, and how much was saved in kWh and SEK?"
FAILS: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {msg}")
    if not ok:
        FAILS.append(msg)


def request(base: str, path: str, method: str = "GET", timeout: int = 30) -> bytes:
    req = urllib.request.Request(base + path, method=method, headers={"User-Agent": "receipts-smoke"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def sse(base: str, path: str, timeout: int = 900):
    """Yield the JSON events of a Server-Sent Events stream."""
    req = urllib.request.Request(base + path, headers={"Accept": "text/event-stream", "User-Agent": "receipts-smoke"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("data: "):
                yield json.loads(line[6:])


def analyst(base: str) -> None:
    print("\n=== Analyst: demo dataset + question")
    ds = json.loads(request(base, "/api/datasets/demo", "POST"))
    print(f"  dataset {ds['dataset_id']} ({ds['filename']}, {ds['size_kb']} KB)")
    t0, stage_t, counts = time.monotonic(), {}, {"policy_block": 0, "healing": 0, "verify_mismatch": 0}
    tasks, verified, replays, reviews, report, sealed, errors, peak_sbx = {}, [], [], [], None, None, [], 0
    running = set()
    query = urllib.parse.urlencode({"dataset_id": ds["dataset_id"], "question": QUESTION})
    for ev in sse(base, f"/api/runs?{query}"):
        kind = ev["type"]
        if kind == "stage":
            stage_t[ev["stage"]] = round(time.monotonic() - t0, 1)
            print(f"  [{stage_t[ev['stage']]:>5}s] {ev['stage']:<8} {ev['message']}")
        elif kind == "sandbox_start":
            running.add(ev["sandbox_id"])
            peak_sbx = max(peak_sbx, len(running))
        elif kind == "sandbox_end":
            running.discard(ev["sandbox_id"])
        elif kind in counts:
            counts[kind] += 1
        elif kind == "plan":
            for t in ev["tasks"]:
                print(f"           task {t['id']}: {t['title']}")
        elif kind == "task_done":
            tasks[ev["task_id"]] = ev
        elif kind == "verify_result":
            verified.append(ev)
        elif kind == "replay_result":
            replays.append(ev)
        elif kind == "chart_review":
            reviews.append(ev)
        elif kind == "report":
            report = ev["report"]
        elif kind == "sealed":
            sealed = ev
        elif kind == "error":
            errors.append(f"{ev['message']}: {ev.get('detail', '')[:300]}")
    total = round(time.monotonic() - t0, 1)

    check(not errors, "no pipeline errors" + (f": {errors}" if errors else ""))
    ok_tasks = [t for t in tasks.values() if t["ok"]]
    check(bool(tasks) and len(ok_tasks) == len(tasks), f"{len(ok_tasks)}/{len(tasks)} tasks succeeded "
          f"(self-healing retries: {counts['healing']}, policy blocks: {counts['policy_block']})")
    charts = sum(1 for t in tasks.values() if t.get("chart_b64"))
    print(f"           charts produced: {charts}, chart reviews: "
          f"{[r['verdict'] for r in reviews] or 'none (VISION_MODEL off?)'}")
    statuses = [v["status"] for v in verified]
    check(bool(verified) and "unverified" not in statuses,
          f"numbers verified independently: {statuses} (mismatches -> tie-breaker: {counts['verify_mismatch']})")
    check(bool(replays) and all(r["reproducible"] for r in replays),
          f"replays reproducible: {[r['reproducible'] for r in replays]}")
    if report:
        for s in report.get("summary", []):
            print(f"           > {s.get('text', '')[:160]}")
    check(bool(report and report.get("summary")), "answer written from verified evidence")
    check(sealed is not None, f"signed receipt sealed (key {sealed['key_fingerprint'] if sealed else '-'})")
    print(f"           peak parallel sandboxes: {peak_sbx} · total {total}s")
    if sealed:
        name = f"receipt-{sealed['run_id']}.json"
        with open(name, "wb") as fh:
            fh.write(request(base, sealed["download"]))
        with open("public-key.pem", "wb") as fh:
            fh.write(request(base, "/api/receipts/public-key"))
        print(f"           saved {name} + public-key.pem -> python -m scripts.verify_receipt {name} public-key.pem")


def attacks(base: str) -> None:
    print("\n=== Attack Lab (policy gate off: the sandbox must hold on its own)")
    for atk in json.loads(request(base, "/api/attacks")):
        t0, result, run_out = time.monotonic(), None, []
        for ev in sse(base, f"/api/attacks/{atk['id']}/run"):
            if ev["type"] == "attack_run":
                run_out.append(f"{ev.get('status')} {ev.get('duration_s')}s peak {ev.get('peak_mem_mb')} MB")
            elif ev["type"] == "attack_result":
                result = ev
        secs = round(time.monotonic() - t0, 1)
        detail = "; ".join(run_out) if run_out else "full analysis on the hostile CSV"
        check(bool(result and result["contained"]),
              f"{atk['title']:<30} {'CONTAINED' if result and result['contained'] else 'CHECK'} "
              f"({secs}s · {detail} · host canary {'ok' if result and result['host_canary_intact'] else '??'})")


def red_team(base: str) -> None:
    print("\n=== Autonomous red team")
    summary = None
    for ev in sse(base, "/api/redteam/run"):
        if ev["type"] == "redteam_attack" and ev.get("declined"):
            print(f"  goal {ev['goal_id']}: model declined to write the attack")
        elif ev["type"] == "redteam_result":
            print(f"  goal {ev['goal_id']}: {ev['status']:<7} claimed_success={ev['claimed_success']} "
                  f"peak {ev['peak_mem_mb']} MB · {ev['goal'][:60]}")
        elif ev["type"] == "redteam_summary":
            summary = ev
    ran = summary["attacks"] if summary else 0
    check(ran > 0, f"red team actually ran attacks: {ran} ran, {summary.get('declined', 0) if summary else '?'} declined "
                   f"(if 0 ran, set REDTEAM_MODEL to a model that writes them)")
    check(bool(summary and summary["contained"]),
          f"red team: {'ALL CONTAINED' if summary and summary['contained'] else 'CHECK RESULTS'} "
          f"(API key never seen: {not summary.get('key_leaked') if summary else '?'})")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    base, flags = sys.argv[1].rstrip("/"), set(sys.argv[2:])
    health = json.loads(request(base, "/api/health"))
    print(f"Server {base}: docker={health['docker']} model={health['model']} verifier={health['verifier_model']} "
          f"vision={health['vision_model']} runtime={health['runtime']}")
    check(health["docker"] and health["model_configured"], "health: sandbox engine and model configured")
    try:
        analyst(base)
        if "--analyst" not in flags:
            attacks(base)
        if "--redteam" in flags:
            red_team(base)
    except urllib.error.HTTPError as exc:
        check(False, f"HTTP {exc.code} from server: {exc.read().decode()[:200]}")
    print(f"\n{'ALL PASSED' if not FAILS else f'{len(FAILS)} FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
