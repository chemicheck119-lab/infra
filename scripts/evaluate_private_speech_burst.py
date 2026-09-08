#!/usr/bin/env python3
"""Private Speech API의 bounded burst 분포를 민감정보 없이 평가한다."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import subprocess
import sys
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import wave


SCHEMA_VERSION = "chemicheck119-private-speech-platform-burst-v1"
USER_AGENT = "chemicheck119-burst-evaluator/1.0"
EXPECTED_SCHEMA = "chemicheck119-speech-api-v1"
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 60.0
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_BATCHES = 10
MAX_REQUESTS_PER_BATCH = 10
MAX_TOTAL_AUDIO_SECONDS = 3_600.0
EXPECTED_CONTAINER_CONCURRENCY = 4
EXPECTED_MAX_INSTANCES = "1"
ALLOWED_HTTP_STATUSES = frozenset({200, 429})
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
RESOURCE_LOG_FIELDS = frozenset(
    {
        "event",
        "request_id",
        "processing_seconds",
        "audio_seconds",
        "resource_observation_available",
        "resource_observation_error_type",
        "cgroup_version",
        "cgroup_memory_current_bytes",
        "cgroup_memory_peak_bytes",
        "cgroup_memory_limit_bytes",
        "process_current_rss_bytes",
        "process_max_rss_bytes",
    }
)
RESOURCE_BYTE_FIELDS = (
    "cgroup_memory_current_bytes",
    "cgroup_memory_peak_bytes",
    "cgroup_memory_limit_bytes",
    "process_current_rss_bytes",
    "process_max_rss_bytes",
)


class EvaluationError(RuntimeError):
    """민감한 응답 본문이나 인증값을 포함하지 않는 평가 오류."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_gcloud(arguments: list[str], *, sensitive: bool = False) -> str:
    completed = subprocess.run(
        ["gcloud", *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    if completed.returncode != 0:
        command = arguments[0] if arguments else "unknown"
        raise EvaluationError(f"gcloud {command} failed")
    value = completed.stdout.strip()
    if not value:
        label = "sensitive value" if sensitive else "output"
        raise EvaluationError(f"gcloud returned an empty {label}")
    return value


def bounded_json(raw: bytes) -> dict[str, Any] | None:
    if len(raw) > MAX_RESPONSE_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_audio_metadata(path: Path) -> tuple[bytes, dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        raise EvaluationError("audio input must be a regular non-symlink file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_AUDIO_BYTES:
        raise EvaluationError("audio input exceeds the bounded size")
    try:
        with wave.open(str(path), "rb") as audio:
            channels = audio.getnchannels()
            sample_width = audio.getsampwidth()
            sample_rate = audio.getframerate()
            frames = audio.getnframes()
            compression = audio.getcomptype()
    except (wave.Error, EOFError) as error:
        raise EvaluationError("audio input is not a valid PCM WAV") from error
    duration = frames / sample_rate if sample_rate else 0.0
    if (
        channels not in {1, 2}
        or sample_width != 2
        or sample_rate <= 0
        or compression != "NONE"
        or not math.isfinite(duration)
        or duration <= 0
        or duration > MAX_AUDIO_SECONDS
    ):
        raise EvaluationError("audio input violates the Speech API WAV boundary")
    content = path.read_bytes()
    return content, {
        "classification": "공개 합성 음성의 로컬 파생 입력",
        "sha256": hashlib.sha256(content).hexdigest(),
        "sizeBytes": size,
        "channels": channels,
        "sampleWidthBits": sample_width * 8,
        "sampleRateHz": sample_rate,
        "durationSeconds": duration,
        "audioOrTranscriptIncludedInReport": False,
    }


def service_snapshot(service: dict[str, Any]) -> dict[str, Any]:
    template = service.get("spec", {}).get("template", {})
    template_metadata = template.get("metadata", {})
    spec = template.get("spec", {})
    containers = spec.get("containers", [])
    container = containers[0] if isinstance(containers, list) and containers else {}
    env = container.get("env", [])
    env_by_name = {
        item.get("name"): item
        for item in env
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    annotations = template_metadata.get("annotations", {})
    resources = container.get("resources", {}).get("limits", {})
    image = str(container.get("image", ""))
    api_key_ref = (
        env_by_name.get("CHEMICHECK119_SPEECH_API_KEY", {})
        .get("valueFrom", {})
        .get("secretKeyRef", {})
    )

    def env_value(name: str) -> str | None:
        value = env_by_name.get(name, {}).get("value")
        return value if isinstance(value, str) else None

    checks = {
        "immutableImageDigest": "@sha256:" in image,
        "maxInstanceOne": annotations.get("autoscaling.knative.dev/maxScale")
        == EXPECTED_MAX_INSTANCES,
        "minInstanceZero": annotations.get("autoscaling.knative.dev/minScale", "0")
        == "0",
        "containerConcurrencyFour": spec.get("containerConcurrency")
        == EXPECTED_CONTAINER_CONCURRENCY,
        "requestTimeoutBounded": 1 <= int(spec.get("timeoutSeconds", 0)) <= 60,
        "cpuBaseline": resources.get("cpu") == "4",
        "memoryBaseline": resources.get("memory") == "8Gi",
        "fasterWhisperSmall": env_value("CHEMICHECK119_SPEECH_MODEL") == "small",
        "cpuDevice": env_value("CHEMICHECK119_SPEECH_DEVICE") == "cpu",
        "int8Compute": env_value("CHEMICHECK119_SPEECH_COMPUTE_TYPE") == "int8",
        "anonymousDisabled": env_value("CHEMICHECK119_SPEECH_ALLOW_ANONYMOUS")
        == "false",
        "runtimeDownloadDisabled": env_value(
            "CHEMICHECK119_SPEECH_LOCAL_FILES_ONLY"
        )
        == "true",
        "apiKeySecretReference": isinstance(api_key_ref.get("name"), str)
        and isinstance(api_key_ref.get("key"), str),
    }
    return {
        "name": service.get("metadata", {}).get("name"),
        "revision": service.get("status", {}).get("latestReadyRevisionName"),
        "url": service.get("status", {}).get("url"),
        "imageDigestUri": image,
        "runtimeServiceAccount": spec.get("serviceAccountName"),
        "containerConcurrency": spec.get("containerConcurrency"),
        "timeoutSeconds": spec.get("timeoutSeconds"),
        "maxInstances": annotations.get("autoscaling.knative.dev/maxScale"),
        "minInstances": annotations.get("autoscaling.knative.dev/minScale", "0"),
        "cpu": resources.get("cpu"),
        "memory": resources.get("memory"),
        "model": env_value("CHEMICHECK119_SPEECH_MODEL"),
        "device": env_value("CHEMICHECK119_SPEECH_DEVICE"),
        "computeType": env_value("CHEMICHECK119_SPEECH_COMPUTE_TYPE"),
        "apiKeySecretName": api_key_ref.get("name"),
        "apiKeySecretVersionSelector": api_key_ref.get("key"),
        "checks": checks,
        "allChecksPassed": all(checks.values()),
    }


def load_service(project: str, region: str, service_name: str) -> dict[str, Any]:
    raw = run_gcloud(
        [
            "run",
            "services",
            "describe",
            service_name,
            "--project",
            project,
            "--region",
            region,
            "--format=json",
        ]
    )
    service = json.loads(raw)
    if not isinstance(service, dict):
        raise EvaluationError("Cloud Run service description is not an object")
    return service


def load_credentials(
    project: str, secret_name: str, secret_version: str
) -> tuple[str, str]:
    token = run_gcloud(["auth", "print-identity-token"], sensitive=True)
    api_key = run_gcloud(
        [
            "secrets",
            "versions",
            "access",
            secret_version,
            "--secret",
            secret_name,
            "--project",
            project,
        ],
        sensitive=True,
    )
    if len(token.split(".")) != 3:
        raise EvaluationError("identity token has an invalid shape")
    if len(api_key) < 32 or any(character.isspace() for character in api_key):
        raise EvaluationError("Speech API key violates the local format boundary")
    return token, api_key


def summarize_response(
    *,
    http_status: int,
    payload: dict[str, Any] | None,
    expected_request_id: str,
    response_request_id: str | None,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "httpStatus": http_status,
        "responseRequestIdMatches": response_request_id == expected_request_id,
        "jsonContractObserved": payload is not None,
    }
    if http_status == 200 and payload is not None:
        runtime = payload.get("runtime", {})
        audio_input = payload.get("input", {})
        safety = payload.get("safety_boundary", {})
        transcript = payload.get("transcript")
        checks = {
            "schemaVersion": payload.get("schema_version") == EXPECTED_SCHEMA,
            "requestId": payload.get("request_id") == expected_request_id,
            "status": payload.get("status")
            in {"TRANSCRIBED", "ABSTAINED_NO_TRANSCRIPT"},
            "transcriptObjectPresent": isinstance(transcript, dict),
            "audioNotRetained": audio_input.get("audio_retained") is False,
            "hotwordsDisabled": runtime.get("hotwords_used") is False,
            "chemicalIdentificationNotPerformed": safety.get(
                "chemical_identification_performed"
            )
            is False,
            "casConfirmationNotPerformed": safety.get("cas_confirmation_performed")
            is False,
            "riskAssessmentNotPerformed": safety.get("risk_assessment_performed")
            is False,
            "decisionSupportOnly": safety.get("decision_support_only") is True,
        }
        return {
            **base,
            "responseOrigin": "application",
            "status": payload.get("status"),
            "abstained": payload.get("abstained"),
            "audioSeconds": audio_input.get("duration_seconds"),
            "processingSeconds": runtime.get("processing_seconds"),
            "realTimeFactor": runtime.get("real_time_factor"),
            "actualDevice": runtime.get("actual_device"),
            "actualComputeType": runtime.get("actual_compute_type"),
            "serviceVersion": runtime.get("service_version"),
            "contractChecks": checks,
            "contractValid": all(checks.values()),
        }
    if http_status == 429 and payload is not None:
        error = payload.get("error", {})
        is_application = (
            payload.get("schema_version") == EXPECTED_SCHEMA
            and payload.get("request_id") == expected_request_id
            and error.get("code") == "TRANSCRIBER_BUSY"
            and error.get("retryable") is True
        )
        return {
            **base,
            "responseOrigin": "application" if is_application else "unknown_json",
            "errorCode": error.get("code"),
            "retryable": error.get("retryable"),
            "contractValid": is_application,
        }
    if http_status == 429:
        return {
            **base,
            "responseOrigin": "cloud_run_platform",
            "errorCode": "PLATFORM_CAPACITY_429",
            "retryable": None,
            "contractValid": True,
        }
    return {**base, "responseOrigin": "unknown", "contractValid": False}


def request_once(
    *,
    service_url: str,
    audio_content: bytes,
    identity_token: str,
    api_key: str,
    request_id: str,
    timeout_seconds: float,
    barrier: threading.Barrier,
) -> dict[str, Any]:
    barrier.wait(timeout=10)
    started_at = utc_now()
    started = time.perf_counter()
    request = Request(
        service_url.rstrip("/") + "/api/v1/transcriptions",
        data=audio_content,
        method="POST",
        headers={
            "Authorization": f"Bearer {identity_token}",
            "X-API-Key": api_key,
            "X-Request-Id": request_id,
            "Content-Type": "audio/wav",
            "User-Agent": USER_AGENT,
        },
    )
    http_status = 0
    response_headers: Any = {}
    payload: dict[str, Any] | None = None
    transport_error_type: str | None = None
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            http_status = response.status
            response_headers = response.headers
            payload = bounded_json(response.read(MAX_RESPONSE_BYTES + 1))
    except HTTPError as error:
        http_status = error.code
        response_headers = error.headers
        payload = bounded_json(error.read(MAX_RESPONSE_BYTES + 1))
    except (TimeoutError, URLError, OSError) as error:
        transport_error_type = type(error).__name__
    ended = time.perf_counter()
    ended_at = utc_now()
    response_request_id = response_headers.get("X-Request-Id")
    summary = summarize_response(
        http_status=http_status,
        payload=payload,
        expected_request_id=request_id,
        response_request_id=response_request_id,
    )
    client_latency = ended - started
    processing_seconds = summary.get("processingSeconds")
    non_inference_seconds = (
        max(0.0, client_latency - float(processing_seconds))
        if isinstance(processing_seconds, (int, float))
        else None
    )
    return {
        "requestId": request_id,
        "startedAt": isoformat(started_at),
        "endedAt": isoformat(ended_at),
        "clientLatencySeconds": round(client_latency, 6),
        "clientNonInferenceSecondsEstimate": (
            round(non_inference_seconds, 6)
            if non_inference_seconds is not None
            else None
        ),
        "transportErrorType": transport_error_type,
        **summary,
    }


def health_ready(
    service_url: str, identity_token: str, timeout_seconds: float
) -> dict[str, Any]:
    request_id = "REQ-SPEECH-PLATFORM-BURST-READY"
    request = Request(
        service_url.rstrip("/") + "/health/ready",
        method="GET",
        headers={
            "Authorization": f"Bearer {identity_token}",
            "X-Request-Id": request_id,
            "User-Agent": USER_AGENT,
        },
    )
    started = time.perf_counter()
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = bounded_json(response.read(MAX_RESPONSE_BYTES + 1))
        latency = time.perf_counter() - started
        return {
            "httpStatus": response.status,
            "latencySeconds": round(latency, 6),
            "ready": bool(
                response.status == 200
                and payload is not None
                and payload.get("schema_version") == EXPECTED_SCHEMA
                and payload.get("request_id") == request_id
                and payload.get("status") == "READY"
            ),
        }


def status_counts(results: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(result.get("httpStatus")) for result in results)
    return dict(sorted(counts.items()))


def latency_summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)
        return ordered[max(0, index)]

    return {
        "min": round(ordered[0], 6),
        "median": round(statistics.median(ordered), 6),
        "p95NearestRank": round(percentile(0.95), 6),
        "max": round(ordered[-1], 6),
    }


def parse_log_latency(value: Any) -> float | None:
    if not isinstance(value, str) or not value.endswith("s"):
        return None
    try:
        return float(value[:-1])
    except ValueError:
        return None


def collect_request_logs(
    *,
    project: str,
    service: str,
    revision: str,
    started_at: datetime,
    ended_at: datetime,
    expected_count: int,
) -> dict[str, Any]:
    lower = isoformat(started_at - timedelta(seconds=2))
    upper = isoformat(ended_at + timedelta(seconds=2))
    filter_expression = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service}" '
        f'AND resource.labels.revision_name="{revision}" '
        'AND logName="projects/'
        f'{project}/logs/run.googleapis.com%2Frequests" '
        f'AND httpRequest.userAgent="{USER_AGENT}" '
        'AND httpRequest.requestMethod="POST" '
        'AND httpRequest.requestUrl:"/api/v1/transcriptions" '
        f'AND timestamp>="{lower}" AND timestamp<="{upper}"'
    )
    entries: list[dict[str, Any]] = []
    for attempt in range(6):
        raw = run_gcloud(
            [
                "logging",
                "read",
                filter_expression,
                "--project",
                project,
                f"--limit={expected_count + 5}",
                "--order=asc",
                "--format=json",
            ]
        )
        candidate = json.loads(raw)
        entries = candidate if isinstance(candidate, list) else []
        if len(entries) >= expected_count:
            break
        if attempt < 5:
            time.sleep(3)
    sanitized: list[dict[str, Any]] = []
    for entry in entries:
        request = entry.get("httpRequest", {})
        sanitized.append(
            {
                "timestamp": entry.get("timestamp"),
                "severity": entry.get("severity"),
                "httpStatus": request.get("status"),
                "latencySeconds": parse_log_latency(request.get("latency")),
                "requestSizeBytes": request.get("requestSize"),
                "responseSizeBytes": request.get("responseSize"),
            }
        )
    return {
        "windowStart": lower,
        "windowEnd": upper,
        "userAgent": USER_AGENT,
        "expectedRequestCount": expected_count,
        "observedRequestCount": len(sanitized),
        "allExpectedLogsObserved": len(sanitized) == expected_count,
        "httpStatusCounts": status_counts(sanitized),
        "requestMetadata": sanitized,
        "requestOrResponseBodiesCollected": False,
        "credentialsCollected": False,
    }


def collect_resource_logs(
    *,
    project: str,
    service: str,
    revision: str,
    started_at: datetime,
    ended_at: datetime,
    expected_request_ids: set[str],
) -> dict[str, Any]:
    lower = isoformat(started_at - timedelta(seconds=2))
    upper = isoformat(ended_at + timedelta(seconds=2))
    filter_expression = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.service_name="{service}" '
        f'AND resource.labels.revision_name="{revision}" '
        f'AND jsonPayload.event="speech_resource_sample" '
        f'AND timestamp>="{lower}" AND timestamp<="{upper}"'
    )
    selected: list[dict[str, Any]] = []
    raw_entries: list[dict[str, Any]] = []
    for attempt in range(6):
        raw = run_gcloud(
            [
                "logging",
                "read",
                filter_expression,
                "--project",
                project,
                f"--limit={len(expected_request_ids) + 10}",
                "--order=asc",
                "--format=json",
            ]
        )
        candidate = json.loads(raw)
        raw_entries = candidate if isinstance(candidate, list) else []
        selected = [
            entry
            for entry in raw_entries
            if isinstance(entry.get("jsonPayload"), dict)
            and entry["jsonPayload"].get("request_id") in expected_request_ids
        ]
        if len(selected) >= len(expected_request_ids):
            break
        if attempt < 5:
            time.sleep(3)

    samples: list[dict[str, Any]] = []
    fields_allowlisted = True
    counters_nonnegative = True
    for entry in selected:
        payload = entry.get("jsonPayload", {})
        if not isinstance(payload, dict):
            fields_allowlisted = False
            continue
        fields_allowlisted = fields_allowlisted and set(payload).issubset(
            RESOURCE_LOG_FIELDS
        )
        sample = {
            field: payload.get(field)
            for field in RESOURCE_LOG_FIELDS
            if field in payload
        }
        for field in RESOURCE_BYTE_FIELDS:
            value = sample.get(field)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                counters_nonnegative = False
        samples.append(sample)
    samples.sort(key=lambda item: str(item.get("request_id")))
    observed_ids = [str(sample.get("request_id")) for sample in samples]
    exact_request_ids = (
        len(observed_ids) == len(set(observed_ids))
        and set(observed_ids) == expected_request_ids
    )
    all_available = all(
        sample.get("resource_observation_available") is True for sample in samples
    )
    return {
        "required": True,
        "windowStart": lower,
        "windowEnd": upper,
        "expectedSuccessCount": len(expected_request_ids),
        "observedSampleCount": len(samples),
        "exactRequestIdsObserved": exact_request_ids,
        "fieldsAllowlisted": fields_allowlisted,
        "byteCountersNonnegativeIntegersOrNull": counters_nonnegative,
        "allSamplesAvailable": all_available,
        "samples": samples,
        "audioOrTranscriptCollected": False,
        "credentialsCollected": False,
    }


def memory_limit_bytes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"([1-9][0-9]*)Gi", value)
    return int(match.group(1)) * (1024**3) if match else None


