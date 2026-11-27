"""Verify a Receipts receipt offline, without trusting the server.

    python -m scripts.verify_receipt receipt-<run_id>.json [public_key.pem]

Checks the Ed25519 signature over the whole receipt and that every task's code matches its recorded hash.
Pass the public key you downloaded earlier from /api/receipts/public-key to pin the issuer (otherwise the
key embedded in the receipt is used).
"""
import hashlib
import json
import sys
from pathlib import Path

from backend.receipts import verify

sealed = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
pinned = Path(sys.argv[2]).read_text() if len(sys.argv) > 2 else None
receipt = sealed["receipt"]

signature_ok = verify(sealed, pinned)
code_ok = all(hashlib.sha256(t["code"].encode()).hexdigest() == t["code_sha256"] for t in receipt["tasks"])

print(f"Receipt {receipt['run_id']} issued {receipt['issued_at']}")
print(f"Question: {receipt['question']}")
print(f"Dataset sha256: {receipt['dataset']['sha256']}")
print(f"Signature ({sealed['algorithm']}, key {sealed['key_fingerprint']}): {'VALID' if signature_ok else 'INVALID'}")
print(f"Code hashes: {'match' if code_ok else 'MISMATCH'}")
for v in receipt["verification"]:
    print(f"  {v['status']:>10}  {v['name']} = {v['claimed']}")
sys.exit(0 if signature_ok and code_ok else 1)
