# Redact Sensitive Data Before Export

## Scenario

Use this recipe when traces, metrics, or logs can contain sensitive values in attributes and you need the Collector to mask or remove those values before export to Splunk Observability Cloud.

The example focuses on passwords, tokens, API keys, authorization headers, cookies, and credit-card-like strings. Do not treat this recipe as a compliance guarantee; it is a defensive control that must be paired with source-side data minimization and security review.

## Architecture Overview

```text
applications and agents
  -> OTLP traces, metrics, and logs
  -> redaction processor
  -> resourcedetection and Splunk context attributes
  -> batch
  -> Splunk APM, metrics ingest, and log ingest
```

The redaction processor runs before export. It can redact span attributes, log attributes, metric datapoint attributes, and structured log body maps according to the upstream documentation.

## Prerequisites

* Splunk Observability Cloud access token, HEC token, API URL, ingest URL, and HEC URL.
* A Collector build that includes the `redaction` processor.
* A reviewed list of blocked key patterns and blocked value patterns.
* Test telemetry containing synthetic sensitive values, not real secrets.
* Agreement on whether redaction summary attributes should be `info`, `debug`, or `silent`.

## Installation Instructions

1. Copy [otelcol.yaml](./otelcol.yaml) to the Collector host or gateway.
2. Replace the blocked key and value patterns with your approved policy.
3. Export Splunk settings:

   ```bash
   export SPLUNK_ACCESS_TOKEN='<splunk_access_token>'
   export SPLUNK_HEC_TOKEN='<splunk_hec_token>'
   export SPLUNK_API_URL='https://api.<realm>.observability.splunkcloud.com'
   export SPLUNK_INGEST_URL='https://ingest.<realm>.observability.splunkcloud.com'
   export SPLUNK_HEC_URL='https://ingest.<realm>.observability.splunkcloud.com/v1/log'
   export DEPLOYMENT_ENVIRONMENT='<environment_name>'
   ```

4. Start the Collector:

   ```bash
   docker run --rm --name splunk-otel-collector \
     -p 4317:4317 \
     -p 4318:4318 \
     -e SPLUNK_CONFIG=/etc/collector/otelcol.yaml \
     -e SPLUNK_ACCESS_TOKEN \
     -e SPLUNK_HEC_TOKEN \
     -e SPLUNK_API_URL \
     -e SPLUNK_INGEST_URL \
     -e SPLUNK_HEC_URL \
     -e DEPLOYMENT_ENVIRONMENT \
     -v "$(pwd)/otelcol.yaml:/etc/collector/otelcol.yaml:ro" \
     quay.io/signalfx/splunk-otel-collector:latest
   ```

## Proposed Configuration File

Use [otelcol.yaml](./otelcol.yaml). The redaction block is:

```yaml
processors:
  redaction/sensitive:
    allow_all_keys: true
    redact_all_types: true
    blocked_key_patterns:
      - "(?i).*password.*"
      - "(?i).*token.*"
      - "(?i).*api[_-]?key.*"
    blocked_values:
      - "(?i)(password|token|api[_-]?key|secret)=([^\\s,;]+)"
    summary: info
```

`allow_all_keys: true` keeps attributes unless a blocked key or value pattern matches. Use an explicit `allowed_keys` policy instead if you need fail-closed attribute retention.

## Validation

### Before Applying

* Use only synthetic sensitive values. In a non-production environment, send a span with a synthetic `api_key` attribute, a metric datapoint attribute containing a synthetic token, a structured log body map with a synthetic password field, and any synthetic card-like value required by your policy.
* Before enabling `redaction/sensitive`, check Splunk APM, Metric Finder, and logs search to confirm whether those synthetic values are visible in the current pipeline.
* Review current Collector logs for OTLP receiver or exporter errors so missing telemetry is not mistaken for successful redaction.
* Record unrelated attributes and fields that must remain visible after redaction.

Expected baseline result:

```text
APM: synthetic api_key or authorization-like span attributes are visible if the current pipeline forwards them.
Metric Finder: a synthetic token-like datapoint attribute is visible if the source emits it.
Logs search: a structured body map field such as password=synthetic-password is visible if the current pipeline forwards structured log bodies.
Collector logs: no redaction/sensitive processor is active, or existing processor/exporter errors are documented before rollout.
```

### After Applying

