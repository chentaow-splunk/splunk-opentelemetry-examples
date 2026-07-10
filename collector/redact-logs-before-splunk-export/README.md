# Redact Logs Before Splunk Export

## Scenario

You already have a Collector receiving logs and exporting them to Splunk through HEC. In this scenario, you will add log redaction processors before the HEC exporter so synthetic secret-like values are masked before export.

Use this when logs can include passwords, tokens, API keys, cookies, authorization headers, or credit-card-like values. Do not use this as the only control; application teams should still avoid writing secrets to logs.

What you should capture before changing the Collector:

| Log shape | Example before this config | Risk |
| --- | --- | --- |
| Plain text body | `login token=synthetic-token user=demo` | Synthetic token-like value appears in log search. |
| Structured body map | `{ "password": "synthetic-password" }` | Secret-like key/value can be exported. |
| Attributes | `authorization=Bearer synthetic-token` | Sensitive header value can be exported. |

## Architecture Overview

```text
applications or log agents
  -> existing Collector OTLP logs receiver
  -> transform/log_string_redaction for string bodies
  -> redaction/log_maps_and_attributes for attributes and structured maps
  -> Splunk HEC exporter
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Collector deployment that already receives logs.
* A working Splunk HEC exporter configuration and HEC token managed outside the config file.
* Access to edit the Collector configuration and restart or roll out the Collector safely.
* A reviewed list of blocked keys and value patterns.
* Synthetic test logs only; do not test with real secrets.

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

1. Download or copy `otelcol.yaml` and compare it with your current logs pipeline.
2. Copy `transform/log_string_redaction` and `redaction/log_maps_and_attributes` into your existing `processors` block.
3. Place the processors after `memory_limiter` and before enrichment/export processors in the logs pipeline.
4. Replace regexes with your approved policy and keep synthetic examples in tests.
5. Restart or roll out the Collector and send synthetic logs through the same path as application logs.

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
  transform/log_string_redaction:
    error_mode: ignore
    log_statements:
      - 'replace_pattern(log.body, "(?i)(password|passwd|token|api[_-]?key|secret)=([^\\s,;]+)", "$$1=***") where IsString(log.body)'
      - 'replace_pattern(log.body, "\\b4[0-9]{12}(?:[0-9]{3})?\\b", "****") where IsString(log.body)'
      - 'replace_pattern(log.body, "\\b5[1-5][0-9]{14}\\b", "****") where IsString(log.body)'
  redaction/log_maps_and_attributes:
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
        value: redacted-logs
  batch: {}

exporters:
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
    logs:
      receivers: [otlp]
      processors: [memory_limiter, transform/log_string_redaction, redaction/log_maps_and_attributes, resourcedetection, resource/splunk_context, batch]
      exporters: [splunk_hec]
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Log shape | Example before this config | Risk |
| --- | --- | --- |
| Plain text body | `login token=synthetic-token user=demo` | Synthetic token-like value appears in log search. |
| Structured body map | `{ "password": "synthetic-password" }` | Secret-like key/value can be exported. |
| Attributes | `authorization=Bearer synthetic-token` | Sensitive header value can be exported. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Log shape | Expected after applying this config | Validation target |
| --- | --- | --- |
| Plain text body | `login token=*** user=demo` | The original `synthetic-token` string is absent. |
| Credit-card-like value | Value matching the example card regex is replaced with `****`. | The original synthetic number is absent. |
| Structured body or attributes | Matching keys or values are redacted by the redaction processor. | The original synthetic password/header value is absent; redaction summary behavior follows processor settings. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

The transform processor can mask plain string log bodies, while the redaction processor covers attributes and structured body maps. Combining both gives better log coverage than either one alone.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Plain text logs are not masked | Check that `transform/log_string_redaction` is in the logs pipeline before export. | Fix pipeline order and regex syntax. |
| Structured fields are not redacted | Check blocked key/value patterns in `redaction/log_maps_and_attributes`. | Add key patterns for the structured field names you actually emit. |
| Collector reports processor errors | Check logs for regex or OTTL errors. | Test with synthetic examples and simplify patterns. |

## Scaling Recommendations

* Keep regexes targeted; expensive broad patterns can increase CPU cost.
* Start with high-risk keys and expand after reviewing real log schemas.
* Monitor Collector CPU and dropped/refused log counters after rollout.

## Security and Operations Notes

* Use synthetic secrets for validation; never send real tokens to prove redaction.
* Keep HEC tokens in your platform secret store.
* Treat redaction as a defense-in-depth control, not a compliance guarantee.

## Configuration Source Basis

This cookbook adapts the local `otelcol.yaml` example, transform processor body masking, redaction processor attribute/body-map behavior, and Splunk HEC exporter patterns.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/redactionprocessor
