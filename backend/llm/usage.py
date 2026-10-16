"""Token/cost ledger (data/usage.jsonl), spend caps, and per-backend request pacing.

Caps come from the environment and are checked before every paid call:
  S2M_MAX_USD_PER_JOB    (default 1.00)   spend attributed to the current job
  S2M_MAX_USD_PER_MONTH  (default 5.00)   calendar-month spend across everything
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from backend.paths import DATA_ROOT

LEDGER = DATA_ROOT / "usage.jsonl"
_lock = threading.Lock()
_job: str | None = None                      # set by the worker / CLI around one conversion
_job_spend: dict[str, float] = defaultdict(float)
_month: tuple[str, float] | None = None      # (YYYY-MM, spend) — loaded lazily from the ledger


class BudgetExceeded(RuntimeError):
    """A spend cap was hit. Never retried, never failed over: the job stops."""


def set_job(job_id: str | None) -> None:
    global _job
    _job = job_id


def current_job() -> str | None:
    return _job


def _cap(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _this_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _month_spend_locked() -> float:
    global _month
    month = _this_month()
    if _month is None or _month[0] != month:
        total = 0.0
        if LEDGER.exists():
            for line in LEDGER.read_text().splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("ts", "").startswith(month):
                    total += row.get("usd", 0.0)
        _month = (month, total)
    return _month[1]


def month_spend() -> float:
    with _lock:
        return _month_spend_locked()


def job_spend(job_id: str | None = None) -> float:
    with _lock:
        return _job_spend[job_id or _job or ""]


def check_budget(paid: bool, job: str | None = None) -> None:
    """Raise BudgetExceeded if a paid call now would go over a cap. Free/local backends always pass."""
    if not paid:
        return
    job = job or _job
    with _lock:
        month = _month_spend_locked()
        spent = _job_spend[job or ""]
    if month >= _cap("S2M_MAX_USD_PER_MONTH", 5.0):
        raise BudgetExceeded(f"monthly spend cap reached (${month:.2f}); raise S2M_MAX_USD_PER_MONTH to continue")
    if job and spent >= _cap("S2M_MAX_USD_PER_JOB", 1.0):
        raise BudgetExceeded(f"per-job spend cap reached (${spent:.2f}); raise S2M_MAX_USD_PER_JOB to continue")


def record(*, backend: str, model: str, stage: str | None, prompt_tokens: int, completion_tokens: int,
           usd: float, job: str | None = None) -> None:
    global _month
    job = job or _job
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "job": job, "stage": stage,
           "backend": backend, "model": model, "in": prompt_tokens, "out": completion_tokens,
           "usd": round(usd, 6)}
    with _lock:
        month = _month_spend_locked()
        _month = (_this_month(), month + usd)
        _job_spend[job or ""] += usd
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a") as f:
            f.write(json.dumps(row) + "\n")


def cost(price: list[float] | None, prompt_tokens: int, completion_tokens: int) -> float:
    """USD from a `price: [in, out]` ($ per 1M tokens) entry in backends.yaml."""
    if not price:
        return 0.0
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1e6


def summary(month: str | None = None) -> dict:
    """Totals for one month, by backend and by job (for `s2m usage`)."""
    month = month or _this_month()
    by_backend: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "usd": 0.0})
    by_job: dict[str, float] = defaultdict(float)
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not row.get("ts", "").startswith(month):
                continue
            b = by_backend[row["backend"]]
            b["calls"] += 1
            b["in"] += row.get("in", 0)
            b["out"] += row.get("out", 0)
            b["usd"] += row.get("usd", 0.0)
            by_job[row.get("job") or "-"] += row.get("usd", 0.0)
    return {"month": month, "backends": dict(by_backend), "jobs": dict(by_job),
            "total": sum(b["usd"] for b in by_backend.values())}


class RateLimiter:
    """Sliding-window limiter: at most `rpm` request starts per 60 s for one backend."""

    _registry: dict[str, "RateLimiter"] = {}
    _registry_lock = threading.Lock()

    def __init__(self, rpm: int):
        self.rpm = rpm
        self.starts: deque[float] = deque()
        self.lock = threading.Lock()

    @classmethod
    def for_backend(cls, name: str, rpm: int | None) -> "RateLimiter | None":
        if not rpm:
            return None
        with cls._registry_lock:
            if name not in cls._registry:
                cls._registry[name] = cls(rpm)
            return cls._registry[name]

    def wait(self) -> None:
        while True:
            with self.lock:
                now = time.monotonic()
                while self.starts and now - self.starts[0] >= 60:
                    self.starts.popleft()
                if len(self.starts) < self.rpm:
                    self.starts.append(now)
                    return
                delay = 60 - (now - self.starts[0]) + 0.05
            time.sleep(delay)
