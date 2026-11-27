"""The sandbox runner: runs untrusted code in a fresh, locked-down Docker container and destroys it afterwards.

Containment, flag by flag:
  --network none          isolation: no internet, nothing can be exfiltrated
  --read-only, --tmpfs    isolation: system files cannot be changed
  --user 1000, cap-drop   isolation: not root, no special kernel powers
  --runtime runsc         isolation: gVisor kernel boundary (when available)
  --memory/--cpus         resource limits
  --ulimit nproc          resource limits: process cap inside the sandbox (fork bombs fail cleanly)
  --pids-limit            high host-side backstop only (a low value crashes gVisor's own kernel threads)
  timeout + docker kill   resource limits (infinite loops)
  no -e / --env flags     secret hygiene: the container never sees our keys
  docker rm + rmtree      lifecycle: nothing survives the run

While a container runs, its CPU and memory are sampled and streamed as live telemetry.
"""
import asyncio
import base64
import hashlib
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config

MAX_OUTPUT_CHARS = 8000
UNIT_MB = {"b": 1 / 1024**2, "kib": 1 / 1024, "kb": 1 / 1000, "mib": 1, "mb": 1, "gib": 1024, "gb": 1000}


@dataclass
class SandboxResult:
    sandbox_id: str
    status: str  # ok | error | timeout | oom
    exit_code: int | None
    stdout: str
    stderr: str
    duration_s: float
    chart_b64: str | None = None
    peak_mem_mb: float = 0.0
    samples: list = field(default_factory=list)
    stdout_sha256: str = ""
    limits: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


class SandboxStats:
    """Server-wide counters shown in the header."""

    def __init__(self):
        self.created = 0
        self.destroyed = 0

    def to_dict(self) -> dict:
        return {"created": self.created, "destroyed": self.destroyed}


STATS = SandboxStats()


def _docker(*args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def memory_limit_mb() -> float:
    m = re.match(r"(\d+(?:\.\d+)?)\s*([kmg]?)", config.SANDBOX_MEMORY.lower())
    value, unit = float(m.group(1)), m.group(2)
    return value * {"k": 1 / 1024, "m": 1, "g": 1024, "": 1 / 1024**2}[unit]


def _limits() -> dict:
    return {
        "network": "none",
        "memory": config.SANDBOX_MEMORY,
        "memory_mb": memory_limit_mb(),
        "cpus": config.SANDBOX_CPUS,
        "pids": config.SANDBOX_PIDS,
        "parallel_sandboxes_max": config.MAX_PARALLEL_SANDBOXES,
        "timeout_s": config.SANDBOX_TIMEOUT,
        "filesystem": "read-only",
        "user": "non-root (uid 1000)",
        "runtime": config.SANDBOX_RUNTIME or "runc",
        "secrets_inside": 0,
    }


def _to_mb(text: str) -> float:
    m = re.match(r"([\d.]+)\s*([a-zA-Z]+)", text.strip())
    return float(m.group(1)) * UNIT_MB.get(m.group(2).lower(), 1) if m else 0.0


def _sample(name: str) -> dict | None:
    """One CPU/memory reading of a running container (None if it is not running)."""
    try:
        r = _docker("stats", "--no-stream", "--format", "{{.MemUsage}}|{{.CPUPerc}}", name, timeout=5)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or "|" not in r.stdout:
        return None
    mem, cpu = r.stdout.strip().split("|", 1)
    try:
        return {"mem_mb": round(_to_mb(mem.split("/")[0]), 1), "cpu": round(float(cpu.strip().rstrip("%") or 0), 1)}
    except ValueError:
        return None


# Server-wide cap on simultaneously running sandboxes (public demo: protects the VM's CPU and memory).
_SLOTS = threading.BoundedSemaphore(config.MAX_PARALLEL_SANDBOXES)


def run_code(code: str, csv_path: Path | None = None, on_event: Callable[[dict], None] | None = None) -> SandboxResult:
    """Run `code` as /work/main.py with the dataset at /work/data.csv. Blocking; telemetry goes to on_event.
    Waits for a free sandbox slot first (and reports that it is queued)."""
    emit = on_event or (lambda _ev: None)
    sandbox_id = f"sbx-{uuid.uuid4().hex[:8]}"
    if not _SLOTS.acquire(blocking=False):
        emit({"type": "sandbox_queued", "sandbox_id": sandbox_id})
        _SLOTS.acquire()
    try:
        return _run_in_container(code, csv_path, emit, sandbox_id)
    finally:
        _SLOTS.release()


def _run_in_container(code: str, csv_path: Path | None, emit: Callable[[dict], None], sandbox_id: str) -> SandboxResult:
    work = Path(tempfile.mkdtemp(prefix=f"{sandbox_id}-"))  # mounted into the container
    logs = Path(tempfile.mkdtemp(prefix=f"{sandbox_id}-logs-"))  # host only, never mounted
    work.chmod(0o777)  # the container user (uid 1000) must be able to write chart.png
    (work / "main.py").write_text(code, encoding="utf-8")
    if csv_path is not None:
        shutil.copy(csv_path, work / "data.csv")

    cmd = [
        "docker", "run", "--name", sandbox_id,
        "--network", "none",
        "--memory", config.SANDBOX_MEMORY, "--memory-swap", config.SANDBOX_MEMORY,
        "--cpus", config.SANDBOX_CPUS,
        "--ulimit", f"nproc={config.SANDBOX_PIDS}:{config.SANDBOX_PIDS}",
        "--pids-limit", str(config.SANDBOX_PIDS_BACKSTOP),
        "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777",
        "--user", "1000:1000",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "-v", f"{work}:/work", "-w", "/work",
    ]
    if config.SANDBOX_RUNTIME:
        cmd += ["--runtime", config.SANDBOX_RUNTIME]
    cmd += [config.SANDBOX_IMAGE, "python", "main.py"]

    STATS.created += 1
    emit({"type": "sandbox_start", "sandbox_id": sandbox_id, "limits": _limits()})
    start = time.monotonic()
    status, exit_code, peak, samples = "ok", None, 0.0, []
    out_path, err_path = logs / "stdout", logs / "stderr"
    try:
        with out_path.open("wb") as out_f, err_path.open("wb") as err_f:
            proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f)
            last_sample = 0.0
            while proc.poll() is None:
                if time.monotonic() - start > config.SANDBOX_TIMEOUT:
                    _docker("kill", sandbox_id)
                    proc.wait(timeout=20)
                    status = "timeout"
                    break
                if time.monotonic() - last_sample >= 1.0:
                    last_sample = time.monotonic()
                    s = _sample(sandbox_id)
                    if s:
                        peak = max(peak, s["mem_mb"])
                        samples.append({"t": round(time.monotonic() - start, 1), **s})
                        emit({"type": "sandbox_sample", "sandbox_id": sandbox_id, **s})
                time.sleep(0.2)
            exit_code = proc.returncode
        stdout = out_path.read_bytes().decode("utf-8", errors="replace")
        stderr = err_path.read_bytes().decode("utf-8", errors="replace")
        if status == "timeout":
            stderr += f"\n[sandbox] Killed: time limit of {config.SANDBOX_TIMEOUT}s reached."
        elif exit_code != 0:
            status = "error"
            oom = _docker("inspect", "-f", "{{.State.OOMKilled}}", sandbox_id).stdout.strip()
            if oom == "true" or exit_code == 137:
                status = "oom"
                stderr += f"\n[sandbox] Killed: memory limit of {config.SANDBOX_MEMORY} reached."
    finally:
        duration = round(time.monotonic() - start, 2)
        chart = work / "chart.png"
        chart_b64 = base64.b64encode(chart.read_bytes()).decode() if chart.exists() else None
        _docker("rm", "-f", sandbox_id)
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(logs, ignore_errors=True)
        STATS.destroyed += 1

    result = SandboxResult(
        sandbox_id=sandbox_id, status=status, exit_code=exit_code,
        stdout=stdout[-MAX_OUTPUT_CHARS:], stderr=stderr[-MAX_OUTPUT_CHARS:], duration_s=duration,
        chart_b64=chart_b64, peak_mem_mb=peak, samples=samples[-120:],
        stdout_sha256=hashlib.sha256(stdout.encode()).hexdigest(), limits=_limits(),
    )
    emit({"type": "sandbox_end", "sandbox_id": sandbox_id, "status": status, "duration_s": duration,
          "peak_mem_mb": peak, "destroyed": True})
    return result


async def run_code_async(code: str, csv_path: Path | None = None, on_event=None) -> SandboxResult:
    """Non-blocking wrapper so several sandboxes run in parallel. `on_event` is an async callback."""
    callback = None
    if on_event is not None:
        loop = asyncio.get_running_loop()

        def callback(ev: dict):
            asyncio.run_coroutine_threadsafe(on_event(ev), loop)

    return await asyncio.to_thread(run_code, code, csv_path, callback)


def docker_available() -> bool:
    try:
        return _docker("info", timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
