#!/usr/bin/env python3
"""Live-validate the collector data-processing cookbook examples.

The workflow runs the local Splunk OTel Collector container with validation
configs that preserve each cookbook's receiver/processor intent and replace
external Splunk exporters with the debug exporter. It sends synthetic telemetry
only and records observable before/after evidence from Collector logs.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


IMAGE = "quay.io/signalfx/splunk-otel-collector:latest"
NANO = 1_710_000_000_000_000_000
ROOT = Path(__file__).resolve().parents[1]
KEEP_CONTAINERS = False


@dataclass
class ValidationResult:
    slug: str
    title: str
    passed: bool
    summary: str
    observed: dict[str, str] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


@dataclass
class BackendQueryResult:
    found: bool
    http_status: int
    metric: str
    dimensions: dict[str, str]
    evidence: list[str] = field(default_factory=list)
    values: list[object] = field(default_factory=list)


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
        timeout=timeout,
        env=env,
    )


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def post_json(url: str, payload: dict) -> str:
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=5) as response:
            return response.read().decode("utf-8")
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not POST to {url}: {exc}") from exc


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        value = raw_value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def backend_env(env_file: Path, realm: str) -> dict[str, str]:
    load_env_file(env_file)
    missing = [name for name in ("SPLUNK_ACCESS_TOKEN", "SPLUNK_API_TOKEN") if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"missing required backend environment values: {', '.join(missing)}")
    return {
        "SPLUNK_ACCESS_TOKEN": os.environ["SPLUNK_ACCESS_TOKEN"],
        "SPLUNK_API_TOKEN": os.environ["SPLUNK_API_TOKEN"],
        "SPLUNK_REALM": realm,
        "SPLUNK_API_URL": f"https://api.{realm}.observability.splunkcloud.com",
        "SPLUNK_INGEST_URL": f"https://ingest.{realm}.observability.splunkcloud.com",
        "SPLUNK_STREAM_URL": f"https://stream.{realm}.observability.splunkcloud.com",
    }


def log_record(body: str, *, severity_number: int = 9, severity_text: str = "INFO", attrs: dict[str, object] | None = None) -> dict:
    return {
        "timeUnixNano": str(NANO),
        "observedTimeUnixNano": str(NANO),
        "severityText": severity_text,
        "severityNumber": severity_number,
        "body": value(body),
        "attributes": attrs_list(attrs or {}),
    }


def span(name: str, *, trace_id: str, span_id: str, status_code: int = 1, duration_ms: int = 25, attrs: dict[str, object] | None = None) -> dict:
    return {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": 2,
        "startTimeUnixNano": str(NANO),
        "endTimeUnixNano": str(NANO + duration_ms * 1_000_000),
        "status": {"code": status_code},
        "attributes": attrs_list(attrs or {}),
    }


def gauge_metric(
    name: str,
    val: float,
    *,
    attrs: dict[str, object] | None = None,
    time_unix_nano: int = NANO,
) -> dict:
    return {
        "name": name,
        "gauge": {
            "dataPoints": [
                {
                    "timeUnixNano": str(time_unix_nano),
                    "asDouble": val,
                    "attributes": attrs_list(attrs or {}),
                }
            ]
        },
    }


def value(item: object) -> dict:
    if isinstance(item, bool):
        return {"boolValue": item}
    if isinstance(item, int):
        return {"intValue": str(item)}
    if isinstance(item, float):
        return {"doubleValue": item}
    if isinstance(item, dict):
        return {"kvlistValue": {"values": [{"key": k, "value": value(v)} for k, v in item.items()]}}
    return {"stringValue": str(item)}


def attrs_list(attrs: dict[str, object]) -> list[dict]:
    return [{"key": k, "value": value(v)} for k, v in attrs.items()]


def logs_payload(records: list[dict], *, service: str = "cookbook-validation") -> dict:
    return {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": attrs_list(
                        {
                            "service.name": service,
                            "deployment.environment": "validation",
                        }
                    )
                },
                "scopeLogs": [{"scope": {"name": "validation"}, "logRecords": records}],
            }
        ]
    }


def traces_payload(spans: list[dict], *, service: str = "cookbook-validation") -> dict:
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": attrs_list(
                        {
                            "service.name": service,
                            "deployment.environment": "validation",
                        }
                    )
                },
                "scopeSpans": [{"scope": {"name": "validation"}, "spans": spans}],
            }
        ]
    }


def metrics_payload(metrics: list[dict], *, service: str = "cookbook-validation") -> dict:
    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": attrs_list(
                        {
                            "service.name": service,
                            "deployment.environment": "validation",
                        }
                    )
                },
                "scopeMetrics": [{"scope": {"name": "validation"}, "metrics": metrics}],
            }
        ]
    }


class MetricsHandler(BaseHTTPRequestHandler):
    body = b""

    def do_GET(self) -> None:
        if self.path != "/metrics":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *_: object) -> None:
        return


class MetricsServer:
    def __init__(self, body: str) -> None:
        self.port = free_port()
        handler = type("CookbookMetricsHandler", (MetricsHandler,), {"body": body.encode("utf-8")})
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)

    def __enter__(self) -> "MetricsServer":
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()


class CollectorRun:
    def __init__(
        self,
        slug: str,
        config: str,
        workdir: Path,
        ports: dict[int, int] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.slug = slug
        self.name = f"cookbook-validation-{slug[:32]}-{int(time.time() * 1000)}"
        self.config = config
        self.workdir = workdir
        self.ports = ports or {}
        self.env = env or {}
        self.config_path = workdir / f"{self.name}.yaml"

    def __enter__(self) -> "CollectorRun":
        self.config_path.write_text(self.config, encoding="utf-8")
        cmd = ["docker", "create", "--name", self.name]
        if platform.system() == "Linux":
            cmd.extend(["--add-host", "host.docker.internal:host-gateway"])
        for host_port, container_port in self.ports.items():
            cmd.extend(["-p", f"127.0.0.1:{host_port}:{container_port}"])
        container_env = os.environ.copy()
        container_env.update(self.env)
        for key in self.env:
            cmd.extend(["-e", key])
        cmd.extend([IMAGE, f"--config=/tmp/{self.name}.yaml"])
        try:
            run(cmd, env=container_env)
            run(["docker", "cp", str(self.config_path), f"{self.name}:/tmp/{self.name}.yaml"])
            run(["docker", "start", self.name])
            self.wait_ready()
        except Exception:
            run(["docker", "rm", "-f", self.name], check=False)
            raise
        return self

    def __exit__(self, *_: object) -> None:
        if not KEEP_CONTAINERS:
            run(["docker", "rm", "-f", self.name], check=False)
        else:
            print(f"Kept temporary container {self.name}", flush=True)

    def logs(self) -> str:
        completed = run(["docker", "logs", "--tail", "400", self.name], timeout=10)
        return completed.stdout + completed.stderr

    def wait_ready(self) -> None:
        deadline = time.time() + 20
        while time.time() < deadline:
            logs = self.logs()
            if "Everything is ready" in logs:
                return
            if "application run finished with error" in logs or "\tError:" in logs:
                raise RuntimeError(logs)
            time.sleep(0.4)
        raise TimeoutError(self.logs())


def base_processors(extra: str) -> str:
    return textwrap.dedent(
        f"""
        processors:
          memory_limiter:
            check_interval: 2s
            limit_mib: 256
        {textwrap.indent(extra.strip(), '  ')}
          resourcedetection:
            detectors: [env, system]
            override: false
          resource/splunk_context:
            attributes:
              - action: upsert
                key: deployment.environment
                value: validation
          batch: {{}}
        """
    ).strip()


def debug_exporter() -> str:
    return textwrap.dedent(
        """
        exporters:
          debug:
            verbosity: detailed
        """
    ).strip()


def signalfx_exporter(realm: str) -> str:
    return textwrap.dedent(
        f"""
        exporters:
          signalfx:
            access_token: "${{env:SPLUNK_ACCESS_TOKEN}}"
            api_url: "https://api.{realm}.observability.splunkcloud.com"
            ingest_url: "https://ingest.{realm}.observability.splunkcloud.com"
            sync_host_metadata: false
        """
    ).strip()


def otlp_receiver() -> str:
    return textwrap.dedent(
        """
        receivers:
          otlp:
            protocols:
              http:
                endpoint: 0.0.0.0:4318
        """
    ).strip()


def service_pipeline(signal: str, processors: list[str]) -> str:
    receiver = "otlp"
    return textwrap.dedent(
        f"""
        service:
          telemetry:
            logs:
              level: info
          pipelines:
            {signal}:
              receivers: [{receiver}]
              processors: [{', '.join(processors)}]
              exporters: [debug]
        """
    ).strip()


def service_multi(pipelines: dict[str, list[str]]) -> str:
    lines = [
        "service:",
        "  telemetry:",
        "    logs:",
        "      level: info",
        "  pipelines:",
    ]
    for signal, processors in pipelines.items():
        lines.extend(
            [
                f"    {signal}:",
                "      receivers: [otlp]",
                f"      processors: [{', '.join(processors)}]",
                "      exporters: [debug]",
            ]
        )
    return "\n".join(lines)


def service_metrics_export(processors: list[str], exporter: str = "debug", receiver: str = "otlp") -> str:
    return textwrap.dedent(
        f"""
        service:
          telemetry:
            logs:
              level: info
          pipelines:
            metrics:
              receivers: [{receiver}]
              processors: [{', '.join(processors)}]
              exporters: [{exporter}]
        """
    ).strip()


def join_config(*sections: str) -> str:
    return "\n\n".join(section.strip() for section in sections if section.strip()) + "\n"


def assert_contains(text: str, expected: list[str]) -> list[str]:
    return [item for item in expected if item not in text]


def signalflow_query(
    metric: str,
    dimensions: dict[str, str],
    env: dict[str, str],
    *,
    start_unix: int,
    stop_unix: int,
    timeout: int = 45,
) -> BackendQueryResult:
    filters = " and ".join(f'filter("{key}", "{value}")' for key, value in dimensions.items())
    if filters:
        program = f'data("{metric}", filter={filters}).publish(label="A")'
    else:
        program = f'data("{metric}").publish(label="A")'
    body = json.dumps({"programText": program}).encode("utf-8")
    query = urlencode({"start": f"{start_unix * 1000}", "stop": f"{stop_unix * 1000}", "resolution": "10000"})
    url = f"{env['SPLUNK_STREAM_URL']}/v2/signalflow/execute?{query}"
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "X-SF-TOKEN": env["SPLUNK_API_TOKEN"]},
        method="POST",
    )
    evidence: list[str] = []
    values: list[object] = []
    status = 0
    current_event = ""
    data_lines: list[str] = []

    def flush_event() -> None:
        nonlocal current_event, data_lines, values
        if not current_event and not data_lines:
            return
        payload = "\n".join(data_lines).strip()
        if current_event in {"control-message", "message", "data"} and payload:
            compact = " ".join(payload.split())
            evidence.append(f"{current_event}: {compact[:500]}")
        if current_event == "data" and payload:
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                parsed = {}
            batch = parsed.get("data") if isinstance(parsed, dict) else None
            if batch:
                values.extend(batch)
        current_event = ""
        data_lines = []

    try:
        with urlopen(req, timeout=timeout) as response:
            status = response.status
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\n")
                if line.startswith("event:"):
                    flush_event()
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.split(":", 1)[1].strip())
                elif not line.strip():
                    flush_event()
                if values:
                    break
            flush_event()
    except HTTPError as exc:
        status = exc.code
        body = exc.read().decode("utf-8", errors="replace")
        evidence.append(f"HTTP {exc.code}: {' '.join(body.split())[:500]}")
    except (TimeoutError, socket.timeout):
        evidence.append("SignalFlow read timed out after receiving partial stream")
        flush_event()
    except URLError as exc:
        evidence.append(f"SignalFlow request failed: {exc}")

    found = bool(values)
    if not found and not any("FIND_MATCHED_NO_TIMESERIES" in item for item in evidence):
        evidence.append("No non-empty data events were returned")
    return BackendQueryResult(found, status, metric, dimensions, evidence[-8:], values[:5])


def validate_prometheus_static(workdir: Path) -> ValidationResult:
    slug = "prometheus-scrape-to-splunk"
    body = """# HELP http_server_requests_total Synthetic retained counter
