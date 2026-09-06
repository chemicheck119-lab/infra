#!/usr/bin/env python3
"""Cloud Run Speech API의 정적 IAM·scale·Secret 경계를 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


SCHEMA_VERSION = "chemicheck119-private-speech-cloud-run-audit-v2"
MAX_INPUT_BYTES = 1024 * 1024
EXPECTED_CONTAINER_CONCURRENCY = 4


def read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"invalid bounded JSON input: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return payload


def build_report(
    service: dict[str, Any],
    iam: dict[str, Any],
    backend_service_account: str,
    runtime_service_account: str,
) -> dict[str, Any]:
    expected_backend = "serviceAccount:" + backend_service_account
    template = service.get("spec", {}).get("template", {})
    annotations = template.get("metadata", {}).get("annotations", {})
    spec = template.get("spec", {})
    containers = spec.get("containers", [])
    container = containers[0] if isinstance(containers, list) and containers else {}
    env = container.get("env", [])
    env_by_name = {
        item.get("name"): item
        for item in env
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    invokers = sorted(
        {
            member
            for binding in iam.get("bindings", [])
            if binding.get("role") == "roles/run.invoker"
            for member in binding.get("members", [])
        }
    )
    image = container.get("image", "")
    api_key = env_by_name.get("CHEMICHECK119_SPEECH_API_KEY", {})
    anonymous = env_by_name.get("CHEMICHECK119_SPEECH_ALLOW_ANONYMOUS", {})
    local_only = env_by_name.get("CHEMICHECK119_SPEECH_LOCAL_FILES_ONLY", {})
    checks = {
        "immutable_image_digest": "@sha256:" in image,
        "runtime_service_account": spec.get("serviceAccountName")
        == runtime_service_account,
        "bounded_request_concurrency": spec.get("containerConcurrency")
        == EXPECTED_CONTAINER_CONCURRENCY,
        "max_instance_one": annotations.get("autoscaling.knative.dev/maxScale") == "1",
        "min_instance_zero": annotations.get(
            "autoscaling.knative.dev/minScale", "0"
        )
        == "0",
        "anonymous_disabled": "allUsers" not in invokers
        and "allAuthenticatedUsers" not in invokers,
        "backend_invoker_present": expected_backend in invokers,
        "only_backend_invoker": invokers == [expected_backend],
        "api_key_secret_reference": bool(api_key.get("valueFrom", {}).get("secretKeyRef")),
        "anonymous_env_disabled": anonymous.get("value") == "false",
        "runtime_download_disabled": local_only.get("value") == "true",
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "fact_status": "부분 구현 또는 개발용 데모",
        "service": service.get("metadata", {}).get("name"),
        "revision": service.get("status", {}).get("latestReadyRevisionName"),
        "url": service.get("status", {}).get("url"),
        "image_digest_uri": image,
        "runtime_service_account": spec.get("serviceAccountName"),
        "invokers": invokers,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "claim_limits": [
            "IAM과 정적 배포 경계만 확인",
            "실제 WAV 추론, cold/warm latency, 부하, 현장 무전 정확도는 별도 검증 필요",
        ],
    }


def self_test() -> None:
    runtime = "speech@example.iam.gserviceaccount.com"
    backend = "back@example.iam.gserviceaccount.com"
    service = {
        "metadata": {"name": "speech"},
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"autoscaling.knative.dev/maxScale": "1"}
                },
                "spec": {
                    "serviceAccountName": runtime,
                    "containerConcurrency": EXPECTED_CONTAINER_CONCURRENCY,
                    "containers": [
                        {
                            "image": "region.pkg.dev/project/repo/speech@sha256:"
                            + "a" * 64,
                            "env": [
                                {
                                    "name": "CHEMICHECK119_SPEECH_API_KEY",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": "speech-key", "key": "1"}
                                    },
                                },
                                {
                                    "name": "CHEMICHECK119_SPEECH_ALLOW_ANONYMOUS",
                                    "value": "false",
                                },
                                {
                                    "name": "CHEMICHECK119_SPEECH_LOCAL_FILES_ONLY",
                                    "value": "true",
                                },
                            ],
                        }
                    ],
                },
            }
        },
    }
    iam = {
        "bindings": [
            {
                "role": "roles/run.invoker",
                "members": ["serviceAccount:" + backend],
            }
        ]
    }
    report = build_report(service, iam, backend, runtime)
    if not report["all_checks_passed"]:
        raise AssertionError(report["checks"])
    iam["bindings"][0]["members"].append("allUsers")
    unsafe = build_report(service, iam, backend, runtime)
    if (
        unsafe["all_checks_passed"]
        or unsafe["checks"]["anonymous_disabled"]
        or unsafe["checks"]["only_backend_invoker"]
    ):
        raise AssertionError("public invoker must fail closed")
    print("Private Speech API audit self-test passed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service-json", type=Path)
    parser.add_argument("--iam-json", type=Path)
    parser.add_argument("--backend-service-account")
    parser.add_argument("--runtime-service-account")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (
        args.service_json,
        args.iam_json,
        args.backend_service_account,
        args.runtime_service_account,
        args.output,
    )
    if any(value is None for value in required):
        parser.error("all input and output arguments are required")
    report = build_report(
        read_json(args.service_json),
        read_json(args.iam_json),
        args.backend_service_account,
        args.runtime_service_account,
    )
    if not report["all_checks_passed"]:
        raise SystemExit(
            "private Speech API boundary audit failed: "
            + json.dumps(report["checks"], sort_keys=True)
        )
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    args.output.write_bytes(payload)
    print(hashlib.sha256(payload).hexdigest())


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