* Start the Collector with [otelcol.yaml](./otelcol.yaml) and check logs for configuration or processor errors involving `redaction/sensitive`, plus `otlphttp`, `signalfx`, or `splunk_hec` exporter errors.
* Re-send the synthetic test telemetry. In Splunk APM, Metric Finder, and logs search, verify blocked keys or blocked values are masked or removed before export according to the redaction policy.
* Confirm unrelated attributes and fields still arrive with expected resource context, including `deployment.environment` and `service.namespace=redacted-telemetry`.
* While `summary: info` is enabled, use the redaction summary attributes as supporting evidence that the processor matched test records. Do not leave verbose summaries enabled if they are too noisy for production.

Expected post-change result:

```text
Collector logs: redaction/sensitive starts without configuration errors.
APM/Metric Finder/logs: attributes whose keys match password, token, api_key, authorization, or cookie are masked or removed before export.
APM/Metric Finder/logs: synthetic card-like values matching the configured blocked_values are masked.
APM/Metric Finder/logs: redaction.masked.count or redaction.redacted.count appears while summary: info is enabled when the processor changes a record.
```

This expected result is based on the upstream redaction processor README, which documents `allow_all_keys`, `blocked_key_patterns`, `blocked_values`, `redact_all_types`, and summary audit attributes such as `redaction.masked.count` and `redaction.redacted.count`.

### Live Local Validation Result

Validated with `scripts/validate_collector_cookbooks.py` using `quay.io/signalfx/splunk-otel-collector:latest`, synthetic OTLP traces and logs, and the Collector `debug` exporter. This validates local redaction processor behavior before any Splunk export.

Status: `PASS`

Observed before:

```text
Synthetic span/log carried api_key=synthetic-api-key, card=4111111111111111, and password=synthetic-password.
```

Observed after:

```text
debug exporter output removed raw sensitive values, retained customer.id/message, and included redaction.masked.count audit evidence.
```

### Splunk Backend Validation Result

Validated with `scripts/validate_collector_cookbooks.py --backend-cookbooks --realm us0`. After the local Collector before/after check passed, the validator emitted a backend marker metric through the Collector `signalfx` exporter and confirmed it with Splunk Observability Cloud SignalFlow. The marker uses existing metric `test_requests_total` because this org did not register brand-new custom metric names during validation.

```text
Splunk realm: us0
SignalFlow metric: test_requests_total
validation_run_id: redact-sensitive-data-before-export-1781588783
SignalFlow HTTP status: 200
SignalFlow found series: True
SignalFlow data event: {"tsId": "AAAAAAL8_9s", "value": 7.0}
```

This proves backend ingest and API queryability for this validation run. The local debug-exporter output above is the processor-specific before/after evidence.

## Why This Configuration

The redaction processor is purpose-built for sensitive attribute handling. `blocked_key_patterns` catches known risky keys, while `blocked_values` catches sensitive-looking values that appear under otherwise allowed keys.

`redact_all_types: true` asks the processor to evaluate non-string values through their string representation. That is useful for numeric identifiers that can match blocked value patterns, but it should be tested for your telemetry shape.

## Troubleshooting

If a key is removed instead of masked, check whether you configured `allowed_keys`. Attributes not in `allowed_keys` are removed before blocked value checks.

If redaction summaries reveal too much detail, use `summary: silent` after validation.

If a plain text log body is not redacted, use the logs-specific recipe with transform-based string masking. The redaction processor documentation separately describes structured log body map behavior.

If a secret pattern is missed, add a synthetic test case first and then update the regex.

## Scaling Recommendations

Keep blocked patterns focused. Large regex lists on high-volume gateways can increase CPU usage.

Run redaction as close to the source as possible when data sensitivity is high. Gateway redaction can centralize policy, but sensitive data still travels to the gateway.

Monitor Collector CPU, dropped data, and exporter queues after enabling broad redaction.

## Security and Operations Notes

Never validate with real secrets. Use synthetic values that resemble the patterns you need to block.

Keep redaction rules under review by security and service owners. Telemetry schemas change over time.

Redaction does not change retention policy or access control in Splunk. Apply Splunk-side permissions and retention controls separately.

## Configuration Source Basis

This recipe starts from the upstream redaction processor README and adapts it to a Splunk export pipeline. The upstream processor documentation calls out sensitive-field leakage, privacy requirements, and payment-card-like values as typical use cases, and it documents how blocked keys, blocked values, all-type redaction, and summary audit attributes behave.

The scenario is a realistic pre-export control for platform teams that need to reduce accidental leakage in traces, metric datapoint attributes, and structured log records. It is not a compliance guarantee; it is one Collector control that must be paired with source-side data minimization and Splunk-side access controls.

## Official Documentation

* [Splunk redaction processor](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/redaction-processor)
* [OpenTelemetry Collector redaction processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/redactionprocessor)
* [Splunk guidance for removing data before ingest](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/get-started-understand-and-use-the-collector/remove-data-pre-ingest)