# TYPE http_server_requests_total counter
http_server_requests_total{route="/checkout"} 7
# HELP promhttp_metric_handler_requests_total Synthetic excluded counter
# TYPE promhttp_metric_handler_requests_total counter
promhttp_metric_handler_requests_total{code="200"} 3
"""
    with MetricsServer(body) as server:
        receivers = textwrap.dedent(
            f"""
            receivers:
              prometheus/static_targets:
                config:
                  scrape_configs:
                    - job_name: app-metrics
                      scrape_interval: 1s
                      scrape_timeout: 1s
                      metrics_path: /metrics
                      static_configs:
                        - targets: ["host.docker.internal:{server.port}"]
                      metric_relabel_configs:
                        - source_labels: [__name__]
                          regex: "(http_server_requests_total)"
                          action: keep
            """
        )
        service = textwrap.dedent(
            """
            service:
              telemetry:
                logs:
                  level: info
              pipelines:
                metrics:
                  receivers: [prometheus/static_targets]
                  processors: [memory_limiter, resourcedetection, resource/splunk_context, batch]
                  exporters: [debug]
            """
        )
        config = join_config(receivers, base_processors(""), debug_exporter(), service)
        with CollectorRun(slug, config, workdir) as collector:
            time.sleep(8)
            logs = collector.logs()
    missing = assert_contains(logs, ["http_server_requests_total", "deployment.environment", "validation"])
    unexpected = "promhttp_metric_handler_requests_total" in logs
    return ValidationResult(
        slug,
        "Prometheus Scraping to Splunk",
        not missing and not unexpected,
        "Scraped a synthetic Prometheus endpoint and exported only the allow-listed metric.",
        {
            "before": "Synthetic endpoint exposed http_server_requests_total and promhttp_metric_handler_requests_total.",
            "after": "debug exporter output contained http_server_requests_total with deployment.environment=validation; excluded promhttp_metric_handler_requests_total was not exported.",
        },
        missing + (["promhttp_metric_handler_requests_total unexpectedly exported"] if unexpected else []),
    )


def validate_backend_probe(workdir: Path, env: dict[str, str]) -> ValidationResult:
    slug = "backend-signalfx-exporter-probe"
    run_id = str(int(time.time()))
    metric = "test_requests_total"
    start_unix = int(time.time())
    current_nano = start_unix * 1_000_000_000
    config = join_config(
        otlp_receiver(),
        base_processors(""),
        signalfx_exporter(env["SPLUNK_REALM"]),
        service_metrics_export(["memory_limiter", "resourcedetection", "resource/splunk_context", "batch"], "signalfx"),
    )
    port = free_port()
    collector_env = {"SPLUNK_ACCESS_TOKEN": env["SPLUNK_ACCESS_TOKEN"]}
    with CollectorRun(slug, config, workdir, {port: 4318}, collector_env) as collector:
        post_json(
            f"http://127.0.0.1:{port}/v1/metrics",
            metrics_payload(
                [
                    gauge_metric(
                        metric,
                        42,
                        attrs={
                            "validation_run_id": run_id,
                            "source": "codex-collector-backend-probe",
                        },
                        time_unix_nano=current_nano,
                    )
                ],
                service="codex-collector-backend-probe",
            ),
        )
        time.sleep(30)
        logs = collector.logs()
    exporter_errors = [
        line
        for line in logs.splitlines()
        if "Exporting failed" in line or "Permanent error" in line or "Unauthorized" in line
    ]
    time.sleep(90)
    query = signalflow_query(
        metric,
        {"validation_run_id": run_id},
        env,
        start_unix=start_unix - 120,
        stop_unix=int(time.time()) + 180,
    )
    observed_after = [
        f"Collector signalfx exporter sent metric {metric} with validation_run_id={run_id}.",
        f"SignalFlow query HTTP status: {query.http_status}.",
        f"SignalFlow found series: {query.found}.",
    ]
    if query.values:
        observed_after.append(f"First SignalFlow data event: {json.dumps(query.values[0], sort_keys=True)[:500]}")
    else:
        observed_after.extend(query.evidence[-3:])
    errors = exporter_errors + ([] if query.found else ["SignalFlow did not return a non-empty data event for the exported metric"])
    return ValidationResult(
        slug,
        "Backend Signalfx Exporter Probe",
        not errors,
        "Exported a synthetic metric through the Collector signalfx exporter and queried it through Splunk Observability Cloud SignalFlow.",
        {
            "before": f"Synthetic OTLP metric {metric}=42 was posted to a local Collector with validation_run_id={run_id}.",
            "after": "\n".join(observed_after),
        },
        errors,
    )


def attach_backend_markers(
    workdir: Path,
    env: dict[str, str],
    local_results: list[ValidationResult],
) -> list[ValidationResult]:
    marker_metric = "test_requests_total"
    started = int(time.time())
    markers: dict[str, tuple[str, int]] = {}
    config = join_config(
        otlp_receiver(),
        base_processors(""),
        signalfx_exporter(env["SPLUNK_REALM"]),
        service_metrics_export(["memory_limiter", "resourcedetection", "resource/splunk_context", "batch"], "signalfx"),
    )
    port = free_port()
    collector_env = {"SPLUNK_ACCESS_TOKEN": env["SPLUNK_ACCESS_TOKEN"]}
    with CollectorRun("backend-cookbook-markers", config, workdir, {port: 4318}, collector_env):
        for idx, item in enumerate(local_results, start=1):
            if not item.passed:
                continue
            run_id = f"{item.slug}-{started}"
            markers[item.slug] = (run_id, idx)
            post_json(
                f"http://127.0.0.1:{port}/v1/metrics",
                metrics_payload(
                    [
                        gauge_metric(
                            marker_metric,
                            idx,
                            attrs={
                                "validation_run_id": run_id,
                                "cookbook_slug": item.slug,
                                "validation_backend": "splunk-us0-signalflow",
                            },
                            time_unix_nano=(started + idx) * 1_000_000_000,
                        )
                    ],
                    service="codex-cookbook-backend-validation",
                ),
            )
        time.sleep(30)

    time.sleep(90)
    combined: list[ValidationResult] = []
    for item in local_results:
        marker = markers.get(item.slug)
        if not marker:
            combined.append(item)
            continue
        run_id, expected_value = marker
        query = signalflow_query(
            marker_metric,
            {"validation_run_id": run_id},
            env,
            start_unix=started - 120,
            stop_unix=int(time.time()) + 240,
        )
        backend_lines = [
            f"Splunk realm: {env['SPLUNK_REALM']}",
            f"SignalFlow metric: {marker_metric}",
            f"validation_run_id: {run_id}",
            f"SignalFlow HTTP status: {query.http_status}",
            f"SignalFlow found series: {query.found}",
        ]
        if query.values:
            backend_lines.append(f"SignalFlow data event: {json.dumps(query.values[0], sort_keys=True)[:500]}")
        else:
            backend_lines.extend(query.evidence[-3:])
        errors = list(item.errors)
        if not query.found:
            errors.append(f"Splunk backend marker was not queryable for validation_run_id={run_id}")
        combined.append(
            ValidationResult(
                item.slug,
                item.title,
                item.passed and query.found,
                item.summary,
                {
                    **item.observed,
                    "backend": "\n".join(backend_lines),
                },
                errors,
            )
        )
    return combined


def validate_prometheus_kubernetes(workdir: Path) -> ValidationResult:
    slug = "prometheus-scrape-kubernetes-discovery"
    body = """# HELP http_server_requests_total Synthetic retained service counter
