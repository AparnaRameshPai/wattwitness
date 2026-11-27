"""Signed receipts: every run is sealed into a JSON bundle signed with Ed25519.

Anyone with the public key can verify offline (scripts/verify_receipt.py) that the receipt was issued by this
server and has not been altered: question, dataset hash, every piece of code, every output hash, every sandbox ID,
verification results and the final answer.
"""
import base64
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from . import config

KEY_DIR = config.ROOT / "data" / "keys"
RECEIPT_DIR = config.ROOT / "data" / "receipts"
KEY_DIR.mkdir(parents=True, exist_ok=True)
RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
_KEY_PATH = KEY_DIR / "receipt_signing_key.pem"


def _private_key() -> Ed25519PrivateKey:
    if _KEY_PATH.exists():
        return serialization.load_pem_private_key(_KEY_PATH.read_bytes(), password=None)
    key = Ed25519PrivateKey.generate()
    _KEY_PATH.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.NoEncryption()))
    _KEY_PATH.chmod(0o600)
    return key


_KEY = _private_key()


def public_key_pem() -> str:
    return _KEY.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def fingerprint() -> str:
    return hashlib.sha256(public_key_pem().encode()).hexdigest()[:16]


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def seal(receipt: dict) -> dict:
    signature = base64.b64encode(_KEY.sign(canonical(receipt))).decode()
    sealed = {"receipt": receipt, "algorithm": "Ed25519", "signature": signature,
              "public_key": public_key_pem(), "key_fingerprint": fingerprint()}
    (RECEIPT_DIR / f"{receipt['run_id']}.json").write_text(json.dumps(sealed, indent=1), encoding="utf-8")
    return sealed


def verify(sealed: dict, public_pem: str | None = None) -> bool:
    key: Ed25519PublicKey = serialization.load_pem_public_key((public_pem or sealed["public_key"]).encode())
    try:
        key.verify(base64.b64decode(sealed["signature"]), canonical(sealed["receipt"]))
        return True
    except InvalidSignature:
        return False
