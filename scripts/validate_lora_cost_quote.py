#!/usr/bin/env python3
"""Reject a stale or over-budget LoRA quote before creating a billable VM."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys


EXPERIMENT_HARD_CAP_KRW = 20_000
TOTAL_DEVELOPMENT_CAP_KRW = 70_000
TRACKED_PRIOR_CEILING_KRW = 50_000
COMPUTE_CEILING_USD_PER_HOUR = 1.0
BOOT_DISK_CEILING_USD = 1.0
NETWORK_TRANSFER_CEILING_USD = 0.25
FX_CEILING_KRW_PER_USD = 1_700
CONTINGENCY_FRACTION = 0.25


def number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate(
    path: Path,
    speech_revision: str,
    now: datetime | None = None,
) -> dict[str, object]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 64 * 1024:
        raise ValueError("cost quote must be a bounded regular non-symlink file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("cost quote must be an object")
    if (
        payload.get("schema_version") != "1.0.0"
        or payload.get("protocol_id") != "whisper-small-lora-cost-quote-v1"
        or payload.get("currency") != "USD"
    ):
        raise ValueError("cost quote identity does not match")
    authorization = payload.get("authorization")
    if not isinstance(authorization, dict):
        raise ValueError("cost authorization must be an object")
    authorization_id = authorization.get("id")
    cumulative_before = authorization.get("cumulative_development_cost_before_krw")
    if (
        not isinstance(authorization_id, str)
        or re.fullmatch(r"[a-z0-9][a-z0-9-]{7,63}", authorization_id) is None
        or re.fullmatch(r"[0-9a-f]{40}", speech_revision) is None
        or authorization.get("speech_revision") != speech_revision
        or authorization.get("authorized_run_count") != 1
        or authorization.get("remote_claim_required") is not True
        or not isinstance(cumulative_before, int)
        or isinstance(cumulative_before, bool)
        or cumulative_before < TRACKED_PRIOR_CEILING_KRW
    ):
        raise ValueError("cost authorization does not match the single-use run")
    generated = timestamp(payload.get("generated_at"), "generated_at")
    expires = timestamp(payload.get("expires_at"), "expires_at")
    observed_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if expires <= generated or (expires - generated).total_seconds() > 86_400:
        raise ValueError("cost quote validity window exceeds 24 hours")
    if observed_now < generated or observed_now >= expires:
        raise ValueError("cost quote is not current")

    resource = payload.get("resource")
    expected_resource = {
        "gcp_region": "asia-northeast1",
        "machine_type": "n1-standard-4",
        "gpu_type": "nvidia-tesla-t4",
        "gpu_count": 1,
        "vcpu_count": 4,
        "memory_gib": 15,
        "boot_disk_gib": 100,
        "runtime_hours": 3.0,
    }
    if resource != expected_resource:
        raise ValueError("cost quote resource does not match")
    pricing = payload.get("pricing")
    if not isinstance(pricing, dict):
        raise ValueError("cost quote pricing must be an object")
    compute_hour = (
        number(pricing.get("gpu_usd_per_hour"), "GPU price")
        + 4 * number(pricing.get("vcpu_usd_per_hour"), "vCPU price")
        + 15 * number(pricing.get("memory_gib_usd_per_hour"), "memory price")
    )
    boot_total = (
        number(pricing.get("boot_disk_usd_per_gib_month"), "disk price")
        * 100
        * 3
        / number(pricing.get("month_hours"), "month hours")
    )
    network_transfer = number(
        pricing.get("network_transfer_usd"), "network transfer price"
    )
    fx = number(payload.get("fx_krw_per_usd"), "FX rate")
    quoted_krw = math.ceil(
        (compute_hour * 3 + boot_total + network_transfer)
        * fx
        * (1 + CONTINGENCY_FRACTION)
    )
    if compute_hour > COMPUTE_CEILING_USD_PER_HOUR:
        raise ValueError("compute quote exceeds the registered ceiling")
    if boot_total > BOOT_DISK_CEILING_USD:
        raise ValueError("disk quote exceeds the registered ceiling")
    if network_transfer > NETWORK_TRANSFER_CEILING_USD:
        raise ValueError("network transfer quote exceeds the registered ceiling")
    if fx > FX_CEILING_KRW_PER_USD or quoted_krw > EXPERIMENT_HARD_CAP_KRW:
        raise ValueError("quote exceeds the registered KRW ceiling")
    independent_ceiling = math.ceil(
        (
            COMPUTE_CEILING_USD_PER_HOUR * 3
            + BOOT_DISK_CEILING_USD
            + NETWORK_TRANSFER_CEILING_USD
        )
        * FX_CEILING_KRW_PER_USD
        * (1 + CONTINGENCY_FRACTION)
    )
    independent_total = cumulative_before + independent_ceiling
    if independent_total > TOTAL_DEVELOPMENT_CAP_KRW:
        raise ValueError("independent total ceiling exceeds the development cap")
    return {
        "status": "accepted",
        "authorization_id": authorization_id,
        "quoted_total_krw_with_contingency": quoted_krw,
        "independent_experiment_ceiling_krw": independent_ceiling,
        "independent_total_ceiling_krw": independent_total,
    }


def main() -> int:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(
            "usage: validate_lora_cost_quote.py COST_QUOTE_JSON SPEECH_COMMIT_SHA "
            "[--authorization-id]"
        )
    result = validate(Path(sys.argv[1]), sys.argv[2])
    if len(sys.argv) == 4:
        if sys.argv[3] != "--authorization-id":
            raise SystemExit("unsupported output option")
        print(result["authorization_id"])
    else:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
