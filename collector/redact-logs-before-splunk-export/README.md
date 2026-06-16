# Redact Logs Before Splunk Export

## Scenario

Use this recipe when application logs can contain passwords, tokens, API keys, cookies, authorization headers, or credit-card-like values and must be cleaned before Splunk HEC export.

The recipe combines transform-based masking for plain string log bodies with the redaction processor for log attributes and structured log body maps. Do not use it as the only control for sensitive logging; fix producers so they avoid writing secrets.

## Architecture Overview

```text
applications or agents
  -> OTLP logs
  -> transform processor for plain string bodies
  -> redaction processor for attributes and structured body maps
  -> resourcedetection and Splunk context attributes
  -> splunk_hec exporter
  -> Splunk log ingest
```

The transform processor handles string bodies because it can apply `replace_pattern` directly to `log.body`. The redaction processor handles log attributes and documented structured log body map behavior.

## Prerequisites

* Splunk HEC token and HEC URL, for example `https://ingest.<realm>.observability.splunkcloud.com/v1/log`.
* A Collector build that includes the `transform` and `redaction` processors plus the `splunk_hec` exporter.
* A reviewed list of blocked log key patterns and value patterns.
* Synthetic test logs for plain text bodies, structured bodies, and log attributes.
* Agreement on redaction summaries. `summary: info` helps validation but can add diagnostic attributes.

## Installation Instructions

1. Copy [otelcol.yaml](./otelcol.yaml) to the Collector host or gateway.
2. Replace the regex patterns with your approved policy.
3. Export Splunk settings:

   ```bash
   export SPLUNK_HEC_TOKEN='<splunk_hec_token>'
   export SPLUNK_HEC_URL='https://ingest.<realm>.observability.splunkcloud.com/v1/log'
   export DEPLOYMENT_ENVIRONMENT='<environment_name>'
   ```

4. Start the Collector:

   ```bash
   docker run --rm --name splunk-otel-collector \
     -p 4317:4317 \
     -p 4318:4318 \
     -e SPLUNK_CONFIG=/etc/collector/otelcol.yaml \
     -e SPLUNK_HEC_TOKEN \
     -e SPLUNK_HEC_URL \
     -e DEPLOYMENT_ENVIRONMENT \
     -v "$(pwd)/otelcol.yaml:/etc/collector/otelcol.yaml:ro" \
     quay.io/signalfx/splunk-otel-collector:latest
   ```

## Proposed Configuration File

Use [otelcol.yaml](./otelcol.yaml). The logs-only processor chain is:

```yaml
processors:
  transform/log_string_redaction:
    error_mode: ignore
    log_statements:
      - 'replace_pattern(log.body, "(?i)(password|passwd|token|api[_-]?key|secret)=([^\\s,;]+)", "$$1=***") where IsString(log.body)'
  redaction/log_maps_and_attributes:
    allow_all_keys: true
    redact_all_types: true
    blocked_key_patterns:
      - "(?i).*authorization.*"
      - "(?i).*cookie.*"
    summary: info
```

## Validation

### Before Applying

* Use only synthetic sensitive values. In a non-production environment, send a plain text log such as `login token=synthetic-token`, a structured log body map with a synthetic password field, and log attributes containing synthetic `authorization` or `cookie` values.
* Before enabling this logs pipeline, use Splunk logs search to confirm whether those synthetic values are visible in the current pipeline.
* Review current Collector logs for OTLP receiver or `splunk_hec` exporter errors before testing redaction behavior.
* Record unrelated log fields that must remain available for search and incident review.

Expected baseline result:

```text
Logs search: "login token=synthetic-token" is visible when sent through the current pipeline.
Logs search: structured fields such as password=synthetic-password or attributes such as authorization=Bearer synthetic-token are visible if the source emits them.
Collector logs: no transform/log_string_redaction or redaction/log_maps_and_attributes processor is active, or existing OTLP/HEC errors are documented before rollout.
```

### After Applying

* Start the Collector with [otelcol.yaml](./otelcol.yaml) and check logs for configuration, OTTL parse, or evaluation errors involving `transform/log_string_redaction`, and for processor errors involving `redaction/log_maps_and_attributes`.
* Check Collector logs for `splunk_hec` exporter errors before using the Splunk UI result as proof.
* Re-send the synthetic logs. In Splunk logs search, verify the plain string token-like value and card-like test values are masked by the transform processor.
* Verify structured body fields and log attributes that match the blocked key or value patterns are masked or removed before export, while unrelated log fields still arrive with expected resource context such as `service.namespace=redacted-logs`.
* While `summary: info` is enabled, use redaction summary attributes as supporting evidence during validation, then reduce summary verbosity if it is too noisy for production.

