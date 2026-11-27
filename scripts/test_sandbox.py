"""Smoke test for the sandbox, no AI needed. Run from the project root:  python -m scripts.test_sandbox

Checks: normal code runs, a chart comes back, the network is blocked, and the time limit works.
"""
from backend import config
from backend.sandbox import docker_available, run_code

assert docker_available(), "Docker is not running. Start Docker Desktop (or the docker service) first."

ok = run_code("import pandas as pd\nprint(pd.read_csv('data.csv').shape)", config.DEMO_DIR / "sales_2025.csv")
print("1. pandas on demo data:", ok.status, ok.stdout.strip(), ok.stderr.strip()[-300:])

chart = run_code("import matplotlib.pyplot as plt\nplt.plot([1, 3, 2])\nplt.savefig('chart.png')\nprint('saved')")
print("2. chart returned:", chart.chart_b64 is not None, chart.stderr.strip()[-300:])

net = run_code("import urllib.request\ntry:\n    urllib.request.urlopen('http://example.com', timeout=5)\n    print('REACHED')\nexcept Exception as e:\n    print('BLOCKED', type(e).__name__)")
print("3. network:", net.stdout.strip())

loop = run_code("while True:\n    pass")
print("4. infinite loop:", loop.status, f"after {loop.duration_s}s")
