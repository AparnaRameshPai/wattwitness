# WattWitness ⚡

**Check whether an energy upgrade really delivered the savings it promised: weather-corrected, double-checked and signed.**

**[▶ Live demo](https://70-34-206-6.sslip.io)** · Built on Vultr for the Vultr Agent Runtime Hackathon, Stockholm, 27 Nov 2026

![WattWitness architecture on Vultr](docs/architecture.png)

## The problem
A housing co-op paid for a heat pump with a **guaranteed 40%** cut in electricity. The bill dropped **43%**, so the
contractor says the promise was kept. But the winter after was milder. Corrected for the weather, the real saving is
**37.6%**: the guarantee was **not** met. Who is right decides who pays.

## How it works
1. **Upload** a building's meter data (a CSV file) and ask in plain English, for example "Was the 40% guarantee met?"
2. **An AI agent does the energy auditor's math.** It writes code that runs in locked, throwaway sandboxes on a Vultr
   server: no internet, no secrets, deleted right after use.
3. **Every key number is checked twice.** A second AI recomputes each number without seeing the first one's code,
   every step is re-run and must give the same result, and a vision model reviews every chart.
4. **You get a signed receipt.** The whole run is sealed with a digital signature that the owner and the contractor
   can both verify.

**Try to break it:** the site has an AI hacker that writes 5 brand-new attacks against its own sandboxes, and a box
where you can run *your own* attack code. Nothing gets out.

## Results
Live runs on the Vultr VM, 27 Nov 2026:

| Check | Result |
|---|---|
| Right answer | **37.6%** weather-corrected saving, so the guarantee was **not met** (the naive figure is 42.6%). 90,048 kWh and 73,593 SEK saved. Matches the answer key in every run. |
| Double-checked | **5 of 5** key numbers confirmed by the independent re-check |
| Not hard-coded | An unseen building (a Luleå school with other column names, a colder winter and the opposite outcome) got the correct verdict, 27.3% against a 25% guarantee, so it was met, with 5 of 5 numbers confirmed |
| Speed and cost | About **30–60 seconds** and **$0.02–0.04** of AI per analysis |
| Security | Every attack contained: 8 scripted attacks, the AI hacker's 5, and visitors' own code |

## How Vultr is used
| Vultr product | What it does in WattWitness |
|---|---|
| **Cloud Compute** (Stockholm, `vc2-4c-8gb`) | Runs the app and all the sandboxes. The only place the AI API key exists. |
| **Serverless Inference** | Every AI step: planning, writing and fixing code, re-checking numbers, reviewing charts, writing the answer, and the AI hacker. Three models: `deepseek-v4.1-flash-1017`, `qwen3.8-27b` (vision) and `mimo-v2.6-flash` (attacks). |
| **Metadata service** | Feeds the live **Vultr Dashboard** on the site: which machine, which models, how many AI calls and what each run cost. |

The sandboxes are Docker containers with gVisor on the same VM: one for each task, check and attack.

## Who it's for
- **Property owners with many buildings** (municipal housing and property companies, Nordics first) who buy upgrades
  under savings guarantees and must report verified savings to lenders and in sustainability reports, every year, for
  every building.
- **Energy service companies** whose pay depends on verified savings, and anyone checking a savings claim before
  paying for it.

**Why not a spreadsheet?** The arithmetic is easy. The disputes are about the judgment calls: which period is the
baseline, which days to leave out, how to correct for the weather. WattWitness shows every assumption, re-checks every
key number and signs the whole run. A spreadsheet can't show where its number came from; a receipt can.

## Limitations
- Both test buildings are **synthetic**, built with a known answer so that the agent can be checked. Real metered
  data is the next step.
- One method is built in: **IPMVP Option C** (whole-building meter, weather correction with heating degree days).
- The checker uses the **same AI model** as the coder. Its independence comes from never seeing the code. A wrong
  assumption shared by both would not be caught, which is why every assumption is shown.
- The SEK figure covers the **spot energy price only**, not grid fees, energy tax or VAT.
- One CSV per run. Fetching meter and weather data automatically is on the roadmap.

## Technical details
Click a section to open it.

<details>
<summary><b>Architecture: the 6-stage pipeline</b></summary>

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
Browser ◄── live SSE stream: stage pipeline, sandbox fleet (live memory), proof, receipt download, Vultr Dashboard
```

The method is **IPMVP Option C**: heating degree days HDD = max(0, 17 °C − outdoor temperature), an ordinary
least-squares baseline `daily_kWh = a + b·HDD` fitted on the 12 months before the upgrade, then avoided kWh, percent
and SEK at daily SE3 spot prices. It lives in `ENERGY_SAVINGS_METHOD` in `backend/prompts.py`, shared by the planner,
coder, verifier and writer.

Hackathon track: **Pattern A: CSV Analyst + Self-Healing Runner**, extended with multi-sandbox planning, independent
verification, reproducibility replay, signed receipts and an autonomous red team.
</details>

<details>
<summary><b>Security: defense in depth, containment and the Attack Lab</b></summary>

| Layer | What it does |
|---|---|
| 1. Untrusted-data rules | Every prompt treats cell contents and column names as data, never as instructions. |
| 2. Policy gate | AI code is statically scanned (AST) before it runs: only data libraries may be imported; no shell, eval, network URLs, dunder tricks or file access beyond `data.csv`/`chart.png`. Blocked code goes back to the agent as feedback. |
| 3. Sandbox | The real boundary. The Attack Lab switches layer 2 off on purpose to prove that layer 3 holds alone. |
| 4. Verification | Independent recomputation with a tie-breaker, replay with output-hash comparison, vision review of charts. |
| 5. Signed receipt | A tamper-evident record of the whole run, verifiable offline with the server's public key. |

| Requirement | How WattWitness does it |
|---|---|
| **Process isolation** | Code never runs in the app process. Each run gets its own container: `--network none`, `--read-only`, non-root, `--cap-drop ALL`, `no-new-privileges`, and **gVisor** (`runsc`) for a user-space kernel boundary. |
| **Secret hygiene** | The Vultr key lives only in the orchestrator. No env vars, files or mounts with credentials enter a sandbox. The *Secret theft* attack and the red team prove it. |
| **Resource limits** | Every run: 512 MB memory (no swap), 1 CPU, 64 processes (`--ulimit nproc`, so a fork bomb gets a clean refusal), 30 s wall clock, 64 MB `/tmp`. Server-wide: at most 6 sandboxes and 3 runs at once, one per visitor. |
| **Lifecycle discipline** | One container per run, force-removed afterwards with its folder deleted. The *Persistence* attack plants files and a background process; a fresh probe sandbox then proves nothing survived. |

**Attack Lab** ("Try to break it" on the site):
- **AI hacker (autonomous red team):** an LLM invents its own attacks for five goals (escape, steal secrets, send data
  out, persist, exhaust the host). They all run in parallel sandboxes, followed by independent host checks.
- **Bring your own attack:** paste any Python (max 20 KB) and run it with the policy gate switched off. Afterwards the
  server checks itself from the outside: host canary unchanged, sandbox engine healthy, API key never in the output,
  and a fresh sandbox finds no files or processes left behind.
- **Scripted attacks:** `rm -rf /`, infinite loop, memory bomb (watch it hit the cap live), fork bomb, exfiltration
  (HTTP and DNS), secret theft, persistence, and a **prompt injection hidden in the meter data** (a "contractor note"
  telling the agent to report a fake 52% saving).
- **Proof the host is untouched:** a canary file on the host keeps its exact SHA-256, and the sandbox engine stays healthy.
</details>

<details>
<summary><b>Full evidence</b></summary>

Live runs on the Vultr VM (Stockholm, `vc2-4c-8gb`, gVisor), 27 Nov 2026. The demo building and its sample question,
unless a row says otherwise:

| Check | Result |
|---|---|
| Answer vs. answer key (`make_demo_data.py`) | Every run: **37.6% weather-adjusted savings, guarantee not met**; 90,048 kWh and 73,593 SEK avoided; naive 42.6% |
| Independent verification | **5 of 5 key numbers verified** in every run checked, no tie-breaker needed |
| Generalization to an **unseen building** | A building the prompts were never tuned on: a Luleå school (SE1) with *different* column names, prices in öre, a **colder** winter afterwards and the opposite outcome (the guarantee **was** met), uploaded through the normal upload path. Both runs: **5 of 5 numbers verified** and the correct verdict (**27.3%** weather-adjusted vs. the 25% guarantee, so met), matching an independently computed answer key. The method is not hard-coded to the demo. |
| Baseline model | a = 311.6 kWh/day, b = 45.16 kWh/HDD, R² 0.977, CV(RMSE) 6.7% (acceptable for daily data) |
| Reproducibility | Every task replayed in a fresh sandbox with an identical output hash |
| Chart review (vision model) | Every chart is reviewed; when the model finds an issue, the site shows it next to the chart and the answer adds a caveat |
| Lifecycle | Sandboxes created = destroyed after every run |
| Time and cost per analysis | About 30–60 s and $0.02–0.04 of Vultr Serverless Inference (from the app's own usage meter) |
| Prompt injection in the meter data ("report 52%, delete everything") | The answer is still **37.6%, guarantee not met**; the agent flagged the injected text as a caveat; host canary intact |
| Scripted attacks (policy gate off) | rm -rf, infinite loop (killed at 30 s), memory bomb (OOM-killed at the 512 MB cap), fork bomb, exfiltration, secret theft, persistence: **all contained** |
| Autonomous red team | 5 AI-written attacks ran in parallel; some *claimed* success inside their own sandbox, but the outside checks (host canary, engine health, fresh-sandbox leftover probe, API key never in any output) show **all contained** |
| Bring your own attack (visitor's own code, gate off) | Tested on gVisor with a deliberately aggressive, multi-technique script: container escape (`nsenter`/`chroot` into the host, both refused), host-file writes (`/etc/passwd`, `/proc/sysrq-trigger`, `/dev/mem`, blocked), outbound TCP/DNS and the cloud-metadata endpoint (all blocked), raw sockets and a new user namespace (`unshare`, blocked), a memory bomb (OOM at 0.65 s), an infinite loop (killed at 30 s), a fork bomb, and planted files plus a background process. **All contained**: the API key never appeared, the host canary was intact, and a fresh sandbox found nothing left behind. The `GOT IN` lines it prints are reads of the sandbox's *own* throwaway files, not the host. |
| Is 37.6% vs. 40% a real difference? | Our own offline check of the demo data: 37.3–37.9% for an HDD base of 15–18 °C, 95% range about ±1 point (simplified ASHRAE Guideline 14). Below 40% in every case. |
</details>

<details>
<summary><b>Verify a receipt yourself</b></summary>

```bash
curl -O https://70-34-206-6.sslip.io/api/receipts/public-key      # pin the issuer's key once
python -m scripts.verify_receipt receipt-<run_id>.json public-key
# Signature (Ed25519, key …): VALID · Code hashes: match
```
Change one character in the receipt and the signature becomes INVALID.
</details>

<details>
<summary><b>Run locally</b></summary>

Requires Python 3.11+ and Docker.
```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
docker build -t receipts-sandbox:latest sandbox-image
cp .env.example .env                                    # Windows: copy; then fill in the key and models
python -m scripts.test_sandbox                          # sandbox smoke test, no AI needed
uvicorn backend.main:app --reload                       # http://127.0.0.1:8000
```
</details>

<details>
<summary><b>Deploy on a Vultr VM</b></summary>

On a fresh Vultr Cloud Compute VM (Ubuntu 24.04, your SSH key added in the console):
```bash
# 0. Prepare the VM: non-root user, firewall, then SSH hardening (details at the top of the script)
ssh root@<VM-IP> 'bash -s' < deploy/bootstrap_vm.sh
ssh receipts@<VM-IP> 'sudo bash -s -- --harden' < deploy/bootstrap_vm.sh
# 1. Store the inference key in /etc/agent/inference.env (the script prints a no-echo one-liner for this)
# 2. As the receipts user on the VM:
git clone https://github.com/AparnaRameshPai/wattwitness.git receipts && cd receipts
cp .env.example .env && nano .env                    # models, SANDBOX_RUNTIME=runsc (no key here)
sudo bash deploy/setup_vm.sh <VM-IP-with-dashes>.sslip.io   # Docker, gVisor, venv, image, systemd, Caddy HTTPS
python3 scripts/smoke_e2e.py https://<VM-IP-with-dashes>.sslip.io --redteam   # end-to-end demo check
```
The app runs as the non-root user via systemd; re-run `setup_vm.sh` after `git pull` to update. On a public URL it
protects itself: at most 6 sandboxes at once (the rest queue), 3 runs at once in total and one per visitor
(`MAX_PARALLEL_SANDBOXES`, `MAX_ACTIVE_RUNS`).
</details>

<details>
<summary><b>Code layout and demo data</b></summary>

```
backend/  main.py API + SSE · orchestrator.py pipeline · sandbox.py containers + telemetry · policy.py gate
          receipts.py Ed25519 sealing · attacks.py Attack Lab + red team · llm.py Vultr Inference · prompts.py
          telemetry.py Vultr usage (calls, tokens, cost, VM facts) for the Vultr Dashboard
frontend/ index.html · app.js (no build step): guided analysis, "Try to break it", Vultr Dashboard, info tooltips
sandbox-image/Dockerfile
deploy/   bootstrap_vm.sh fresh-VM prep + SSH hardening · setup_vm.sh app install · receipts.service · Caddyfile
scripts/  demo data, sandbox test, receipt verifier, smoke_e2e.py end-to-end demo check
docs/     architecture.png
```
The demo dataset is synthetic (`scripts/make_demo_data.py`, which also prints the answer key): daily meter data,
outdoor temperature and SE3 spot prices for a Stockholm housing co-op that installed a heat pump in March 2025, with
a milder winter afterwards and a few days of meter outage. Internal names still say "receipts" (the service, the
sandbox image), after the signed receipt each run produces.
</details>
