# Filter Noisy Telemetry Before Export

## Scenario

You already have an OpenTelemetry Collector receiving traces, metrics, and logs and exporting them to Splunk Observability Cloud. In this scenario, you will add a `filter` processor so the Collector drops known low-value telemetry before export.

Use this when platform teams have agreed that specific health-check spans, low-severity logs, runtime metrics, or route-level datapoints are noisy and do not need to be sent to Splunk. Do not use this as a first response to unknown telemetry volume; validate that the dropped data is truly low value and does not hide incident evidence.

What you should capture before changing the Collector:

| Signal | Example before this config | What you see |
| --- | --- | --- |
| Trace span | `GET /health` or `GET /ready` | Health-check spans appear in APM for the service. |
| Metric | `go_memstats_alloc_bytes`, `process_cpu_seconds_total`, `promhttp_metric_handler_requests_total` | Runtime/exporter metrics appear in Metric Finder. |
| Metric datapoint | `http.server.duration{http.route="/health"}` | Health route datapoints are visible. |
| Log | `INFO healthcheck ok` or `DEBUG ready probe passed` | Low-value health logs appear in log search. |

## Architecture Overview

```text
applications, agents, or SDKs
  -> existing Collector OTLP receiver
  -> filter/noise processor
  -> resource detection and Splunk context
  -> existing Splunk exporters
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Collector deployment that already receives OTLP traces, metrics, and logs.
* Access to edit the Collector configuration and restart or roll out the Collector safely.
* Splunk Observability Cloud access token, HEC token, ingest URL, API URL, and HEC URL already available through your normal secret mechanism.
* A reviewed list of span names, metric names, route attributes, and log patterns that are safe to drop.
* Representative non-production telemetry to prove retained data still arrives after the filter is applied.

If your current Collector already defines these values, keep using your existing secret mechanism. Otherwise map these placeholders to your platform's environment variables or secret references:

```bash
export SPLUNK_ACCESS_TOKEN='<splunk_access_token>'
export SPLUNK_HEC_TOKEN='<splunk_hec_token>'
export SPLUNK_API_URL='https://api.<realm>.observability.splunkcloud.com'
export SPLUNK_INGEST_URL='https://ingest.<realm>.observability.splunkcloud.com'
export SPLUNK_HEC_URL='https://ingest.<realm>.observability.splunkcloud.com/v1/log'
export DEPLOYMENT_ENVIRONMENT='<environment_name>'
```

## Installation Instructions

1. Download or copy `otelcol.yaml` from this cookbook and compare it with your current Collector config.
2. Copy the `filter/noise` processor into your existing `processors` block.
3. Add `filter/noise` after `memory_limiter` and before enrichment/export processors in the affected `traces`, `metrics`, and `logs` pipelines.
4. Replace the example OTTL conditions with your approved drop policy.
5. Restart the Collector or roll out the updated config using your existing deployment method.

For host-based Collectors, validate the merged file with your existing Collector binary or service wrapper before restart. For Kubernetes Helm deployments, run a Helm template or diff workflow before applying changes.

## Proposed Configuration File

Download the reusable example file: [otelcol.yaml](./otelcol.yaml).

Use it as a reference or overlay, not as a blind replacement for your production Collector config. Keep your existing receivers, extensions, exporters, resource attributes, and secret references unless this scenario intentionally changes them.

Full example Collector configuration:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 2s
    limit_mib: 512
  filter/noise:
    error_mode: ignore
    trace_conditions:
      - 'IsMatch(span.name, ".*/(health|ready|live|metrics).*")'
    metric_conditions:
      - 'IsMatch(metric.name, "^(go_|process_|promhttp_).*")'
      - 'datapoint.attributes["http.route"] == "/health"'
      - 'datapoint.attributes["http.target"] == "/metrics"'
    log_conditions:
      - 'log.severity_number < SEVERITY_NUMBER_WARN'
      - 'IsMatch(log.body, "(?i).*health(check)?.*")'
  resourcedetection:
    detectors: [env, system]
    override: false
  resource/splunk_context:
    attributes:
      - action: upsert
        key: deployment.environment
        value: "${env:DEPLOYMENT_ENVIRONMENT}"
      - action: upsert
        key: service.namespace
        value: filtered-telemetry
  batch: {}

exporters:
  otlphttp:
    traces_endpoint: "${env:SPLUNK_INGEST_URL}/v2/trace/otlp"
    headers:
      X-SF-Token: "${env:SPLUNK_ACCESS_TOKEN}"
  signalfx:
    access_token: "${env:SPLUNK_ACCESS_TOKEN}"
    api_url: "${env:SPLUNK_API_URL}"
    ingest_url: "${env:SPLUNK_INGEST_URL}"
    sync_host_metadata: true
  splunk_hec:
    token: "${env:SPLUNK_HEC_TOKEN}"
    endpoint: "${env:SPLUNK_HEC_URL}"
    source: otel
    sourcetype: otel
    profiling_data_enabled: false

service:
  telemetry:
    logs:
      level: info
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, filter/noise, resourcedetection, resource/splunk_context, batch]
      exporters: [otlphttp]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, filter/noise, resourcedetection, resource/splunk_context, batch]
      exporters: [signalfx]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, filter/noise, resourcedetection, resource/splunk_context, batch]
      exporters: [splunk_hec]
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Signal | Example before this config | What you see |
| --- | --- | --- |
| Trace span | `GET /health` or `GET /ready` | Health-check spans appear in APM for the service. |
| Metric | `go_memstats_alloc_bytes`, `process_cpu_seconds_total`, `promhttp_metric_handler_requests_total` | Runtime/exporter metrics appear in Metric Finder. |
| Metric datapoint | `http.server.duration{http.route="/health"}` | Health route datapoints are visible. |
| Log | `INFO healthcheck ok` or `DEBUG ready probe passed` | Low-value health logs appear in log search. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Signal | Expected after applying this config | What should remain visible |
| --- | --- | --- |
| Trace span | `GET /health` and `GET /ready` are no longer exported by this Collector. | Error spans and non-health request spans still appear in APM. |
| Metric | Metrics matching `go_*`, `process_*`, and `promhttp_*` are dropped by this Collector. | Application metrics outside the allow/drop rules still appear. |
| Metric datapoint | Datapoints with `http.route="/health"` are dropped. | Business or service-level routes still appear. |
| Log | Low-severity or health-check logs are not exported. | Warning and error logs from the same service still appear. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

Filtering at the Collector reduces export volume before data leaves your environment and keeps the drop policy centralized. The tradeoff is operational risk: overly broad OTTL conditions can hide useful telemetry, so start narrow and validate retained signals.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Collector fails to start | Check Collector logs for OTTL parse errors in `filter/noise`. | Fix the condition syntax and test with non-sensitive samples before rollout. |
| Expected telemetry is missing | Temporarily remove one condition or test it in isolation. | Narrow broad regexes and add explicit exceptions where needed. |
| No volume change | Confirm the pipeline includes `filter/noise` before exporters. | Add the processor to every affected signal pipeline. |

## Scaling Recommendations

* Keep filter rules narrow and owned by service/platform teams.
* Track dropped versus retained volume during rollout using Collector and Splunk-side telemetry.
* Apply the same filter policy consistently across gateway replicas or node agents that handle the same services.

## Security and Operations Notes

* Do not use filtering as the only protection for sensitive data; producers should avoid emitting secrets.
* Review drop rules with incident responders before removing telemetry classes.
* Use placeholders for Splunk tokens and keep credentials in your existing secret manager.

## Configuration Source Basis

This cookbook adapts the existing Collector config in `otelcol.yaml`, the OpenTelemetry Collector filter processor pattern, and Splunk OpenTelemetry Collector exporter patterns already used in this backend repository.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/filterprocessor
