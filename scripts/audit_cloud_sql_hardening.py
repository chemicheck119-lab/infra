#!/usr/bin/env python3
"""Cloud SQL TLS·storage hardening 적용 전 상태를 읽기 전용으로 감사한다.

Database URL Secret은 명시적으로 허용한 경우에만 메모리에서 읽는다. 원문 대신
SHA-256과 TLS 관련 boolean만 보고하며 Cloud SQL·Cloud Run·Secret을 수정하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib.parse import parse_qs, urlsplit


SCHEMA_VERSION = "chemicheck119-cloud-sql-hardening-audit-v1"
SCRIPT_PATH = Path(__file__).resolve()


def _run_json(arguments: Sequence[str]) -> Any:
    completed = subprocess.run(
        ["gcloud", *arguments, "--format=json"],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout or "null")


def _active_project() -> str:
    completed = subprocess.run(
        ["gcloud", "config", "get-value", "project"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _read_secret(project: str, secret: str, version: str) -> str:
    completed = subprocess.run(
        [
            "gcloud",
            "secrets",
            "versions",
            "access",
            version,
            f"--secret={secret}",
            f"--project={project}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _sha256_bytes(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _database_secret_reference(service: dict[str, Any]) -> dict[str, Any]:
    template = (service.get("spec") or {}).get("template") or {}
    containers = (template.get("spec") or {}).get("containers", [])
    if len(containers) != 1:
        raise ValueError("Backend Cloud Run container는 정확히 하나여야 합니다.")
    matches = [
        row
        for row in containers[0].get("env") or []
        if row.get("name") == "CHEMICHECK119_DATABASE_URL"
    ]
    if len(matches) != 1:
        raise ValueError("Backend Database URL 환경변수는 정확히 하나여야 합니다.")
    row = matches[0]
    reference = (row.get("valueFrom") or {}).get("secretKeyRef") or {}
    secret = str(reference.get("name") or "")
    version = str(reference.get("key") or "")
    return {
        "secret": secret,
        "version": version,
        "secret_backed": bool(secret),
        "version_pinned": bool(re.fullmatch(r"[1-9][0-9]*", version)),
        "direct_value_present": "value" in row,
    }


def _host_classification(host: str) -> str:
    if not host:
        return "MISSING"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return "HOSTNAME"
    if address.is_private:
        return "PRIVATE_IP"
    return "PUBLIC_IP"


def _database_url_summary(raw_secret: str) -> dict[str, Any]:
    value = raw_secret.strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError(
            "Database URL Secret은 비어 있지 않은 단일 행이어야 합니다."
        )
    jdbc_postgresql = value.startswith("jdbc:postgresql://")
    parsed = urlsplit(value[len("jdbc:") :] if value.startswith("jdbc:") else "")
    query = parse_qs(parsed.query, keep_blank_values=True)
    sslmode = str((query.get("sslmode") or [""])[-1]).lower() or None
    allowed_sslmodes = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}
    if sslmode is not None and sslmode not in allowed_sslmodes:
        raise ValueError("지원하지 않는 pgJDBC sslmode입니다.")
    explicit_encryption_required = sslmode in {"require", "verify-ca", "verify-full"}
    server_identity_verified = sslmode == "verify-full"
    return {
        "value_sha256": _sha256_bytes(value),
        "single_line": True,
        "jdbc_postgresql": jdbc_postgresql,
        "host_classification": _host_classification(parsed.hostname or ""),
        "database_name_present": bool(parsed.path.strip("/")),
        "credentials_in_url": parsed.username is not None or parsed.password is not None,
        "sslmode": sslmode,
        "sslmode_explicit": sslmode is not None,
        "explicit_encryption_required": explicit_encryption_required,
        "server_identity_verified": server_identity_verified,
        "sslrootcert_configured": bool((query.get("sslrootcert") or [""])[-1]),
        "raw_value_exposed": False,
    }


def _resolve_backend_runtime(backend_root: Path) -> dict[str, str]:
    resolved = backend_root.resolve()
    if not (resolved / ".git").exists() or not (resolved / "gradlew").exists():
        raise ValueError("유효한 Backend repository가 아닙니다.")
    commit = subprocess.run(
        ["git", "-C", str(resolved), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    completed = subprocess.run(
        [
            str(resolved / "gradlew"),
            "dependencyInsight",
            "--dependency",
            "org.postgresql:postgresql",
            "--configuration",
            "runtimeClasspath",
            "--no-daemon",
            "--console=plain",
        ],
        cwd=resolved,
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"org\.postgresql:postgresql:([0-9][0-9A-Za-z._-]*)", completed.stdout)
    if not match:
        raise ValueError("Backend runtimeClasspath에서 pgJDBC version을 찾지 못했습니다.")
    return {
        "repository": resolved.name,
        "commit": commit,
        "pgjdbc_version": match.group(1),
        "resolution_command": (
            "./gradlew dependencyInsight --dependency org.postgresql:postgresql "
            "--configuration runtimeClasspath"
        ),
    }


def _build_report(
    *,
    observed_at: datetime,
    project: str,
    region: str,
    instance_name: str,
    service_name: str,
    instance: dict[str, Any],
    service: dict[str, Any],
    secret_state: dict[str, Any],
    url_summary: dict[str, Any] | None,
    backend_runtime: dict[str, str],
) -> dict[str, Any]:
    settings = instance.get("settings") or {}
    ip_configuration = settings.get("ipConfiguration") or {}
    backup = settings.get("backupConfiguration") or {}
    reference = _database_secret_reference(service)
    secret_enabled = str(secret_state.get("state") or "").upper() == "ENABLED"
    service_revision = str((service.get("status") or {}).get("latestReadyRevisionName") or "")
    url_checked = url_summary is not None
    explicit_tls = bool(url_summary and url_summary["explicit_encryption_required"])
    secret_contract_ready = (
        reference["secret_backed"]
        and reference["version_pinned"]
        and not reference["direct_value_present"]
        and secret_enabled
    )
    client_preflight_ready = secret_contract_ready and url_checked and explicit_tls
    storage_limit = int(settings.get("storageAutoResizeLimit") or 0)
    ssl_mode = str(ip_configuration.get("sslMode") or "")
    blockers: list[str] = []
    if not client_preflight_ready:
        blockers.append(
            "Database URL에 명시적 sslmode=require 이상을 적용한 새 Secret·candidate "
            "smoke가 필요합니다."
        )
    if ssl_mode != "ENCRYPTED_ONLY":
        blockers.append("Cloud SQL server가 아직 암호화 연결만 강제하지 않습니다.")
    if storage_limit == 0:
        blockers.append(
            "disk 사용량·증가율 근거가 없어 storage auto-resize 상한을 결정하지 "
            "못했습니다."
        )
    blockers.append("Cloud SQL 변경과 traffic 전환 전 사용자 승인이 필요합니다.")
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "gcp_mutation_performed": False,
        "secret_value_exposed": False,
        "project": project,
        "region": region,
        "instance": instance_name,
        "backend_service": service_name,
        "backend_revision": service_revision,
        "cloud_sql": {
            "state": str(instance.get("state") or ""),
            "database_version": str(instance.get("databaseVersion") or ""),
            "tier": str(settings.get("tier") or ""),
            "availability_type": str(settings.get("availabilityType") or ""),
            "activation_policy": str(settings.get("activationPolicy") or ""),
            "public_ipv4_enabled": bool(ip_configuration.get("ipv4Enabled")),
            "private_network_configured": bool(ip_configuration.get("privateNetwork")),
            "ssl_mode": ssl_mode,
            "require_ssl_legacy": bool(ip_configuration.get("requireSsl")),
            "server_ca_mode": str(ip_configuration.get("serverCaMode") or ""),
            "storage_gb": int(settings.get("dataDiskSizeGb") or 0),
            "storage_auto_resize": bool(settings.get("storageAutoResize")),
            "storage_auto_resize_limit_gb": storage_limit,
            "backup_enabled": bool(backup.get("enabled")),
            "pitr_enabled": bool(backup.get("pointInTimeRecoveryEnabled")),
            "deletion_protection_enabled": bool(settings.get("deletionProtectionEnabled")),
        },
        "database_url_secret": {
            **reference,
            "enabled": secret_enabled,
            "safe_url_summary": url_summary,
        },
        "backend_runtime": backend_runtime,
        "gates": {
            "secret_contract_ready": secret_contract_ready,
            "database_url_inspected": url_checked,
            "client_explicit_tls_preflight_ready": client_preflight_ready,
            "server_tls_enforced": ssl_mode == "ENCRYPTED_ONLY",
            "candidate_backend_smoke_executed": False,
            "storage_limit_evidence_available": False,
            "user_change_approval_recorded": False,
            "safe_to_apply": False,
        },
        "decision": "BLOCK_CHANGE_PENDING_TLS_CANDIDATE_AND_STORAGE_EVIDENCE",
        "blockers": blockers,
        "storage_limit_recommendation_gb": None,
        "fact_status": "부분 구현 또는 개발용 데모",
        "claim_scope": [
            "현재 Cloud SQL·Cloud Run·Secret metadata와 Database URL TLS 옵션의 "
            "읽기 전용 preflight",
            "로컬에서 해결된 Backend pgJDBC version",
        ],
        "not_claimed": [
            "현재 연결이 실제로 TLS인지 여부",
            "ENCRYPTED_ONLY 전환 뒤 Backend 연결·Flyway·readiness 성공",
            "storage 상한의 적정성",
            "고가용성·재해복구·상용 운영 검증",
        ],
        "artifacts": {
            "auditor_source": {
                "file_name": SCRIPT_PATH.name,
                "sha256": _sha256_file(SCRIPT_PATH),
            }
        },
    }


def _write(payload: dict[str, Any], output: Path | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(serialized)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized, encoding="utf-8")
    os.chmod(output, 0o600)


def _self_test() -> None:
    instance = {
        "state": "RUNNABLE",
        "databaseVersion": "POSTGRES_16",
        "settings": {
            "tier": "db-f1-micro",
            "availabilityType": "ZONAL",
            "activationPolicy": "ALWAYS",
            "dataDiskSizeGb": "10",
            "storageAutoResize": True,
            "storageAutoResizeLimit": "0",
            "deletionProtectionEnabled": True,
            "ipConfiguration": {
                "ipv4Enabled": False,
                "privateNetwork": "redacted-private-network",
                "sslMode": "ALLOW_UNENCRYPTED_AND_ENCRYPTED",
                "requireSsl": False,
                "serverCaMode": "GOOGLE_MANAGED_INTERNAL_CA",
            },
            "backupConfiguration": {
                "enabled": True,
                "pointInTimeRecoveryEnabled": True,
            },
        },
    }
    service = {
        "status": {"latestReadyRevisionName": "backend-test-r1"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "env": [
                                {
                                    "name": "CHEMICHECK119_DATABASE_URL",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": "db-url", "key": "1"}
                                    },
                                }
                            ]
                        }
                    ]
                }
            }
        },
    }
    secret_value = "jdbc:postgresql://10.0.0.3:5432/db?sslmode=require"
    summary = _database_url_summary(secret_value)
    assert summary["explicit_encryption_required"] is True
    assert summary["host_classification"] == "PRIVATE_IP"
    implicit_summary = _database_url_summary(
        "jdbc:postgresql://10.0.0.3:5432/db"
    )
    assert implicit_summary["sslmode"] is None
    assert implicit_summary["explicit_encryption_required"] is False
    report = _build_report(
        observed_at=datetime(2026, 9, 8, tzinfo=timezone.utc),
        project="test",
        region="test-region",
        instance_name="test-db",
        service_name="test-backend",
        instance=instance,
        service=service,
        secret_state={"state": "ENABLED"},
        url_summary=summary,
        backend_runtime={
            "repository": "back",
            "commit": "1" * 40,
            "pgjdbc_version": "42.7.3",
            "resolution_command": "test",
        },
    )
    serialized = json.dumps(report)
    assert secret_value not in serialized
    assert "10.0.0.3" not in serialized
    assert report["gates"]["client_explicit_tls_preflight_ready"] is True
    assert report["gates"]["safe_to_apply"] is False
    assert report["storage_limit_recommendation_gb"] is None
    print("Cloud SQL hardening audit self-test passed.")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="chemi-check")
    parser.add_argument("--region", default="asia-northeast3")
    parser.add_argument("--instance", default="chemicheck119-pg-staging")
    parser.add_argument("--backend-service", default="chemicheck119-be-staging")
    parser.add_argument("--backend-root", type=Path, default=Path("../back"))
    parser.add_argument("--inspect-database-url-secret", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.self_test:
        _self_test()
        return 0
    if _active_project() != args.project:
        raise RuntimeError(f"active gcloud project는 {args.project}여야 합니다.")

    instance = _run_json(
        ["sql", "instances", "describe", args.instance, f"--project={args.project}"]
    )
    service = _run_json(
        [
            "run",
            "services",
            "describe",
            args.backend_service,
            f"--region={args.region}",
            f"--project={args.project}",
        ]
    )
    reference = _database_secret_reference(service)
    if not reference["secret"] or not reference["version"]:
        raise RuntimeError("Backend Database URL의 고정 Secret 참조가 필요합니다.")
    secret_state = _run_json(
        [
            "secrets",
            "versions",
            "describe",
            reference["version"],
            f"--secret={reference['secret']}",
            f"--project={args.project}",
        ]
    )
    url_summary = None
    if args.inspect_database_url_secret:
        raw_secret = _read_secret(
            args.project,
            reference["secret"],
            reference["version"],
        )
        url_summary = _database_url_summary(raw_secret)
        raw_secret = ""
    report = _build_report(
        observed_at=datetime.now(timezone.utc),
        project=args.project,
        region=args.region,
        instance_name=args.instance,
        service_name=args.backend_service,
        instance=instance,
        service=service,
        secret_state=secret_state,
        url_summary=url_summary,
        backend_runtime=_resolve_backend_runtime(args.backend_root),
    )
    _write(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