Expected post-change result:

```text
Collector logs: transform/log_string_redaction has no OTTL parse errors and splunk_hec has no send failures.
Logs search: "login token=synthetic-token" becomes "login token=***".
Logs search: synthetic card-like values matching the configured patterns become "****".
Logs search: structured body fields or attributes matching blocked key/value patterns are masked or removed; unrelated fields remain searchable.
```

You can sanity-check the string-body OTTL statement with a synthetic log record in an OTTL playground such as `https://ottl.run/`. Expected OTTL behavior:

| Statement | Synthetic input | Expected result |
| --- | --- | --- |
| `replace_pattern(log.body, "(?i)(password|passwd|token|api[_-]?key|secret)=([^\\s,;]+)", "$$1=***") where IsString(log.body)` | `log.body = "login token=synthetic-token"` | `log.body = "login token=***"`. |

### Live Local Validation Result

Validated with `scripts/validate_collector_cookbooks.py` using `quay.io/signalfx/splunk-otel-collector:latest`, a synthetic OTLP log record, and the Collector `debug` exporter. This validates local transform and redaction processor behavior before any Splunk export.

Status: `PASS`

Observed before:

```text
Synthetic log body contained token=synthetic-token and card=4111111111111111; attributes included authorization=Bearer synthetic-token and safe.field=keep-me.
```

Observed after:

```text
debug exporter output contained login **** card=****, retained safe.field=keep-me, and included redaction.masked.count.
```

### Splunk Backend Payload Validation Status

Checked with `scripts/validate_collector_cookbooks.py --backend-cookbooks --realm us0`. The local Collector payload validation passed, but backend payload validation for this signal was not performed in this environment.

```text
Not performed.
This cookbook processes log bodies and log attributes. No SPLUNK_HEC_TOKEN or Splunk log-query endpoint is configured in .env, so I cannot honestly query the ingested log body or log attributes in Splunk.
The local Collector validation above still inspects the actual processed debug-exporter payload, including log/span bodies and attributes.
Backend validation is required; local health alone does not prove ingestion.
```

## Why This Configuration

Plain string log bodies and structured log records need different handling. `replace_pattern(log.body, ...)` is explicit for string bodies. The redaction processor is then used for attributes and structured maps where it has documented support.

The pipeline exports only logs through `splunk_hec`, matching the local logs examples in this backend.

## Troubleshooting

If a string log body is not masked, confirm it is a string and that the regex matches the exact emitted format.

If structured fields are removed unexpectedly, check whether you changed from `allow_all_keys: true` to an `allowed_keys` policy.

If summaries are too noisy, switch `summary` to `silent` after rollout validation.

If logs stop exporting, check `SPLUNK_HEC_TOKEN`, `SPLUNK_HEC_URL`, and Collector exporter errors.

## Scaling Recommendations

Run logs redaction near the source when logs can contain sensitive values. A central gateway is easier to govern but still receives raw logs.

Avoid broad, expensive regex patterns on very high-volume logs. Test CPU impact before production rollout.

Keep a small synthetic log test suite with examples for every blocked pattern.

## Security and Operations Notes

Do not test with real secrets or real payment data. Use synthetic examples that match the same structure.

Masking credit-card-like patterns can produce false positives. Review both misses and over-masking with the application team.

Redaction does not replace Splunk access controls, index controls, or retention policy.

## Configuration Source Basis

This recipe combines two documented processors for a common log problem: plain string bodies require OTTL string replacement through the transform processor, while structured log body maps and log attributes can use the redaction processor's blocked key and blocked value behavior.

The redaction portion is based on the upstream redaction processor README, including `redact_all_types`, blocked key/value patterns, and summary audit attributes. The logs-only exporter pattern follows local Collector log examples that send logs through `splunk_hec`.

## Official Documentation

* [Splunk redaction processor](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/redaction-processor)
* [OpenTelemetry Collector redaction processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/redactionprocessor)
* [OpenTelemetry Collector transform processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor)
* [Splunk guidance for removing data before ingest](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/get-started-understand-and-use-the-collector/remove-data-pre-ingest)
