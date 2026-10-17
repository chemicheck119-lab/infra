#!/usr/bin/env python3
"""Read-only, allowlisted GCP metadata. Never reads secret payloads or enables APIs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess


def service_summary(service, iam):
    template = service.get("spec", {}).get("template", {})
    spec = template.get("spec", {})
    container = (spec.get("containers") or [{}])[0]
    metadata = service.get("metadata", {})
    annotations = metadata.get("annotations", {})
    revision = template.get("metadata", {}).get("annotations", {})
    members = [member for binding in (iam or {}).get("bindings", [])
               if binding.get("role") == "roles/run.invoker" for member in binding.get("members", [])]
    return {
        "service": metadata.get("name"),
        "container_concurrency": spec.get("containerConcurrency"),
        "timeout_seconds": spec.get("timeoutSeconds"),
        "resource_limits": container.get("resources", {}).get("limits", {}),
        "service_scaling": {key: value for key, value in annotations.items()
                            if key in {"run.googleapis.com/maxScale", "run.googleapis.com/minScale",
                                       "run.googleapis.com/manualInstanceCount", "run.googleapis.com/scalingMode"}},
        "revision_scaling": {key: value for key, value in revision.items()
                             if key in {"autoscaling.knative.dev/minScale", "autoscaling.knative.dev/maxScale",
                                        "run.googleapis.com/cpu-throttling"}},
        "traffic_tag_count": sum(bool(item.get("tag")) for item in service.get("status", {}).get("traffic", [])),
        "iam_available": iam is not None,
        "public_invoker": "allUsers" in members if iam is not None else None,
        "all_authenticated_invoker": "allAuthenticatedUsers" in members if iam is not None else None,
        # Never export env entries, addresses, account identities or IAM member names.
    }


def read_gcloud(arguments):
    try:
        result = subprocess.run(["gcloud", *arguments, "--format=json", "--quiet"],
                                capture_output=True, text=True, timeout=45, check=False)
        if result.returncode or len(result.stdout) > 4_000_000:
            return None
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="asia-northeast3")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", args.project):
        parser.error("invalid project ID")
    if not re.fullmatch(r"[a-z]+-[a-z]+[0-9]", args.region):
        parser.error("invalid region")
    target = args.output.resolve()
    if any((parent / ".git").exists() for parent in [target, *target.parents]):
        parser.error("output must be outside Git checkouts")
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    billing = read_gcloud(["billing", "projects", "describe", args.project])
    account_name = (billing or {}).get("billingAccountName", "").split("/")[-1]
    account = read_gcloud(["billing", "accounts", "describe", account_name]) if account_name else None
    services = read_gcloud(["run", "services", "list", "--project=" + args.project,
                           "--region=" + args.region, "--platform=managed"])
    sql = read_gcloud(["sql", "instances", "list", "--project=" + args.project])
    report = {"observed_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
              "billing_project_enabled": (billing or {}).get("billingEnabled"),
              "billing_account_open": (account or {}).get("open"),
              "actual_costs_included": False, "services": None, "sql": None}
    if services is not None:
        report["services"] = []
        for service in services:
            iam = read_gcloud(["run", "services", "get-iam-policy", service["metadata"]["name"],
                               "--project=" + args.project, "--region=" + args.region])
            report["services"].append(service_summary(service, iam))
    if sql is not None:
        report["sql"] = []
        for instance in sql:
            settings = instance.get("settings", {})
            network = settings.get("ipConfiguration", {})
            report["sql"].append({"state": instance.get("state"), "tier": settings.get("tier"),
                                  "activation_policy": settings.get("activationPolicy"),
                                  "disk_gb": settings.get("dataDiskSizeGb"),
                                  "storage_resize_limit_gb": settings.get("storageAutoResizeLimit"),
                                  "public_ipv4": network.get("ipv4Enabled"),
                                  "tls_mode": network.get("sslMode"),
                                  "authorized_network_count": len(network.get("authorizedNetworks", []))})
    raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()
    path = target / "metadata.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    print(json.dumps({"output": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                      "actual_costs_included": False}))


if __name__ == "__main__":
    main()
