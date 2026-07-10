# Redact Sensitive Data Before Export

## Scenario

You already have a Collector receiving traces, metrics, and logs and exporting them to Splunk Observability Cloud. In this scenario, you will add a redaction processor to mask sensitive-looking attributes and structured log fields before export.

Use this when telemetry can include passwords, tokens, API keys, authorization headers, cookies, or credit-card-like values. Do not treat this as a compliance guarantee; producers should still avoid emitting sensitive data.

What you should capture before changing the Collector:

| Signal | Example before this config | Risk |
| --- | --- | --- |
| Trace span attribute | `http.request.header.authorization=Bearer synthetic-token` | Header value can appear in APM span metadata. |
| Metric datapoint attribute | `api_key=synthetic-key` | Sensitive label can create risky metric dimensions. |
| Log attribute/body map | `password=synthetic-password` | Secret-like value can appear in logs. |

## Architecture Overview

```text
applications, agents, or SDKs
  -> existing Collector OTLP receiver
  -> redaction/sensitive processor
  -> resource detection and Splunk context
  -> existing Splunk trace, metrics, and log exporters
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Collector deployment that already receives the signals you want to protect.
* Working Splunk exporters for traces, metrics, and logs as needed by your environment.
* Access to edit the Collector configuration and restart or roll out the Collector safely.
* A reviewed list of blocked key and value patterns.
* Synthetic sensitive-looking test values for validation.

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
2. Copy `redaction/sensitive` into your existing `processors` block.
3. Add `redaction/sensitive` after `memory_limiter` and before enrichment/export processors in each signal pipeline that needs protection.
4. Replace blocked key/value patterns with your approved policy.
5. Restart or roll out the Collector and send synthetic trace, metric, and log examples through it.

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
  redaction/sensitive:
    allow_all_keys: true
    redact_all_types: true
    blocked_key_patterns:
      - "(?i).*password.*"
      - "(?i).*passwd.*"
      - "(?i).*secret.*"
      - "(?i).*token.*"
      - "(?i).*api[_-]?key.*"
      - "(?i).*authorization.*"
      - "(?i).*cookie.*"
    blocked_values:
      - "(?i)(password|passwd|token|api[_-]?key|secret)=([^\\s,;]+)"
      - "\\b4[0-9]{12}(?:[0-9]{3})?\\b"
      - "\\b5[1-5][0-9]{14}\\b"
    summary: info
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
        value: redacted-telemetry
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
      processors: [memory_limiter, redaction/sensitive, resourcedetection, resource/splunk_context, batch]
      exporters: [otlphttp]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, redaction/sensitive, resourcedetection, resource/splunk_context, batch]
      exporters: [signalfx]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, redaction/sensitive, resourcedetection, resource/splunk_context, batch]
      exporters: [splunk_hec]
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Signal | Example before this config | Risk |
| --- | --- | --- |
| Trace span attribute | `http.request.header.authorization=Bearer synthetic-token` | Header value can appear in APM span metadata. |
| Metric datapoint attribute | `api_key=synthetic-key` | Sensitive label can create risky metric dimensions. |
| Log attribute/body map | `password=synthetic-password` | Secret-like value can appear in logs. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Signal | Expected after applying this config | Validation target |
| --- | --- | --- |
| Trace span attribute | Original synthetic token/header value is not visible in APM span metadata. | The value is redacted or absent according to processor behavior. |
| Metric datapoint attribute | Original sensitive-looking label value is not exported as-is. | Metric dimensions do not contain the synthetic secret. |
| Log attribute/body map | Original password/token value is not visible in log search. | Redaction summary behavior follows the processor settings. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

Centralized redaction reduces the chance that accidental sensitive attributes reach Splunk. The tradeoff is that regex-based redaction can miss unexpected formats and can also redact useful data if patterns are too broad.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Synthetic value still appears | Check whether the signal pipeline includes `redaction/sensitive` before export. | Add the processor to the correct pipeline and adjust key/value patterns. |
| Useful attributes disappear | Review broad blocked key patterns. | Narrow regexes or move to an explicit allow-list policy after review. |
| Collector CPU increases | Check regex complexity and telemetry volume. | Reduce broad value patterns and test on representative traffic. |

## Scaling Recommendations

* Start with the highest-risk keys and values and expand gradually.
* Avoid using high-cardinality customer identifiers in validation examples.
* Review redaction rules as application schemas change.

## Security and Operations Notes

* Never validate with real customer secrets.
* Keep Splunk tokens in environment variables or secret managers.
* Document the remaining risk: redaction is not a substitute for source-side data minimization.

## Configuration Source Basis

This cookbook adapts the local `otelcol.yaml` example, OpenTelemetry redaction processor behavior, and Splunk exporter patterns used in the examples backend.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/redactionprocessor
