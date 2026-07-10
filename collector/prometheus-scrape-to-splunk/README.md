# Prometheus Scraping to Splunk

## Scenario

You already have a Collector host or gateway that can reach Prometheus-format `/metrics` endpoints. In this scenario, you will add a Prometheus receiver scrape job and export the allowed metrics to Splunk Observability Cloud.

Use this for application endpoints, exporters, or appliances that expose Prometheus metrics. Do not use this when you need Prometheus alert rules, remote read, or remote write inside the Collector.

What you should capture before changing the Collector:

| Target | Example before this config | What you see |
| --- | --- | --- |
| App metrics endpoint | `app-1.example.internal:8080/metrics` returns `http_server_requests_total` | Metric is not present in Splunk because nothing scrapes it. |
| HTTPS appliance | Appliance exposes `appliance_errors_total` with bearer auth | Metric is absent or only visible in a separate Prometheus deployment. |
| Collector logs | No `prometheus/static_targets` receiver | No scrape activity for these targets. |

## Architecture Overview

```text
Prometheus /metrics targets
  -> existing Collector prometheus receiver
  -> metric_relabel_configs allow-list
  -> resource detection and Splunk context
  -> signalfx exporter
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Collector deployment with network access to the scrape targets.
* Access to edit the Collector configuration and restart or roll out the Collector safely.
* Splunk Observability Cloud access token, ingest URL, and API URL already available through your secret mechanism.
* Approved target hostnames, metrics paths, authentication files, and CA files.
* A reviewed allow-list of metric names to keep.

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

1. Download or copy `otelcol.yaml` and compare it with your current Collector config.
2. Copy the `prometheus/static_targets` receiver into your existing `receivers` block.
3. Replace example targets, bearer token file paths, CA paths, and `metric_relabel_configs` with your approved values.
4. Add the receiver to a metrics pipeline that includes `memory_limiter`, resource enrichment, `batch`, and your Splunk metrics exporter.
5. Restart or roll out the Collector and verify scrape activity in Collector logs.

For host-based Collectors, validate the merged file with your existing Collector binary or service wrapper before restart. For Kubernetes Helm deployments, run a Helm template or diff workflow before applying changes.

## Proposed Configuration File

Download the reusable example file: [otelcol.yaml](./otelcol.yaml).

Use it as a reference or overlay, not as a blind replacement for your production Collector config. Keep your existing receivers, extensions, exporters, resource attributes, and secret references unless this scenario intentionally changes them.

Full example Collector configuration:

```yaml
receivers:
  prometheus/static_targets:
    config:
      scrape_configs:
        - job_name: app-metrics
          scrape_interval: 30s
          scrape_timeout: 10s
          metrics_path: /metrics
          static_configs:
            - targets:
                - app-1.example.internal:8080
                - app-2.example.internal:8080
          metric_relabel_configs:
            - source_labels: [__name__]
              regex: "(http_server_request_duration_seconds.*|http_server_requests_total|process_cpu_seconds_total|process_resident_memory_bytes)"
              action: keep
        - job_name: https-appliance
          scheme: https
          scrape_interval: 60s
          scrape_timeout: 15s
          metrics_path: /metrics
          authorization:
            type: Bearer
            credentials_file: /etc/collector/secrets/prometheus_bearer_token
          tls_config:
            ca_file: /etc/collector/certs/appliance-ca.pem
            server_name: appliance.example.internal
            insecure_skip_verify: false
          static_configs:
            - targets:
                - appliance.example.internal:443
          metric_relabel_configs:
            - source_labels: [__name__]
              regex: "(appliance_requests_total|appliance_errors_total|appliance_latency_seconds.*)"
              action: keep

processors:
  memory_limiter:
    check_interval: 2s
    limit_mib: 512
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
        value: prometheus-scrapes
  batch: {}

exporters:
  signalfx:
    access_token: "${env:SPLUNK_ACCESS_TOKEN}"
    api_url: "${env:SPLUNK_API_URL}"
    ingest_url: "${env:SPLUNK_INGEST_URL}"
    sync_host_metadata: true

service:
  telemetry:
    logs:
      level: info
  pipelines:
    metrics:
      receivers: [prometheus/static_targets]
      processors: [memory_limiter, resourcedetection, resource/splunk_context, batch]
      exporters: [signalfx]
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Target | Example before this config | What you see |
| --- | --- | --- |
| App metrics endpoint | `app-1.example.internal:8080/metrics` returns `http_server_requests_total` | Metric is not present in Splunk because nothing scrapes it. |
| HTTPS appliance | Appliance exposes `appliance_errors_total` with bearer auth | Metric is absent or only visible in a separate Prometheus deployment. |
| Collector logs | No `prometheus/static_targets` receiver | No scrape activity for these targets. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Target | Expected after applying this config | How to validate |
| --- | --- | --- |
| App metrics endpoint | `http_server_requests_total` and request-duration metrics appear in Splunk. | Search Metric Finder for the metric names and target labels. |
| HTTPS appliance | Allowed `appliance_*` metrics appear after authentication succeeds. | Check Collector logs for scrape/export errors and Metric Finder for appliance metrics. |
| Non-allowed metrics | Metrics outside `metric_relabel_configs` are dropped before export. | Confirm noisy or unwanted metric names are absent. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

Putting Prometheus scraping in the Collector centralizes Splunk credentials and resource metadata while letting application teams keep their existing Prometheus endpoints. The allow-list reduces cardinality and avoids exporting accidental metrics.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Scrape fails | Check Collector logs for target connection, TLS, or authentication errors. | Fix target address, CA file, bearer token file, or metrics path. |
| Metrics scrape but do not appear | Check exporter endpoint/token settings and Metric Finder. | Verify Splunk environment variables and exporter configuration. |
| Too many series appear | Review `metric_relabel_configs` and labels. | Narrow metric names and drop high-cardinality labels at the source or receiver. |

## Scaling Recommendations

* Group targets by scrape interval and ownership.
* Avoid very short scrape intervals until Collector CPU and target load are measured.
* Watch metric cardinality when adding target labels or scraping many pods/instances.

## Security and Operations Notes

* Keep bearer tokens and CA files mounted as secrets, not committed to the repo.
* Do not set `insecure_skip_verify: true` for production targets without an explicit exception.
* Avoid scraping endpoints that expose sensitive labels or values.

## Configuration Source Basis

This cookbook adapts the local `otelcol.yaml` example, Splunk metrics exporter patterns, and the OpenTelemetry Prometheus receiver into an existing Collector configuration workflow.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver
