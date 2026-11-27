# Receipts ✓

**Verified energy savings: every number proven, every action contained, every run signed.**

A housing cooperative pays a contractor for a heat pump that is *guaranteed* to cut electricity use by 40%.
A year later the bill is lower, but was that the heat pump, or just a mild winter? Owner and contractor
disagree, and money (a performance guarantee, a bonus, a grant) depends on the answer.

Receipts settles it. Upload the building's meter data and ask in plain English. An AI agent does what an energy
auditor would do: it builds a **weather-adjusted baseline** (IPMVP Option C, heating degree days) and computes the
avoided energy in kWh and SEK at Nord Pool spot prices. Every line of AI-written code runs in its own throwaway
sandbox on Vultr. An independent verifier recomputes every key number, every task is replayed to prove it is
reproducible, and a vision model critiques every chart. The finished run is sealed into an **Ed25519-signed
receipt** that both parties can verify offline. An **autonomous red-team agent** attacks the sandbox live to prove
the containment holds.

> When money depends on an AI's number, two things must be true: the number is right, and the AI cannot hurt you.
> Receipts proves both, and hands every party the same cryptographic receipt.

**Who it is for:** building owners (housing cooperatives, municipalities, property companies) and the energy
service companies whose pay depends on verified savings, and anyone checking a savings claim before paying for it.
It still works as a general CSV analyst for any other data.

**The demo:** a synthetic Stockholm housing co-op (price area SE3). The naive before/after comparison says the
heat pump saved about 43%, so the guarantee looks met. After adjusting for the milder winter, it saved about 38%:
the guarantee was **not** met. Receipts proves it, number by number, and signs the result.

Track: **Pattern A: CSV Analyst + Self-Healing Runner**, extended with multi-sandbox planning, independent
verification, reproducibility replay, signed receipts and an autonomous red team.

## Architecture

```
Browser ──upload + question──► Vultr VM: FastAPI orchestrator   (holds the ONLY API key)
                                   │                                LLMs: Vultr Serverless Inference
   1 PROFILE  ─────────────────────► sandbox reads the CSV (the host never parses untrusted data with code)
   2 PLAN     ── planner LLM ──────► 1–4 independent tasks
   3 EXECUTE  ── coder LLM ─► POLICY GATE (static AST scan) ─► one sandbox PER TASK, in parallel
                    ▲            │ blocked? / error? ──────────► fixer LLM ─► retry (max 3)
   4 ASSURE   (all in parallel)
       verify  ─ verifier LLM never sees the original code ─► fresh sandbox per number; mismatch ─► tie-breaker
       replay  ─ same code, brand-new sandbox ─► output hash must be identical
       critique─ vision LLM inspects every chart for misleading or empty plots
   5 WRITE    ── writer LLM uses ONLY verified evidence ─► answer + assumptions + caveats
   6 SEAL     ── Ed25519 signature over question, dataset hash, all code, output hashes, sandbox IDs, verdicts
                                   │
Browser ◄── live SSE stream: stage pipeline, sandbox fleet (live CPU/memory), timeline, proof, receipt download
```

## How Vultr is used
| Vultr product | Role in Receipts |
|---|---|
| **Cloud Compute VM** (Stockholm) | Runs the orchestrator (FastAPI behind Caddy HTTPS) and the sandbox fleet. The only place the API key exists. |
| **Serverless Inference** | Every AI step: planner, coder, fixer, verifier, tie-breaker, chart critic (vision), writer and red team. |
| **Docker + gVisor on the VM** | One throwaway sandbox per task, check and attack. |

The website's **"How Vultr powers this"** sidebar shows this live: an architecture diagram that lights up as the agent
works, every Serverless Inference call (model, role, tokens, time), the cost of each run from Vultr's published
per-token prices, and the VM's facts (region, plan, size) from the Vultr metadata service (`GET /api/vultr`).

## Defense in depth

| Layer | What it does |
|---|---|
| 1. Untrusted-data rules | Every prompt treats cell contents and column names as data, never as instructions. |
| 2. Policy gate | AI code is statically scanned (AST) before execution: only data libraries may be imported; no shell, eval, network URLs, dunder tricks or file access beyond `data.csv`/`chart.png`. Blocked code goes back to the agent as feedback. |
| 3. Sandbox | The boundary. See the containment table. The Attack Lab skips layer 2 on purpose to prove layer 3 holds alone. |
| 4. Verification | Independent recomputation with tie-breaker; replay with output-hash comparison; vision critique of charts. |
| 5. Signed receipt | Tamper-evident record of the entire run, verifiable offline with the server's public key. |

## Containment