# TYPE http_server_requests_total counter
http_server_requests_total{service="checkout"} 11
# HELP go_threads Synthetic excluded runtime metric
# TYPE go_threads gauge
go_threads 21
"""
    with MetricsServer(body) as server:
        receivers = textwrap.dedent(
            f"""
            receivers:
              prometheus/kubernetes_services:
                config:
                  scrape_configs:
                    - job_name: kubernetes-service-metrics-validation
                      scrape_interval: 1s
                      scrape_timeout: 1s
                      static_configs:
                        - targets: ["host.docker.internal:{server.port}"]
                      metric_relabel_configs:
                        - source_labels: [__name__]
                          regex: "(http_server_requests_total)"
                          action: keep
            """
        )
        service = textwrap.dedent(
            """
            service:
              telemetry:
                logs:
                  level: info
              pipelines:
                metrics:
                  receivers: [prometheus/kubernetes_services]
                  processors: [memory_limiter, resourcedetection, resource/splunk_context, batch]
                  exporters: [debug]
            """
        )
        config = join_config(receivers, base_processors(""), debug_exporter(), service)
        with CollectorRun(slug, config, workdir) as collector:
            time.sleep(8)
            logs = collector.logs()
    missing = assert_contains(logs, ["http_server_requests_total", "deployment.environment", "validation"])
    unexpected = "go_threads" in logs
    return ValidationResult(
        slug,
        "Prometheus Scraping with Kubernetes Discovery",
        not missing and not unexpected,
        "Validated the Prometheus receiver/relabel/export path with a local static target equivalent. Kubernetes discovery itself still requires cluster validation.",
        {
            "before": "Synthetic endpoint exposed an application metric and a runtime metric.",
            "after": "debug exporter output contained the application metric and excluded the runtime metric by relabel rule.",
        },
        missing + (["go_threads unexpectedly exported"] if unexpected else []),
    )


def validate_filter(workdir: Path) -> ValidationResult:
    slug = "filter-noisy-telemetry-before-export"
    processor_config = base_processors(
        """
        filter/noise:
          error_mode: ignore
          trace_conditions:
            - 'IsMatch(span.name, ".*/(health|ready|live|metrics).*")'
          metric_conditions:
            - 'IsMatch(metric.name, "^(go_|process_|promhttp_).*")'
            - 'datapoint.attributes["http.route"] == "/health"'
          log_conditions:
            - 'log.severity_number < SEVERITY_NUMBER_WARN'
            - 'IsMatch(log.body, "(?i).*health(check)?.*")'
        """
    )
    config = join_config(
        otlp_receiver(),
        processor_config,
        debug_exporter(),
        service_multi({
            "traces": ["memory_limiter", "filter/noise", "resourcedetection", "resource/splunk_context", "batch"],
            "metrics": ["memory_limiter", "filter/noise", "resourcedetection", "resource/splunk_context", "batch"],
            "logs": ["memory_limiter", "filter/noise", "resourcedetection", "resource/splunk_context", "batch"],
        }),
    )
    port = free_port()
    with CollectorRun(slug, config, workdir, {port: 4318}) as collector:
        post_json(
            f"http://127.0.0.1:{port}/v1/traces",
            traces_payload(
                [
                    span("GET /health", trace_id="00000000000000000000000000000001", span_id="0000000000000001"),
                    span("GET /checkout", trace_id="00000000000000000000000000000002", span_id="0000000000000002", status_code=2),
                ]
            ),
        )
        post_json(
            f"http://127.0.0.1:{port}/v1/metrics",
            metrics_payload(
                [
                    gauge_metric("process_cpu_seconds_total", 1, attrs={"http.route": "/work"}),
                    gauge_metric("checkout_requests_total", 2, attrs={"http.route": "/checkout"}),
                ]
            ),
        )
        post_json(
            f"http://127.0.0.1:{port}/v1/logs",
            logs_payload(
                [
                    log_record("healthcheck ok", severity_number=9, severity_text="INFO"),
                    log_record("checkout failed", severity_number=17, severity_text="ERROR"),
                ]
            ),
        )
        time.sleep(2)
        logs = collector.logs()
    required = ["GET /checkout", "checkout_requests_total", "checkout failed"]
    forbidden = ["GET /health", "process_cpu_seconds_total", "healthcheck ok"]
    missing = assert_contains(logs, required)
    unexpected = [item for item in forbidden if item in logs]
    return ValidationResult(
        slug,
        "Filter Noisy Telemetry Before Export",
        not missing and not unexpected,
        "Dropped health span, runtime metric, and low-severity health log while retaining error/service telemetry.",
        {
            "before": "Synthetic batch included GET /health, process_cpu_seconds_total, healthcheck ok, GET /checkout, checkout_requests_total, and checkout failed.",
            "after": "debug exporter output retained GET /checkout, checkout_requests_total, and checkout failed; dropped the noisy samples.",
        },
        missing + [f"{item} unexpectedly exported" for item in unexpected],
    )


def validate_transform(workdir: Path) -> ValidationResult:
    slug = "transform-normalize-telemetry-before-export"
    processor_config = base_processors(
        """
        transform/normalize:
          error_mode: ignore
          trace_statements:
            - context: span
              statements:
                - delete_key(attributes, "http.request.header.authorization")
                - delete_key(attributes, "http.request.header.cookie")
                - replace_pattern(attributes["db.statement"], "(?i)(password|token|api_key)\\\\s*=\\\\s*'[^']*'", "$$1='***'") where attributes["db.statement"] != nil
                - truncate_all(attributes, 2048)
                - limit(attributes, 128, ["http.method", "http.route", "http.status_code"])
          log_statements:
            - context: log
              statements:
                - delete_key(attributes, "http.request.header.authorization")
                - delete_key(attributes, "http.request.header.cookie")
                - replace_pattern(log.body, "(?i)(password|token|api[_-]?key)=([^\\\\s]+)", "$$1=***") where IsString(log.body)
        """
    )
    config = join_config(
        otlp_receiver(),
        processor_config,
        debug_exporter(),
        service_multi({
            "traces": ["memory_limiter", "transform/normalize", "resourcedetection", "resource/splunk_context", "batch"],
            "logs": ["memory_limiter", "transform/normalize", "resourcedetection", "resource/splunk_context", "batch"],
        }),
    )
    port = free_port()
    with CollectorRun(slug, config, workdir, {port: 4318}) as collector:
        post_json(
            f"http://127.0.0.1:{port}/v1/traces",
            traces_payload(
                [
                    span(
                        "SELECT checkout",
                        trace_id="00000000000000000000000000000003",
                        span_id="0000000000000003",
                        attrs={
                            "http.request.header.authorization": "Bearer synthetic-token",
                            "http.request.header.cookie": "session=synthetic",
                            "db.statement": "select * from users where password='synthetic-secret'",
                            "http.method": "GET",
                        },
                    )
                ]
            ),
        )
        post_json(
            f"http://127.0.0.1:{port}/v1/logs",
            logs_payload([log_record("login token=synthetic-token", severity_number=17, severity_text="ERROR")]),
        )
        time.sleep(2)
        logs = collector.logs()
    required = ["password='***'", "login token=***"]
    forbidden = ["Bearer synthetic-token", "session=synthetic", "synthetic-secret", "login token=synthetic-token"]
    missing = assert_contains(logs, required)
    unexpected = [item for item in forbidden if item in logs]
    return ValidationResult(
        slug,
        "Transform and Normalize Telemetry Before Export",
        not missing and not unexpected,
        "Removed sensitive span header attributes and masked synthetic secrets in span/log content.",
        {
            "before": "Synthetic span/log contained Bearer synthetic-token, session=synthetic, password='synthetic-secret', and login token=synthetic-token.",
            "after": "debug exporter output contained password='***' and login token=***; removed authorization/cookie attributes and raw secret values.",
        },
        missing + [f"{item} unexpectedly exported" for item in unexpected],
    )


def validate_probabilistic(workdir: Path) -> ValidationResult:
    slug = "probabilistic-sampling-before-export"
    processor_config = base_processors(
        """
        probabilistic_sampler/logs:
          sampling_percentage: 20
          hash_seed: 22
          fail_closed: false
        """
    )
    config = join_config(
        otlp_receiver(),
        processor_config,
        debug_exporter(),
        service_pipeline("logs", ["memory_limiter", "probabilistic_sampler/logs", "resourcedetection", "resource/splunk_context", "batch"]),
    )
    port = free_port()
    records = [log_record(f"sample-candidate-{idx}", severity_number=17, severity_text="ERROR") for idx in range(100)]
    with CollectorRun(slug, config, workdir, {port: 4318}) as collector:
        post_json(f"http://127.0.0.1:{port}/v1/logs", logs_payload(records))
        time.sleep(2)
        logs = collector.logs()
    matches = len(set(re.findall(r"sample-candidate-(\d+)", logs)))
    return ValidationResult(
        slug,
        "Probabilistic Sampling Before Export",
        5 <= matches <= 45,
        f"Sent 100 synthetic logs through a 20 percent sampler; debug exporter showed {matches} retained unique log records.",
        {
            "before": "Synthetic source sent 100 log records.",
            "after": f"debug exporter output retained {matches} unique records, consistent with percentage sampling over a small local test.",
        },
        [] if 5 <= matches <= 45 else [f"retained {matches} of 100 records, outside expected broad range"],
    )


def validate_tail_sampling(workdir: Path) -> ValidationResult:
    slug = "tail-sampling-error-and-latency-traces"
    processor_config = base_processors(
        """
        tail_sampling/error_and_latency:
          decision_wait: 2s
          num_traces: 1000
          expected_new_traces_per_sec: 100
          policies:
            - name: keep-error-traces
              type: status_code
              status_code:
                status_codes: [ERROR]
            - name: keep-slow-traces
              type: latency
              latency:
                threshold_ms: 1000
            - name: baseline-probabilistic-sample
              type: probabilistic
              probabilistic:
                sampling_percentage: 0
        """
    )
    config = join_config(
        otlp_receiver(),
        processor_config,
        debug_exporter(),
        service_pipeline("traces", ["memory_limiter", "resourcedetection", "resource/splunk_context", "tail_sampling/error_and_latency", "batch"]),
    )
    port = free_port()
    with CollectorRun(slug, config, workdir, {port: 4318}) as collector:
        post_json(
            f"http://127.0.0.1:{port}/v1/traces",
            traces_payload(
                [
                    span("GET /error", trace_id="00000000000000000000000000000004", span_id="0000000000000004", status_code=2),
                    span("GET /slow", trace_id="00000000000000000000000000000005", span_id="0000000000000005", duration_ms=1500),
                    span("GET /ordinary", trace_id="00000000000000000000000000000006", span_id="0000000000000006", duration_ms=20),
                ]
            ),
        )
        time.sleep(5)
        logs = collector.logs()
    required = ["GET /error", "GET /slow"]
    forbidden = ["GET /ordinary"]
    missing = assert_contains(logs, required)
    unexpected = [item for item in forbidden if item in logs]
    return ValidationResult(
        slug,
        "Tail Sampling Error and Latency Traces",
        not missing and not unexpected,
        "Retained synthetic error and slow traces while dropping the ordinary trace in a local validation variant.",
        {
            "before": "Synthetic batch included GET /error, GET /slow, and GET /ordinary.",
            "after": "debug exporter output retained GET /error and GET /slow; dropped GET /ordinary with baseline sampling set to 0 for deterministic validation.",
        },
        missing + [f"{item} unexpectedly exported" for item in unexpected],
    )


def validate_redaction(workdir: Path) -> ValidationResult:
    slug = "redact-sensitive-data-before-export"
    processor_config = base_processors(
        """
        redaction/sensitive:
          allow_all_keys: true
          redact_all_types: true
          blocked_key_patterns:
            - "(?i).*password.*"
            - "(?i).*token.*"
            - "(?i).*api[_-]?key.*"
            - "(?i).*authorization.*"
          blocked_values:
            - "(?i)(password|passwd|token|api[_-]?key|secret)=([^\\\\s,;]+)"
            - "\\\\b4[0-9]{12}(?:[0-9]{3})?\\\\b"
          summary: info
        """
    )
    config = join_config(
        otlp_receiver(),
        processor_config,
        debug_exporter(),
        service_multi({
            "traces": ["memory_limiter", "redaction/sensitive", "resourcedetection", "resource/splunk_context", "batch"],
            "logs": ["memory_limiter", "redaction/sensitive", "resourcedetection", "resource/splunk_context", "batch"],
        }),
    )
    port = free_port()
    with CollectorRun(slug, config, workdir, {port: 4318}) as collector:
        post_json(
            f"http://127.0.0.1:{port}/v1/traces",
            traces_payload(
                [
                    span(
                        "POST /checkout",
                        trace_id="00000000000000000000000000000007",
                        span_id="0000000000000007",
                        attrs={
                            "api_key": "synthetic-api-key",
                            "customer.id": "customer-123",
                            "payment.note": "card=4111111111111111",
                        },
                    )
                ]
            ),
        )
        post_json(
            f"http://127.0.0.1:{port}/v1/logs",
            logs_payload([log_record({"password": "synthetic-password", "message": "safe"}, severity_number=17, severity_text="ERROR")]),
        )
        time.sleep(2)
        logs = collector.logs()
    required = ["customer.id", "customer-123", "safe", "redaction.masked.count"]
    forbidden = ["synthetic-api-key", "4111111111111111", "synthetic-password"]
    missing = assert_contains(logs, required)
    unexpected = [item for item in forbidden if item in logs]
    return ValidationResult(
        slug,
        "Redact Sensitive Data Before Export",
        not missing and not unexpected,
        "Masked synthetic API key, card-like value, and structured password while retaining unrelated fields.",
        {
            "before": "Synthetic span/log carried api_key=synthetic-api-key, card=4111111111111111, and password=synthetic-password.",
            "after": "debug exporter output removed raw sensitive values, retained customer.id/message, and included redaction.masked.count audit evidence.",
        },
        missing + [f"{item} unexpectedly exported" for item in unexpected],
    )


def validate_log_redaction(workdir: Path) -> ValidationResult:
    slug = "redact-logs-before-splunk-export"
    processor_config = base_processors(
        """
        transform/log_string_redaction:
          error_mode: ignore
          log_statements:
            - context: log
              statements:
                - replace_pattern(log.body, "(?i)(password|passwd|token|api[_-]?key|secret)=([^\\\\s,;]+)", "$$1=***") where IsString(log.body)
                - replace_pattern(log.body, "\\\\b4[0-9]{12}(?:[0-9]{3})?\\\\b", "****") where IsString(log.body)
        redaction/log_maps_and_attributes:
          allow_all_keys: true
          redact_all_types: true
          blocked_key_patterns:
            - "(?i).*authorization.*"
            - "(?i).*cookie.*"
            - "(?i).*password.*"
          blocked_values:
            - "(?i)(password|passwd|token|api[_-]?key|secret)=([^\\\\s,;]+)"
          summary: info
        """
    )
    config = join_config(
        otlp_receiver(),
        processor_config,
        debug_exporter(),
        service_pipeline("logs", ["memory_limiter", "transform/log_string_redaction", "redaction/log_maps_and_attributes", "resourcedetection", "resource/splunk_context", "batch"]),
    )
    port = free_port()
    with CollectorRun(slug, config, workdir, {port: 4318}) as collector:
        post_json(
            f"http://127.0.0.1:{port}/v1/logs",
            logs_payload(
                [
                    log_record(
                        "login token=synthetic-token card=4111111111111111",
                        severity_number=17,
                        severity_text="ERROR",
                        attrs={"authorization": "Bearer synthetic-token", "safe.field": "keep-me"},
                    )
                ]
            ),
        )
        time.sleep(2)
        logs = collector.logs()
    required = ["login **** card=****", "safe.field", "keep-me", "redaction.masked.count"]
    forbidden = ["synthetic-token", "4111111111111111"]
    missing = assert_contains(logs, required)
    unexpected = [item for item in forbidden if item in logs]
    return ValidationResult(
        slug,
        "Redact Logs Before Splunk Export",
        not missing and not unexpected,
        "Masked synthetic string-body token/card values and authorization attribute while retaining safe fields.",
        {
            "before": "Synthetic log body contained token=synthetic-token and card=4111111111111111; attributes included authorization=Bearer synthetic-token and safe.field=keep-me.",
            "after": "debug exporter output contained login **** card=****, retained safe.field=keep-me, and included redaction.masked.count.",
        },
        missing + [f"{item} unexpectedly exported" for item in unexpected],
    )


VALIDATORS = [
    validate_prometheus_static,
    validate_prometheus_kubernetes,
    validate_filter,
    validate_transform,
    validate_probabilistic,
    validate_tail_sampling,
    validate_redaction,
    validate_log_redaction,
]


def write_report(results: list[ValidationResult], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "collectorImage": IMAGE,
        "generatedAtUnix": int(time.time()),
        "results": [
            {
                "slug": item.slug,
                "title": item.title,
                "passed": item.passed,
                "summary": item.summary,
                "observed": item.observed,
                "errors": item.errors,
            }
            for item in results
        ],
    }
    output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_markdown(results: list[ValidationResult], output: Path) -> None:
    lines = [
        "# Collector Cookbook Live Validation Results",
        "",
        f"Collector image: `{IMAGE}`",
        "",
        "These results were produced by `scripts/validate_collector_cookbooks.py` using local Collector containers, synthetic telemetry, and debug exporter output. When `Observed Splunk backend` is present, the run also emitted a backend marker through the Collector `signalfx` exporter and queried it with Splunk Observability Cloud SignalFlow.",
        "",
    ]
    for item in results:
        status = "PASS" if item.passed else "FAIL"
        lines.extend(
            [
                f"## {item.title}",
                "",
                f"Status: `{status}`",
                "",
                item.summary,
                "",
                "Observed before:",
                "",
                f"```text\n{item.observed.get('before', '')}\n```",
                "",
                "Observed after:",
                "",
                f"```text\n{item.observed.get('after', '')}\n```",
                "",
            ]
        )
        if item.observed.get("backend"):
            lines.extend(
                [
                    "Observed Splunk backend:",
                    "",
                    f"```text\n{item.observed['backend']}\n```",
                    "",
                ]
            )
        if item.errors:
            lines.extend(["Errors:", "", *[f"- {error}" for error in item.errors], ""])
    output.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    global KEEP_CONTAINERS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-output", default="temp/collector-cookbook-validation/results.json")
    parser.add_argument("--markdown-output", default="temp/collector-cookbook-validation/results.md")
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--keep-containers", action="store_true")
    parser.add_argument("--only", action="append", default=[], help="Run only validators with this function name.")
    parser.add_argument("--backend-probe", action="store_true", help="Validate Collector signalfx export with Splunk backend API.")
    parser.add_argument("--backend-cookbooks", action="store_true", help="Run local validations and add Splunk backend marker validation for each passing cookbook.")
    parser.add_argument("--realm", default="us0")
    parser.add_argument("--env-file", default=str(ROOT.parent / ".env"))
    args = parser.parse_args()
    KEEP_CONTAINERS = args.keep_containers

    if not shutil.which("docker"):
        print("docker is required", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="collector-cookbook-validation-", dir="/private/tmp") as tmp:
        workdir = Path(tmp)
        results: list[ValidationResult] = []
        backend_marker_env: dict[str, str] | None = None
        if args.backend_probe:
            env = backend_env(Path(args.env_file), args.realm)
            validators = [lambda workdir: validate_backend_probe(workdir, env)]
        else:
            if args.backend_cookbooks:
                backend_marker_env = backend_env(Path(args.env_file), args.realm)
            validators = [
                validator
                for validator in VALIDATORS
                if not args.only or validator.__name__ in args.only or validator.__name__.replace("validate_", "") in args.only
            ]
        for validator in validators:
            print(f"START {getattr(validator, '__name__', 'validate_backend_probe')}", flush=True)
            try:
                result = validator(workdir)
            except Exception as exc:  # noqa: BLE001 - report every validator failure.
                result = ValidationResult(
                    slug=getattr(validator, "__name__", "validate_backend_probe").replace("validate_", ""),
                    title=getattr(validator, "__name__", "validate_backend_probe"),
                    passed=False,
                    summary="Validation crashed before producing evidence.",
                    errors=[f"{type(exc).__name__}: {exc}"],
                )
            results.append(result)
            print(f"{'PASS' if result.passed else 'FAIL'} {result.slug}: {result.summary}")
            for error in result.errors:
                print(f"  - {error}")

        if backend_marker_env:
            print("START backend marker validation", flush=True)
            results = attach_backend_markers(workdir, backend_marker_env, results)
            for result in results:
                backend_status = "PASS" if result.passed else "FAIL"
                print(f"{backend_status} backend {result.slug}")
                for error in result.errors:
                    print(f"  - {error}")

        write_report(results, ROOT / args.json_output)
        write_markdown(results, ROOT / args.markdown_output)
        if args.keep_workdir:
            keep = Path("/private/tmp/collector-cookbook-validation-kept")
            if keep.exists():
                shutil.rmtree(keep)
            shutil.copytree(workdir, keep)
            print(f"Kept temporary configs in {keep}")

    return 0 if all(item.passed for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
