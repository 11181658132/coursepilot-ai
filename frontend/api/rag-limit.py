"""Private signed quota-only worker. Never initializes an index or model client."""

import hashlib
import hmac
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_rag_backend"))
from app.services.public_rag_limits import PublicRagLimits  # noqa: E402


def valid_signature(raw, timestamp, signature):
    secret = os.environ.get("DEMO_RATE_LIMIT_SALT", "")
    if len(secret) < 32:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 60:
            return False
    except (TypeError, ValueError):
        return False
    expected = hmac.new(secret.encode(), b"rag-limit-v1." + timestamp.encode() + b"." + raw,
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def handle(body, factory=PublicRagLimits):
    if (not isinstance(body, dict) or set(body) != {"action", "quota_key"}
            or body["action"] not in {"status", "consume"}
            or not isinstance(body["quota_key"], str)
            or not re.fullmatch(r"[a-f0-9]{64}", body["quota_key"])):
        raise ValueError("Invalid envelope")
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn.startswith(("postgresql://", "postgres://")):
        raise RuntimeError("Persistent database missing")
    return factory(dsn).quota(body["quota_key"], consume=body["action"] == "consume")


class handler(BaseHTTPRequestHandler):
    def reply(self, status, data):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256:
                return self.reply(400, {"detail": "Invalid request"})
            raw = self.rfile.read(length)
            if not valid_signature(raw, self.headers.get("X-Limit-Timestamp", ""),
                                   self.headers.get("X-Limit-Signature", "")):
                return self.reply(403, {"detail": "Forbidden"})
            return self.reply(200, handle(json.loads(raw)))
        except (ValueError, TypeError):
            return self.reply(400, {"detail": "Invalid request"})
        except Exception:
            return self.reply(503, {"detail": "Quota service unavailable"})

    def log_message(self, format, *args):
        pass