| Requirement | How Receipts does it |
|---|---|
| **Process isolation** | Code never runs in the app process. Each run gets its own container: `--network none`, `--read-only`, non-root, `--cap-drop ALL`, `no-new-privileges`, and **gVisor** (`runsc`) on the VM for a user-space kernel boundary. |
| **Secret hygiene** | The Vultr key lives only in the orchestrator. No env vars, files or mounts with credentials enter a sandbox. The *Secret theft* attack and the red team prove it. |
| **Resource limits** | Every run: 512 MB memory (no swap), 1 CPU, 64 processes (`--ulimit nproc`, so a fork bomb gets a clean refusal), 30 s wall clock, 64 MB `/tmp`. Live telemetry shows usage against the caps. Server-wide: at most 6 sandboxes at once. |
| **Lifecycle discipline** | One container per run, force-removed afterwards with its folder deleted. The *Persistence* attack plants files and a background process, and then a fresh probe sandbox proves nothing survived. |

### Attack Lab
- **Autonomous red-team agent:** an LLM invents its own attacks for five goals (escape, steal secrets, exfiltrate, persist, exhaust the host). They all run in parallel sandboxes, followed by independent host checks.
- **Scripted attacks:** `rm -rf /`, infinite loop, memory bomb (watch it hit the cap live), fork bomb, exfiltration (HTTP and DNS), secret theft, persistence, and a **prompt injection hidden in the meter data** (a "contractor note" telling the agent to report a fake 52% saving).
- **Proof the host is untouched:** a canary file on the host keeps its exact SHA-256, and the sandbox engine stays healthy.

## Verify a receipt yourself
```bash
curl -O https://<demo-url>/api/receipts/public-key        # pin the issuer's key once
python -m scripts.verify_receipt receipt-<run_id>.json public-key
# Signature (Ed25519, key …): VALID · Code hashes: match
```
Change one character in the receipt and the signature becomes INVALID.

## Run locally
Requires Python 3.11+ and Docker.
```bash
python -m venv .venv && .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
docker build -t receipts-sandbox:latest sandbox-image
copy .env.example .env                               # fill in key + models
python -m scripts.test_sandbox                       # sandbox smoke test, no AI needed
uvicorn backend.main:app --reload                    # http://127.0.0.1:8000
```

## Deploy on the Vultr VM
On a fresh Vultr Cloud Compute VM (Ubuntu 24.04, your SSH key added in the console):
```bash
# 0. Prepare the VM: non-root user, firewall, then SSH hardening (details at the top of the script)
ssh root@<VM-IP> 'bash -s' < deploy/bootstrap_vm.sh
ssh receipts@<VM-IP> 'sudo bash -s -- --harden' < deploy/bootstrap_vm.sh
# 1. Store the inference key in /etc/agent/inference.env (the script prints a no-echo one-liner for this)
# 2. As the receipts user on the VM:
git clone https://github.com/AparnaRameshPai/vultr-hackathon-reciepts.git receipts && cd receipts
cp .env.example .env && nano .env                    # models, SANDBOX_RUNTIME=runsc (no key here)
sudo bash deploy/setup_vm.sh <VM-IP-with-dashes>.sslip.io   # Docker, gVisor, venv, image, systemd, Caddy HTTPS
python3 scripts/smoke_e2e.py https://<VM-IP-with-dashes>.sslip.io --redteam   # end-to-end demo check
```
The app runs as the non-root user via systemd; re-run `setup_vm.sh` after `git pull` to update.
On a public URL it protects itself: at most 6 sandboxes at once (the rest queue), 3 runs at once in total and one
per visitor (`MAX_PARALLEL_SANDBOXES`, `MAX_ACTIVE_RUNS`).

## Layout
```
backend/  main.py API + SSE · orchestrator.py pipeline · sandbox.py containers + telemetry · policy.py gate
          receipts.py Ed25519 sealing · attacks.py Attack Lab + red team · llm.py Vultr Inference · prompts.py
          telemetry.py Vultr usage (calls, tokens, cost, VM facts) for the sidebar
frontend/ index.html · app.js (no build step): guided analysis, "Try to break it", Vultr sidebar, light/dark
sandbox-image/Dockerfile
deploy/   bootstrap_vm.sh fresh-VM prep + SSH hardening · setup_vm.sh app install · receipts.service · Caddyfile
scripts/  demo data, sandbox test, receipt verifier, smoke_e2e.py end-to-end demo check
```
The demo dataset is synthetic (`scripts/make_demo_data.py`, which also prints the answer key): daily meter data, outdoor temperature and SE3 spot prices for a Stockholm housing co-op that installed a heat pump in March 2025, with a milder winter afterwards and a few days of meter outage.
