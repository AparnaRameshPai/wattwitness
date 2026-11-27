"""All settings come from environment variables (.env). Secrets live only in this process, never in a sandbox."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Vultr Serverless Inference (OpenAI-compatible)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.vultrinference.com/v1")
# On the VM the key comes from /etc/agent/inference.env (systemd EnvironmentFile), never from a file in the repo.
LLM_API_KEY = os.getenv("VULTR_INFERENCE_KEY", "") or os.getenv("VULTR_INFERENCE_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
# Optional second model for the verifier, so the two "interns" are truly independent.
VERIFIER_MODEL = os.getenv("VERIFIER_MODEL", "") or LLM_MODEL
# Optional vision-capable model that critiques every chart. Empty = chart critic switched off.
VISION_MODEL = os.getenv("VISION_MODEL", "")
# Model the red-team agent uses to invent attacks. Empty = same as LLM_MODEL.
REDTEAM_MODEL = os.getenv("REDTEAM_MODEL", "") or LLM_MODEL
# Shown in the website's Vultr sidebar (region and hostname come from the Vultr metadata service automatically).
VULTR_PLAN = os.getenv("VULTR_PLAN", "")
# Max tokens per LLM reply. Reasoning models think before answering: 2500 cut scripts off mid-line (tested).
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8000"))
# Re-run every successful task in a fresh sandbox and compare output hashes.
REPLAY = os.getenv("REPLAY", "true").lower() == "true"

# Sandbox limits
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "receipts-sandbox:latest")
SANDBOX_RUNTIME = os.getenv("SANDBOX_RUNTIME", "")  # set to "runsc" on the VM to use gVisor
SANDBOX_MEMORY = os.getenv("SANDBOX_MEMORY", "512m")
SANDBOX_CPUS = os.getenv("SANDBOX_CPUS", "1")
# Process cap INSIDE the sandbox (RLIMIT_NPROC via --ulimit nproc): a fork bomb gets a clean "Resource
# temporarily unavailable". The cgroup --pids-limit is only a high host-side backstop: under gVisor it also
# counts the sandbox kernel's own threads, and a low value crashes the whole sandbox instead (tested).
SANDBOX_PIDS = int(os.getenv("SANDBOX_PIDS", "64"))
SANDBOX_PIDS_BACKSTOP = int(os.getenv("SANDBOX_PIDS_BACKSTOP", "1024"))
SANDBOX_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "30"))

# Public-demo protection: at most this many sandboxes run at once on the VM (the rest queue), at most this many
# analyses/attacks run at once in total, and one at a time per visitor.
MAX_PARALLEL_SANDBOXES = int(os.getenv("MAX_PARALLEL_SANDBOXES", "6"))
MAX_ACTIVE_RUNS = int(os.getenv("MAX_ACTIVE_RUNS", "3"))

# Agent behaviour
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "3"))
MAX_TASKS = int(os.getenv("MAX_TASKS", "4"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))

DATA_DIR = ROOT / "data" / "uploads"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DEMO_DIR = ROOT / "demo-data"
ATTACK_DIR = ROOT / "attacks"
FRONTEND_DIR = ROOT / "frontend"
