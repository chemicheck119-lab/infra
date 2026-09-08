#!/usr/bin/env python3
"""Artifact Registry 삭제 후보를 읽기 전용으로 계산한다.

Cloud Run traffic revision, Cloud Run Job과 수동 보호 digest를 먼저 수집한 뒤,
지정 일수보다 오래된 비보호 image만 후보로 보고한다. cleanup policy를 설정하거나
tag·revision·image를 변경하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


SCHEMA_VERSION = "chemicheck119-artifact-cleanup-audit-v2"
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
INFRA_DIRECTORY = SCRIPT_DIRECTORY.parent
DEFAULT_CONFIG = INFRA_DIRECTORY / "config" / "artifact_cleanup_protected.json"
CommandRunner = Callable[[Sequence[str]], Any]


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


def _parse_time(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    if re.search(r"[+-]\d{4}$", normalized):
        normalized = f"{normalized[:-2]}:{normalized[-2:]}"
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _image_digest(reference: str) -> str | None:
    if "@sha256:" not in reference:
        return None
    return "sha256:" + reference.rsplit("@sha256:", 1)[1]


def _image_package(reference: str) -> str:
    without_digest = reference.split("@", 1)[0]
    final_slash = without_digest.rfind("/")
    final_colon = without_digest.rfind(":")
    if final_colon > final_slash:
        return without_digest[:final_colon]
    return without_digest


def _image_tag(reference: str) -> str | None:
    if "@" in reference:
        return None
    final_slash = reference.rfind("/")
    final_colon = reference.rfind(":")
    return reference[final_colon + 1 :] if final_colon > final_slash else None


def _load_manual_protection(path: Path) -> dict[str, list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "chemicheck119-artifact-protection-v1":
        raise ValueError("지원하지 않는 artifact protection schema입니다.")
    protected: dict[str, list[str]] = defaultdict(list)
    for row in payload.get("digests") or []:
        digest = str(row.get("digest") or "").strip()
        reason = str(row.get("reason") or "").strip()
        if not digest.startswith("sha256:") or len(digest) != 71 or not reason:
            raise ValueError("보호 digest 또는 reason 형식이 올바르지 않습니다.")
        protected[digest].append(f"MANUAL:{reason}")
    return protected


def _protect_reference(
    reference: str,
    reason: str,
    images: list[dict[str, Any]],
    protected: dict[str, list[str]],
) -> None:
    digest = _image_digest(reference)
    if digest:
        protected[digest].append(reason)
        return
    package = _image_package(reference)
    tag = _image_tag(reference)
    if not tag:
        return
    for image in images:
        if image.get("package") == package and tag in (image.get("tags") or []):
            protected[str(image["version"])].append(reason)


def _collect_runtime_protection(
    *,
    project: str,
    region: str,
    images: list[dict[str, Any]],
    runner: CommandRunner,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    protected: dict[str, list[str]] = defaultdict(list)
    zero_traffic_tags: list[dict[str, Any]] = []
    services = runner(
        [
            "run",
            "services",
            "list",
            f"--region={region}",
            f"--project={project}",
        ]
    )
    for service in services or []:
        service_name = str((service.get("metadata") or {}).get("name") or "")
        if not service_name:
            continue
        described = runner(
            [
                "run",
                "services",
                "describe",
                service_name,
                f"--region={region}",
                f"--project={project}",
            ]
        )
        for traffic in ((described.get("status") or {}).get("traffic") or []):
            revision = str(traffic.get("revisionName") or "")
            if not revision:
                continue
            tag = str(traffic.get("tag") or "")
            percent = int(traffic.get("percent") or 0)
            if tag and percent == 0:
                zero_traffic_tags.append(
                    {
                        "service": service_name,
                        "revision": revision,
                        "tag": tag,
                        "url": str(traffic.get("url") or ""),
                    }
                )
            revision_payload = runner(
                [
                    "run",
                    "revisions",
                    "describe",
                    revision,
                    f"--region={region}",
                    f"--project={project}",
                ]
            )
            image = str((revision_payload.get("status") or {}).get("imageDigest") or "")
            _protect_reference(
                image,
                f"CLOUD_RUN_TRAFFIC:{service_name}:{revision}",
                images,
                protected,
            )

    jobs = runner(
        ["run", "jobs", "list", f"--region={region}", f"--project={project}"]
    )
    for job in jobs or []:
        job_name = str((job.get("metadata") or {}).get("name") or "")
        if not job_name:
            continue
        described = runner(
            [
                "run",
                "jobs",
                "describe",
                job_name,
                f"--region={region}",
                f"--project={project}",
            ]
        )
        containers = (
            (((described.get("spec") or {}).get("template") or {}).get("spec") or {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        for container in containers:
            reference = str(container.get("image") or "")
            _protect_reference(
                reference,
                f"CLOUD_RUN_JOB:{job_name}",
                images,
                protected,
            )
    return protected, sorted(
        zero_traffic_tags,
        key=lambda row: (row["service"], row["tag"], row["revision"]),
    )


def _collect_build_bucket(
    *,
    project: str,
    bucket: str,
    now: datetime,
    older_than_days: int,
    runner: CommandRunner,
) -> dict[str, Any]:
    bucket_payload = runner(
        [
            "storage",
            "buckets",
            "describe",
            f"gs://{bucket}",
            f"--project={project}",
        ]
    )
    objects = runner(
        [
            "storage",
            "objects",
            "list",
            f"gs://{bucket}/**",
            f"--project={project}",
        ]
    )
    cutoff = now - timedelta(days=older_than_days)
    candidate_objects: list[dict[str, Any]] = []
    total_size_bytes = 0
    for row in sorted(objects or [], key=lambda item: str(item.get("name") or "")):
        created_raw = str(row.get("creation_time") or row.get("createTime") or "")
        if not created_raw:
            raise ValueError("Cloud Build object creation time이 비어 있습니다.")
        created_at = _parse_time(created_raw)
        size_bytes = int(row.get("size") or 0)
        total_size_bytes += size_bytes
        if created_at <= cutoff:
            candidate_objects.append(
                {
                    "name": str(row.get("name") or ""),
                    "created_at": created_at.isoformat().replace("+00:00", "Z"),
                    "size_bytes": size_bytes,
                }
            )

    soft_delete = bucket_payload.get("soft_delete_policy") or {}
    retention_seconds = int(soft_delete.get("retentionDurationSeconds") or 0)
    lifecycle = bucket_payload.get("lifecycle_config") or {}
    lifecycle_rules = lifecycle.get("rule") or []
    candidate_size_bytes = sum(row["size_bytes"] for row in candidate_objects)
    return {
        "bucket": bucket,
        "location": str(bucket_payload.get("location") or ""),
        "storage_class": str(bucket_payload.get("default_storage_class") or ""),
        "object_count": len(objects or []),
        "total_live_size_bytes": total_size_bytes,
        "lifecycle_rule_count": len(lifecycle_rules),
        "soft_delete_retention_seconds": retention_seconds,
        "simulated_lifecycle": {
            "older_than_days": older_than_days,
            "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
            "candidate_count": len(candidate_objects),
            "candidate_live_size_bytes": candidate_size_bytes,
            "candidate_bytes_subject_to_soft_delete_retention": (
                candidate_size_bytes if retention_seconds > 0 else 0
            ),
            "earliest_permanent_removal_delay_seconds": retention_seconds,
            "candidates": candidate_objects,
        },
    }


def _scan_zero_traffic_tag_urls(
    *, zero_traffic_tags: list[dict[str, Any]], source_roots: Sequence[Path]
) -> dict[str, Any]:
    roots: list[dict[str, str]] = []
    matches: list[dict[str, Any]] = []
    traffic_by_url = {
        str(traffic.get("url") or ""): traffic
        for traffic in zero_traffic_tags
        if traffic.get("url")
    }
    pattern_input = "\n".join(sorted(traffic_by_url)) + "\n"
    for root in source_roots:
        resolved = root.resolve()
        if not (resolved / ".git").exists():
            raise ValueError(f"Git source root가 아닙니다: {resolved}")
        roots.append({"name": resolved.name, "path": str(resolved)})
        if not traffic_by_url:
            continue
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(resolved),
                "grep",
                "--line-number",
                "--fixed-strings",
                "-f",
                "-",
                "--",
                ".",
            ],
            input=pattern_input,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode not in {0, 1}:
            raise RuntimeError(
                f"tracked URL 검색에 실패했습니다: {resolved.name}: "
                f"{completed.stderr.strip()}"
            )
        for line in completed.stdout.splitlines():
            file_name, line_number, content = line.split(":", 2)
            for url, traffic in traffic_by_url.items():
                if url not in content:
                    continue
                matches.append(
                    {
                        "source_root": resolved.name,
                        "file": file_name,
                        "line": int(line_number),
                        "service": traffic["service"],
                        "revision": traffic["revision"],
                        "tag": traffic["tag"],
                        "url": url,
                    }
                )
    return {
        "tracked_source_only": True,
        "source_roots": roots,
        "matched_reference_count": len(matches),
        "matches": sorted(
            matches,
            key=lambda row: (
                row["source_root"],
                row["file"],
                row["line"],
                row["url"],
            ),
        ),
    }


def _build_report(
    *,
    images: list[dict[str, Any]],
    protected: dict[str, list[str]],
    zero_traffic_tags: list[dict[str, Any]],
    now: datetime,
    older_than_days: int,
    repository_size_bytes: int | None,
    project: str,
    region: str,
    repository: str,
    config_path: Path,
    build_bucket: dict[str, Any] | None = None,
    zero_traffic_tag_reference_scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cutoff = now - timedelta(days=older_than_days)
    candidates: list[dict[str, Any]] = []
    protected_rows: list[dict[str, Any]] = []
    for image in sorted(
        images,
        key=lambda row: (str(row.get("package") or ""), str(row.get("createTime") or "")),
    ):
        digest = str(image.get("version") or "")
        created_at = _parse_time(str(image.get("createTime") or ""))
        size_bytes = int((image.get("metadata") or {}).get("imageSizeBytes") or 0)
        row = {
            "package": str(image.get("package") or "").rsplit("/", 1)[-1],
            "digest": digest,
            "tags": sorted(str(tag) for tag in (image.get("tags") or [])),
            "created_at": created_at.isoformat().replace("+00:00", "Z"),
            "manifest_size_bytes": size_bytes,
        }
        reasons = sorted(set(protected.get(digest) or []))
        if reasons:
            protected_rows.append({**row, "reasons": reasons})
        elif created_at <= cutoff:
            candidates.append(row)

    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": now.isoformat().replace("+00:00", "Z"),
        "read_only": True,
        "cleanup_policy_applied": False,
        "project": project,
        "region": region,
        "repository": repository,
        "older_than_days": older_than_days,
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "repository_size_bytes": repository_size_bytes,
        "image_count": len(images),
        "protected_image_count": len(protected_rows),
        "candidate_count": len(candidates),
        "candidate_manifest_size_bytes": sum(
            int(row["manifest_size_bytes"]) for row in candidates
        ),
        "zero_traffic_tag_count": len(zero_traffic_tags),
        "zero_traffic_tags": zero_traffic_tags,
        "zero_traffic_tag_reference_scan": zero_traffic_tag_reference_scan,
        "cloud_build_bucket": build_bucket,
        "candidates": candidates,
        "protected_images": protected_rows,
        "artifacts": {
            "protection_config": {
                "file_name": config_path.name,
                "sha256": _sha256(config_path),
            },
            "auditor_source": {
                "file_name": Path(__file__).name,
                "sha256": _sha256(Path(__file__)),
            },
        },
        "limitations": [
            "후보 목록은 실제 삭제나 Artifact Registry cleanup policy dry-run 적용 결과가 아닙니다.",
            "manifest 크기 합계는 공유 layer 중복 때문에 실제 회수 용량과 다를 수 있습니다.",
            "Cloud Run·Job과 수동 allowlist 밖의 외부 digest 참조는 자동 탐지하지 못합니다.",
            "tracked source URL 검색은 외부 문서·브라우저 북마크·수동 호출 의존성을 증명하지 못합니다.",
            "Cloud Build lifecycle 후보 크기는 soft-delete 보존 종료 전 즉시 절감되는 저장량이 아닙니다.",
            "tag·revision·image 삭제에는 별도 사용자 승인과 적용 후 smoke 검증이 필요합니다.",
        ],
    }


def _repository_size(
    *, project: str, region: str, repository: str, runner: CommandRunner
) -> int | None:
    rows = runner(
        [
            "artifacts",
            "repositories",
            "list",
            f"--project={project}",
            f"--location={region}",
        ]
    )
    suffix = f"/repositories/{repository}"
    for row in rows or []:
        if str(row.get("name") or "").endswith(suffix):
            value = row.get("sizeBytes")
            return int(value) if value is not None else None
    return None


def _write(payload: dict[str, Any], output: Path | None) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is None:
        sys.stdout.write(serialized)
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(serialized, encoding="utf-8")
    os.chmod(output, 0o600)


def _self_test() -> None:
    config = DEFAULT_CONFIG
    now = datetime(2026, 9, 7, tzinfo=timezone.utc)
    images = [
        {
            "package": "repo/be",
            "version": "sha256:" + "1" * 64,
            "createTime": "2026-07-01T00:00:00Z",
            "metadata": {"imageSizeBytes": "10"},
            "tags": ["old"],
        },
        {
            "package": "repo/be",
            "version": "sha256:" + "2" * 64,
            "createTime": "2026-07-01T00:00:00Z",
            "metadata": {"imageSizeBytes": "20"},
            "tags": ["serving"],
        },
        {
            "package": "repo/be",
            "version": "sha256:" + "3" * 64,
            "createTime": "2026-09-01T00:00:00Z",
            "metadata": {"imageSizeBytes": "30"},
            "tags": ["new"],
        },
    ]
    report = _build_report(
        images=images,
        protected={"sha256:" + "2" * 64: ["CLOUD_RUN_TRAFFIC:test"]},
        zero_traffic_tags=[],
        now=now,
        older_than_days=30,
        repository_size_bytes=60,
        project="test",
        region="test-region",
        repository="test-repo",
        config_path=config,
        build_bucket={
            "bucket": "test_cloudbuild",
            "object_count": 2,
            "total_live_size_bytes": 30,
        },
        zero_traffic_tag_reference_scan={
            "tracked_source_only": True,
            "matched_reference_count": 0,
        },
    )
    assert report["candidate_count"] == 1
    assert report["candidates"][0]["digest"] == "sha256:" + "1" * 64
    assert report["candidate_manifest_size_bytes"] == 10
    assert report["protected_image_count"] == 1
    assert report["cleanup_policy_applied"] is False
    assert report["cloud_build_bucket"]["total_live_size_bytes"] == 30
    assert report["zero_traffic_tag_reference_scan"]["matched_reference_count"] == 0

    build_bucket = _collect_build_bucket(
        project="test",
        bucket="test_cloudbuild",
        now=now,
        older_than_days=30,
        runner=lambda arguments: (
            {
                "location": "US",
                "default_storage_class": "STANDARD",
                "soft_delete_policy": {"retentionDurationSeconds": "604800"},
            }
            if arguments[1:3] == ["buckets", "describe"]
            else [
                {
                    "name": "source/old.tgz",
                    "creation_time": "2026-07-01T00:00:00+0000",
                    "size": 10,
                },
                {
                    "name": "source/new.tgz",
                    "creation_time": "2026-09-01T00:00:00+0000",
                    "size": 20,
                },
            ]
        ),
    )
    assert build_bucket["object_count"] == 2
    assert build_bucket["simulated_lifecycle"]["candidate_count"] == 1
    assert build_bucket["simulated_lifecycle"]["candidate_live_size_bytes"] == 10
    assert (
        build_bucket["simulated_lifecycle"]
        ["candidate_bytes_subject_to_soft_delete_retention"]
        == 10
    )
    print("Artifact cleanup audit self-test passed.")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default="chemi-check")
    parser.add_argument("--region", default="asia-northeast3")
    parser.add_argument("--repository", default="chemicheck119")
    parser.add_argument("--build-bucket")
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--protection-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-root", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.self_test:
        _self_test()
        return 0
    if args.older_than_days < 1:
        parser.error("--older-than-days는 1 이상이어야 합니다.")
    if _active_project() != args.project:
        raise RuntimeError(f"active gcloud project는 {args.project}여야 합니다.")

    repository_path = (
        f"{args.region}-docker.pkg.dev/{args.project}/{args.repository}"
    )
    images = _run_json(
        [
            "artifacts",
            "docker",
            "images",
            "list",
            repository_path,
            "--include-tags",
            f"--project={args.project}",
        ]
    )
    protected = _load_manual_protection(args.protection_config)
    runtime_protected, zero_traffic_tags = _collect_runtime_protection(
        project=args.project,
        region=args.region,
        images=images or [],
        runner=_run_json,
    )
    for digest, reasons in runtime_protected.items():
        protected[digest].extend(reasons)
    build_bucket_name = args.build_bucket or f"{args.project}_cloudbuild"
    observed_at = datetime.now(timezone.utc)
    build_bucket = _collect_build_bucket(
        project=args.project,
        bucket=build_bucket_name,
        now=observed_at,
        older_than_days=args.older_than_days,
        runner=_run_json,
    )
    tag_reference_scan = _scan_zero_traffic_tag_urls(
        zero_traffic_tags=zero_traffic_tags,
        source_roots=args.source_root,
    )
    report = _build_report(
        images=images or [],
        protected=protected,
        zero_traffic_tags=zero_traffic_tags,
        now=observed_at,
        older_than_days=args.older_than_days,
        repository_size_bytes=_repository_size(
            project=args.project,
            region=args.region,
            repository=args.repository,
            runner=_run_json,
        ),
        project=args.project,
        region=args.region,
        repository=args.repository,
        config_path=args.protection_config,
        build_bucket=build_bucket,
        zero_traffic_tag_reference_scan=tag_reference_scan,
    )
    _write(report, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
