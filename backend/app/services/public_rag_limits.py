"""Durable request-attempt limits; no model, prompts or raw IPs are stored."""

import math
import re
from datetime import datetime, timezone

from app.services.public_study_store import PublicStudyStore


class PublicRagLimits(PublicStudyStore):
    def __init__(self, dsn):
        super().__init__(dsn, "0" * 64, "0" * 64)

    def quota(self, quota_key, consume=False, *, now=None):
        if not isinstance(quota_key, str) or not re.fullmatch(r"[a-f0-9]{64}", quota_key):
            raise ValueError("Invalid quota key")
        now = now or datetime.now(timezone.utc)
        day = now.date().isoformat()
        month = now.replace(day=1).date().isoformat()
        ip_key, global_key = "rag:ip:" + quota_key, "rag:global"
        with self.connect() as db:
            # Same short transaction lock across serverless instances; no model
            # work in this transaction and no in-memory fallback on failure.
            db.execute("BEGIN IMMEDIATE")
            counts = []
            for key, period in ((ip_key, day), (global_key, month)):
                row = db.execute("SELECT count FROM public_demo_limits WHERE key=? AND day=?",
                                 (key, period)).fetchone()
                counts.append(row["count"] if row else 0)
            allowed = counts[0] < 5 and counts[1] < 300
            reason = "monthly" if counts[1] >= 300 else "daily" if counts[0] >= 5 else ""
            if allowed and consume:
                for key, period in ((ip_key, day), (global_key, month)):
                    db.execute("INSERT INTO public_demo_limits (key, day, count) VALUES (?, ?, 1) "
                               "ON CONFLICT (key, day) DO UPDATE "
                               "SET count=public_demo_limits.count+1", (key, period))
                counts = [value + 1 for value in counts]
        next_day = datetime.fromtimestamp(now.timestamp() + 86400, timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0)
        next_month = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1,
                              tzinfo=timezone.utc)
        retry_at = next_month if reason == "monthly" else next_day if reason else None
        return {"allowed": allowed, "reason": reason,
                "remaining_today": max(0, 5 - counts[0]),
                "remaining_month": max(0, 300 - counts[1]),
                "retry_at": retry_at.isoformat() if retry_at else None,
                "retry_after_seconds": max(1, math.ceil((retry_at - now).total_seconds()))
                if retry_at else 0}
