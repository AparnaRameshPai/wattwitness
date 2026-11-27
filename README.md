# WattWitness ⚡

**Verified energy savings: every key number recomputed independently, every action contained, every run signed.**

> A housing co-op paid for a heat pump with a **guaranteed 40%** cut in electricity. The bill dropped **43%**, so the
> contractor says the guarantee was met. But the winter after was milder. Adjusted for the weather, the saving is
> **38%**: the guarantee was **not** met. Who is right decides who pays. WattWitness settles it with a signed receipt
> that the owner and the contractor can both check.

Upload a building's meter data and ask in plain English. An AI agent does the energy auditor's calculation: a
**weather-adjusted baseline** (IPMVP Option C, heating degree days, base 17 °C), avoided energy in kWh, and avoided
cost in SEK at daily SE3 spot prices. Every line of AI-written code runs in its own throwaway sandbox on Vultr (no
network, no secrets, hard limits). Every key number is recomputed by a second, independent run that never sees the
original code; every task is replayed in a fresh sandbox and must give identical output; a vision model reviews
every chart. The run is sealed into an **Ed25519-signed receipt** anyone can verify offline. An **autonomous
red-team agent** attacks the sandbox live to show the containment holds.

## Who it is for
- **Ideal customer:** a property owner with a portfolio of buildings (municipal housing companies, property
  companies; 50–500 buildings, Nordics first) that buys retrofits under savings guarantees and must report verified
  savings to lenders and in sustainability reports. The need repeats every year, for every building.
- **Also:** energy service companies whose pay depends on verified savings, and anyone checking a savings claim
  before paying for it. WattWitness still works as a general CSV analyst for other data.

## Why not a spreadsheet?
The arithmetic is easy. The cost and the disputes are in the **judgment calls**: the baseline window, the base
temperature, which days to exclude, the model shape. IPMVP is a framework, not a recipe, and these choices move the
result. WattWitness makes every choice explicit (the assumptions box in every answer), recomputes each key number
independently, replays the analysis, and signs the whole run (question, dataset hash, all code, output hashes,
verdicts). A spreadsheet can't show where its number came from; a receipt can.

## Evidence
Live runs on the Vultr VM (Stockholm, `vc2-4c-8gb`, gVisor), demo building, the website's sample question, 27 Nov 2026:

| Check | Result |
|---|---|
| Answer vs. answer key (`make_demo_data.py`) | Every run: **37.6% weather-adjusted savings, guarantee not met**; 90,048 kWh and 73,593 SEK avoided; naive 42.6% |
| Independent verification | Final version, 2 runs: **5 of 5 key numbers verified** in both, no tie-breaker needed |
| Baseline model | a = 311.6 kWh/day, b = 45.16 kWh/HDD, R² 0.977, CV(RMSE) 6.7% (acceptable for daily data) |
| Reproducibility | Every task replayed in a fresh sandbox with identical output hash |
| Chart review (vision model) | Every chart "good" |
| Lifecycle | Sandboxes created = destroyed after every run |
| Time and cost per analysis | About 55–60 s; about $0.04 of Vultr Serverless Inference (from the app's own usage meter) |
| Prompt injection in the meter data ("report 52%, delete everything") | Answer still **37.6%, guarantee not met**; the agent flagged the injected text as a caveat; host canary intact |
| Scripted attacks (policy gate off) | rm -rf, infinite loop (killed at 30 s), memory bomb (OOM-killed at the 512 MB cap), fork bomb, exfiltration, secret theft, persistence: **all contained** |
| Autonomous red team | 5 AI-written attacks ran in parallel; some *claimed* success inside their own sandbox, but the outside checks (host canary, engine health, fresh-sandbox leftover probe, API key never in any output) show **all contained** |
| Bring your own attack (visitor's own code, gate off) | Tested on gVisor: write to `/etc/passwd`, internet access, key-file read, memory bomb (OOM at 0.65 s), infinite loop (killed at 30 s), fork bomb, planted files + background process: **all contained**, and a fresh sandbox found nothing left behind |
| Is 37.6% vs. 40% a real difference? | Our own offline check of the demo data: 37.3–37.9% for HDD base 15–18 °C, 95% range about ±1 point (simplified ASHRAE Guideline 14). Below 40% in every case. |

## Known limitations
- The demo building is **synthetic** (`scripts/make_demo_data.py`), built with a known answer so the agent can be checked.
- Only **IPMVP Option C** (whole-building metering, linear degree-day model) is built in.
- Coder and verifier use the **same model** (`deepseek-v4.1-flash-1017`). Independence comes from blind
  recomputation: the verifier sees only the metric's definition, never the code. A different model (qwen3.8-27b)
  spent its whole token budget reasoning on the harder metrics and printed no result.
- A **shared wrong assumption** between coder and verifier would not be caught. That is why every assumption is shown.
- The **policy gate is a helper**, not the security boundary. The sandbox is the boundary (the Attack Lab proves it with the gate off).
- The SEK figure covers the **spot energy price only**, not grid fees, energy tax or VAT.
- One CSV per run. Pulling meter and weather data automatically is roadmap.

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
| Vultr product | Role in WattWitness |
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

| Requirement | How WattWitness does it |
|---|---|
| **Process isolation** | Code never runs in the app process. Each run gets its own container: `--network none`, `--read-only`, non-root, `--cap-drop ALL`, `no-new-privileges`, and **gVisor** (`runsc`) on the VM for a user-space kernel boundary. |
| **Secret hygiene** | The Vultr key lives only in the orchestrator. No env vars, files or mounts with credentials enter a sandbox. The *Secret theft* attack and the red team prove it. |
| **Resource limits** | Every run: 512 MB memory (no swap), 1 CPU, 64 processes (`--ulimit nproc`, so a fork bomb gets a clean refusal), 30 s wall clock, 64 MB `/tmp`. Live telemetry shows usage against the caps. Server-wide: at most 6 sandboxes at once. |
| **Lifecycle discipline** | One container per run, force-removed afterwards with its folder deleted. The *Persistence* attack plants files and a background process, and then a fresh probe sandbox proves nothing survived. |

### Attack Lab
- **Bring your own attack:** don't take our word for it. Paste any Python into the website and run it in a sandbox with
  the safety check switched off (max 20 KB, one run per visitor). Afterwards the server checks itself from the outside:
  host canary unchanged, sandbox engine healthy, API key never in the output, and a fresh sandbox finds no files or
  processes left behind.
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