def evaluate(
    *,
    project: str,
    region: str,
    service_name: str,
    audio_path: Path,
    output: Path,
    batches: int,
    requests_per_batch: int,
    pause_seconds: float,
    timeout_seconds: float,
    require_resource_logs: bool,
) -> dict[str, Any]:
    if not 1 <= batches <= MAX_BATCHES:
        raise EvaluationError(f"batches must be in [1, {MAX_BATCHES}]")
    if not 2 <= requests_per_batch <= MAX_REQUESTS_PER_BATCH:
        raise EvaluationError(
            f"requests per batch must be in [2, {MAX_REQUESTS_PER_BATCH}]"
        )
    if not 0 <= pause_seconds <= 10 or not 1 <= timeout_seconds <= 75:
        raise EvaluationError("pause or timeout exceeds the bounded range")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise EvaluationError("output must be a new file in an existing directory")
    audio_content, audio_metadata = read_audio_metadata(audio_path)
    total_audio_seconds = (
        audio_metadata["durationSeconds"] * batches * requests_per_batch
    )
    if total_audio_seconds > MAX_TOTAL_AUDIO_SECONDS:
        raise EvaluationError("planned audio workload exceeds the bounded limit")

    service = service_snapshot(load_service(project, region, service_name))
    if not service["allChecksPassed"]:
        raise EvaluationError("deployed Speech API does not match the CPU baseline")
    service_url = service.get("url")
    secret_name = service.get("apiKeySecretName")
    secret_version = service.get("apiKeySecretVersionSelector")
    revision = service.get("revision")
    if not all(
        isinstance(value, str) and value
        for value in (service_url, secret_name, secret_version, revision)
    ):
        raise EvaluationError("deployed Speech API metadata is incomplete")

    identity_token, api_key = load_credentials(project, secret_name, secret_version)
    ready = health_ready(service_url, identity_token, timeout_seconds)
    if not ready["ready"]:
        raise EvaluationError("Speech API readiness preflight failed")

    experiment_started = utc_now()
    batch_reports: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []
    stop_reason: str | None = None
    prefix = utc_now().strftime("%Y%m%dT%H%M%SZ")
    for batch_number in range(1, batches + 1):
        barrier = threading.Barrier(requests_per_batch + 1)
        request_ids = [
            f"REQ-SPEECH-PLATFORM-BURST-{prefix}-B{batch_number:02d}-R{index:02d}"
            for index in range(1, requests_per_batch + 1)
        ]
        if any(not REQUEST_ID_PATTERN.fullmatch(value) for value in request_ids):
            raise EvaluationError("generated request ID violates the API boundary")
        batch_started = utc_now()
        with ThreadPoolExecutor(max_workers=requests_per_batch) as executor:
            futures = [
                executor.submit(
                    request_once,
                    service_url=service_url,
                    audio_content=audio_content,
                    identity_token=identity_token,
                    api_key=api_key,
                    request_id=request_id,
                    timeout_seconds=timeout_seconds,
                    barrier=barrier,
                )
                for request_id in request_ids
            ]
            barrier.wait(timeout=10)
            results = [future.result() for future in futures]
        batch_ended = utc_now()
        results.sort(key=lambda item: item["requestId"])
        all_results.extend(results)
        unexpected = [
            result
            for result in results
            if result["httpStatus"] not in ALLOWED_HTTP_STATUSES
            or result["transportErrorType"] is not None
            or not result["contractValid"]
        ]
        batch_reports.append(
            {
                "batch": batch_number,
                "startedAt": isoformat(batch_started),
                "endedAt": isoformat(batch_ended),
                "httpStatusCounts": status_counts(results),
                "clientLatencySeconds": latency_summary(
                    [float(result["clientLatencySeconds"]) for result in results]
                ),
                "requests": results,
            }
        )
        if unexpected:
            stop_reason = "unexpected transport, status, or response contract"
            break
        if not any(result["httpStatus"] == 200 for result in results):
            stop_reason = "batch contained no successful transcription"
            break
        if batch_number < batches and pause_seconds:
            time.sleep(pause_seconds)
    experiment_ended = utc_now()

    identity_token = ""
    api_key = ""
    expected_requests = len(all_results)
    logs = collect_request_logs(
        project=project,
        service=service_name,
        revision=revision,
        started_at=experiment_started,
        ended_at=experiment_ended,
        expected_count=expected_requests,
    )

    successful = [result for result in all_results if result["httpStatus"] == 200]
    app_busy = [
        result
        for result in all_results
        if result["httpStatus"] == 429
        and result["responseOrigin"] == "application"
    ]
    platform_busy = [
        result
        for result in all_results
        if result["httpStatus"] == 429
        and result["responseOrigin"] == "cloud_run_platform"
    ]
    successful_request_ids = {
        str(result["requestId"]) for result in successful
    }
    resource_logs = (
        collect_resource_logs(
            project=project,
            service=service_name,
            revision=revision,
            started_at=experiment_started,
            ended_at=experiment_ended,
            expected_request_ids=successful_request_ids,
        )
        if require_resource_logs
        else {
            "required": False,
            "claim": "resource log reconciliation was not requested",
        }
    )
    expected_memory_limit = memory_limit_bytes(service.get("memory"))
    resource_limits = [
        sample.get("cgroup_memory_limit_bytes")
        for sample in resource_logs.get("samples", [])
    ]
    resource_limit_matches = (
        not require_resource_logs
        or (
            expected_memory_limit is not None
            and bool(resource_limits)
            and all(value == expected_memory_limit for value in resource_limits)
        )
    )
    safety_checks = {
        "allPlannedBatchesCompleted": len(batch_reports) == batches
        and stop_reason is None,
        "onlyExpectedStatuses": all(
            result["httpStatus"] in ALLOWED_HTTP_STATUSES for result in all_results
        ),
        "noTransportErrors": all(
            result["transportErrorType"] is None for result in all_results
        ),
        "allObservedContractsValid": all(
            result["contractValid"] for result in all_results
        ),
        "successfulResponsesPreserveSafetyBoundary": all(
            result.get("contractValid") is True for result in successful
        ),
        "atLeastOneSuccessPerBatch": all(
            int(batch["httpStatusCounts"].get("200", 0)) >= 1
            for batch in batch_reports
        ),
        "cloudRequestLogsReconciled": logs["allExpectedLogsObserved"],
        "resourceLogsReconciled": not require_resource_logs
        or resource_logs.get("exactRequestIdsObserved") is True,
        "resourceFieldsAllowlisted": not require_resource_logs
        or resource_logs.get("fieldsAllowlisted") is True,
        "resourceCountersSafe": not require_resource_logs
        or resource_logs.get("byteCountersNonnegativeIntegersOrNull") is True,
        "resourceSamplesAvailable": not require_resource_logs
        or resource_logs.get("allSamplesAvailable") is True,
        "resourceLimitMatchesService": resource_limit_matches,
        "noCredentialOrPayloadStored": True,
    }
    decision = "채택" if all(safety_checks.values()) else "조건부 채택"
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": isoformat(utc_now()),
        "classification": "부분 구현 또는 개발용 데모",
        "decision": decision,
        "stopReason": stop_reason,
        "goal": (
            f"private Speech API 동시 {requests_per_batch}요청의 "
            "플랫폼 queue·app busy 분포를 반복 측정"
        ),
        "hypothesis": (
            "CPU 4·8GiB, Cloud Run concurrency 4, app semaphore 1, max instance 1에서 "
            "동시 burst는 정상 전사와 명시적 429로만 종료되고 안전 응답 경계를 유지한다."
        ),
        "service": service,
        "source": audio_metadata,
        "protocol": {
            "batchesPlanned": batches,
            "batchesCompleted": len(batch_reports),
            "requestsPerBatch": requests_per_batch,
            "requestsCompleted": len(all_results),
            "pauseSeconds": pause_seconds,
            "clientTimeoutSeconds": timeout_seconds,
            "totalSubmittedAudioSeconds": total_audio_seconds,
            "hotwordsRequested": False,
            "automaticRetries": 0,
            "maxInstances": 1,
            "resourceLogsRequired": require_resource_logs,
        },
        "aggregate": {
            "httpStatusCounts": status_counts(all_results),
            "successfulCount": len(successful),
            "applicationBusyCount": len(app_busy),
            "cloudRunPlatformBusyCount": len(platform_busy),
            "clientLatencySeconds": latency_summary(
                [float(result["clientLatencySeconds"]) for result in all_results]
            ),
            "successProcessingSeconds": latency_summary(
                [
                    float(result["processingSeconds"])
                    for result in successful
                    if isinstance(result.get("processingSeconds"), (int, float))
                ]
            ),
            "successRealTimeFactor": latency_summary(
                [
                    float(result["realTimeFactor"])
                    for result in successful
                    if isinstance(result.get("realTimeFactor"), (int, float))
                ]
            ),
            "successClientNonInferenceSecondsEstimate": latency_summary(
                [
                    float(result["clientNonInferenceSecondsEstimate"])
                    for result in successful
                    if isinstance(
                        result.get("clientNonInferenceSecondsEstimate"),
                        (int, float),
                    )
                ]
            ),
        },
        "batches": batch_reports,
        "cloudRunRequestLogReconciliation": logs,
        "resourceObservation": resource_logs,
        "safetyChecks": safety_checks,
        "allSafetyChecksPassed": all(safety_checks.values()),
        "privacy": {
            "responseTranscriptCopiedToReport": False,
            "responseBodyCopiedToReport": False,
            "audioCopiedToReport": False,
            "apiKeyOrIdentityTokenCopiedToReport": False,
            "applicationLoggingSourceFields": [
                "event",
                "request_id",
                "method",
                "route",
                "status",
                "duration_ms",
            ],
            "resourceLoggingSourceFields": sorted(RESOURCE_LOG_FIELDS),
            "logAbsenceClaimLimit": (
                "전용 요청 구간의 Cloud Run request metadata와 배포 코드의 로그 필드를 "
                "대조했으며 조직 전체 개인정보 감사를 의미하지 않는다."
            ),
        },
        "costBoundary": {
            "additionalDevelopmentBudgetKrw": 70_000,
            "predeclaredExpectedIncrementalCostKrwLessThan": 100,
            "newBuildOrDeploymentPerformed": False,
            "gpuUsed": False,
            "billingExportAttributionAvailable": False,
            "claim": "요청 횟수·timeout·max instance를 제한한 CPU preview 실험",
        },
        "claimBoundary": {
            "supports": [
                (
                    "현재 private 개발용 Speech revision의 반복 동시 "
                    f"{requests_per_batch}요청 상태·지연 분포"
                ),
                "application 429와 Cloud Run platform 429의 구분",
                "전사 성공 응답의 원음 미보존·hotword 미사용·판단 미수행 계약",
                *(
                    ["성공 요청별 numeric-only runtime resource sample 대조"]
                    if require_resource_logs
                    else []
                ),
            ],
            "doesNotSupport": [
                "실제 현장 무전 정확도나 안전성",
                "다중 인스턴스 전역 동시성 또는 고가용성",
                "상용 traffic capacity나 장시간 안정성",
                "GPU 추론의 속도·비용 우위",
                "조직 전체 개인정보·로그 보안 감사",
            ],
        },
        "reproducibility": {
            "runnerSha256": sha256_file(Path(__file__).resolve()),
            "rawCredentialsStored": False,
            "rawAudioStoredByRunner": False,
            "rawResponsesStored": False,
        },
    }
    payload = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    with output.open("xb") as destination:
        destination.write(payload)
    print(hashlib.sha256(payload).hexdigest())
    return report


def self_test() -> None:
    marker = "DO_NOT_LEAK_TRANSCRIPT"
    success_payload = {
        "schema_version": EXPECTED_SCHEMA,
        "request_id": "REQ-TEST-1",
        "status": "TRANSCRIBED",
        "abstained": False,
        "transcript": {"text": marker, "segments": []},
        "input": {"duration_seconds": 7.4, "audio_retained": False},
        "runtime": {
            "hotwords_used": False,
            "processing_seconds": 1.2,
            "real_time_factor": 0.16,
            "actual_device": "cpu",
            "actual_compute_type": "int8",
            "service_version": "0.1.0",
        },
        "safety_boundary": {
            "chemical_identification_performed": False,
            "cas_confirmation_performed": False,
            "risk_assessment_performed": False,
            "decision_support_only": True,
        },
    }
    summary = summarize_response(
        http_status=200,
        payload=success_payload,
        expected_request_id="REQ-TEST-1",
        response_request_id="REQ-TEST-1",
    )
    serialized = json.dumps(summary)
    if not summary["contractValid"] or marker in serialized or "transcript" in summary:
        raise AssertionError("success summary leaked or rejected transcript metadata")
    busy_payload = {
        "schema_version": EXPECTED_SCHEMA,
        "request_id": "REQ-TEST-2",
        "error": {"code": "TRANSCRIBER_BUSY", "retryable": True},
    }
    busy = summarize_response(
        http_status=429,
        payload=busy_payload,
        expected_request_id="REQ-TEST-2",
        response_request_id="REQ-TEST-2",
    )
    platform = summarize_response(
        http_status=429,
        payload=None,
        expected_request_id="REQ-TEST-3",
        response_request_id=None,
    )
    if (
        busy["responseOrigin"] != "application"
        or not busy["contractValid"]
        or platform["responseOrigin"] != "cloud_run_platform"
        or not platform["contractValid"]
    ):
        raise AssertionError("429 origin classification failed")
    safe_resource = {
        "jsonPayload": {
            "event": "speech_resource_sample",
            "request_id": "REQ-TEST-1",
            "processing_seconds": 1.2,
            "audio_seconds": 7.4,
            "resource_observation_available": True,
            "cgroup_version": "v2",
            "cgroup_memory_current_bytes": 123,
            "cgroup_memory_peak_bytes": 456,
            "cgroup_memory_limit_bytes": 789,
            "process_current_rss_bytes": 100,
            "process_max_rss_bytes": 200,
        }
    }
    if not set(safe_resource["jsonPayload"]).issubset(RESOURCE_LOG_FIELDS):
        raise AssertionError("resource event allowlist rejected safe fields")
    unsafe_resource = dict(safe_resource["jsonPayload"])
    unsafe_resource["transcript"] = "must-not-pass"
    if set(unsafe_resource).issubset(RESOURCE_LOG_FIELDS):
        raise AssertionError("resource event allowlist accepted transcript")
    print("Private Speech burst evaluator self-test passed.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project")
    parser.add_argument("--region")
    parser.add_argument("--service")
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--batches", type=int, default=5)
    parser.add_argument("--requests-per-batch", type=int, default=5)
    parser.add_argument("--pause-seconds", type=float, default=2.0)
    parser.add_argument("--timeout-seconds", type=float, default=70.0)
    parser.add_argument("--require-resource-logs", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    required = (args.project, args.region, args.service, args.audio, args.output)
    if any(value is None for value in required):
        parser.error("project, region, service, audio, and output are required")
    evaluate(
        project=args.project,
        region=args.region,
        service_name=args.service,
        audio_path=args.audio,
        output=args.output,
        batches=args.batches,
        requests_per_batch=args.requests_per_batch,
        pause_seconds=args.pause_seconds,
        timeout_seconds=args.timeout_seconds,
        require_resource_logs=args.require_resource_logs,
    )


if __name__ == "__main__":
    try:
        main()
    except (
        EvaluationError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
