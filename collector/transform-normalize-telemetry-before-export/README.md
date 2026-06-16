# Transform and Normalize Telemetry Before Export

## Scenario

Use this recipe when telemetry is valuable but needs normalization or light redaction before export to Splunk Observability Cloud.

The example sets missing resource attributes, deletes known-sensitive keys, normalizes metric names, removes high-cardinality attributes, truncates oversized attribute values, and masks known key-value patterns in string fields. Do not use this recipe as a replacement for application-side data hygiene; producers should still avoid emitting secrets.

## Architecture Overview

```text
applications and agents
  -> OTLP traces, metrics, and logs
  -> transform processor using OTTL statements
  -> resourcedetection and Splunk context attributes
  -> batch
  -> Splunk APM, metrics ingest, and log ingest
```

The transform processor mutates telemetry in place. Each signal uses OTTL statements valid for that signal context.

## Prerequisites

* Splunk Observability Cloud access token, HEC token, API URL, ingest URL, and HEC URL.
* A Collector build that includes the `transform` processor.
* A Collector version whose transform processor supports the documented `trace_statements`, `metric_statements`, and `log_statements` syntax.
* A reviewed list of attributes that must be deleted, preserved, truncated, or normalized.
* Representative test telemetry for every statement in [otelcol.yaml](./otelcol.yaml).

## Installation Instructions

1. Copy [otelcol.yaml](./otelcol.yaml) to the Collector host or gateway.
2. Replace the example namespace, attribute keys, regexes, limits, and metric rename rule with your approved policy.
3. Export Splunk settings:

   ```bash
   export SPLUNK_ACCESS_TOKEN='<splunk_access_token>'
   export SPLUNK_HEC_TOKEN='<splunk_hec_token>'
   export SPLUNK_API_URL='https://api.<realm>.observability.splunkcloud.com'
   export SPLUNK_INGEST_URL='https://ingest.<realm>.observability.splunkcloud.com'
   export SPLUNK_HEC_URL='https://ingest.<realm>.observability.splunkcloud.com/v1/log'
   export DEPLOYMENT_ENVIRONMENT='<environment_name>'
   ```

4. Run the Collector with the config:

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

Pin the Collector version once the OTTL behavior is validated.

## Proposed Configuration File

Use [otelcol.yaml](./otelcol.yaml). The central pattern is:

```yaml
processors:
  transform/normalize:
    error_mode: ignore
    trace_statements:
      - 'delete_key(span.attributes, "http.request.header.authorization")'
      - 'truncate_all(span.attributes, 2048)'
      - 'limit(span.attributes, 128, ["http.method", "http.route", "http.status_code"])'
    log_statements:
      - 'replace_pattern(log.body, "(?i)(password|token|api[_-]?key)=([^\\s]+)", "$$1=***") where IsString(log.body)'
```

The `$$1` escape is intentional in Collector YAML because `$` is also used for environment substitution.

## Validation

### Before Applying

* In a non-production environment, send representative telemetry that exercises each planned transform: a span with synthetic `http.request.header.authorization` and `http.request.header.cookie` attributes, a span with a synthetic sensitive value in `db.statement`, a metric name matching the `kube_...` rename rule, and a log body containing a synthetic token-like value.
* Use only synthetic sensitive values. Before enabling `transform/normalize`, confirm in Splunk APM, Metric Finder, and logs search whether those raw attributes, metric names, and log bodies are currently visible.
* Check current Collector logs for OTLP receiver or exporter errors before testing transforms.
* Record any required priority attributes, such as `http.method`, `http.route`, `http.status_code`, `service.name`, `k8s.namespace.name`, or `deployment.environment`, so you can verify they survive the `limit` statements.

Expected baseline result:

```text
APM: synthetic http.request.header.authorization or http.request.header.cookie attributes are visible when sent.
APM/logs: synthetic token-like strings are visible in db.statement or log bodies.
Metric Finder: the test metric still uses the original unnormalized name and high-cardinality attributes such as pod_uid or container_id may be present.
Collector logs: no transform/normalize processor is active, or existing transform errors are documented before rollout.
```

### After Applying

* Start the Collector with [otelcol.yaml](./otelcol.yaml) and check logs for configuration, OTTL parse, or evaluation errors involving `transform/normalize`, plus any exporter errors.
* In Splunk APM, verify the synthetic authorization and cookie attributes are removed and the synthetic sensitive value in `db.statement` is masked before export.
* In Metric Finder, verify the test metric matching the rename rule appears under the configured normalized name rather than the original name, and that high-cardinality attributes such as `pod_uid` or `container_id` are not present on the exported datapoints.
* In logs search, verify the synthetic token-like value is masked while unrelated log fields and priority attributes still arrive.
* Confirm missing resource context is set or upserted as expected, including `deployment.environment` and `service.namespace=normalized-telemetry`.

Expected post-change result:

```text
Collector logs: no OTTL parse or evaluation errors for transform/normalize.
APM: http.request.header.authorization and http.request.header.cookie are absent from spans exported by this Collector.
Logs search: "token=synthetic-token" becomes "token=***" while unrelated log text remains searchable.
Metric Finder: renamed test metrics and priority dimensions remain; pod_uid/container_id dimensions do not appear from this pipeline.
```

You can sanity-check the OTTL statements with synthetic records in an OTTL playground such as `https://ottl.run/`. Use non-sensitive samples only. Expected OTTL behavior:

| Statement | Synthetic input | Expected result |
| --- | --- | --- |
| `delete_key(span.attributes, "http.request.header.authorization")` | span attribute exists | key is removed. |
| `replace_pattern(log.body, "(?i)(password|token|api[_-]?key)=([^\\s]+)", "$$1=***") where IsString(log.body)` | `log.body = "login token=synthetic-token"` | `log.body = "login token=***"`. |
| `limit(datapoint.attributes, 64, ["service.name", "k8s.namespace.name", "k8s.pod.name"])` | datapoint has many attributes | priority attributes are retained while excess attributes can be removed. |

### Live Local Validation Result

Validated with `scripts/validate_collector_cookbooks.py` using `quay.io/signalfx/splunk-otel-collector:latest`, synthetic OTLP traces and logs, and the Collector `debug` exporter. This validates local transform processor behavior before any Splunk export.

Status: `PASS`

Observed before:

```text
Synthetic span/log contained Bearer synthetic-token, session=synthetic, password='synthetic-secret', and login token=synthetic-token.
```

Observed after:

```text
debug exporter output contained password='***' and login token=***; removed authorization/cookie attributes and raw secret values.
```

### Splunk Backend Validation Result

Validated with `scripts/validate_collector_cookbooks.py --backend-cookbooks --realm us0`. After the local Collector before/after check passed, the validator emitted a backend marker metric through the Collector `signalfx` exporter and confirmed it with Splunk Observability Cloud SignalFlow. The marker uses existing metric `test_requests_total` because this org did not register brand-new custom metric names during validation.

```text
Splunk realm: us0
SignalFlow metric: test_requests_total
validation_run_id: transform-normalize-telemetry-before-export-1781588783
SignalFlow HTTP status: 200
SignalFlow found series: True
SignalFlow data event: {"tsId": "AAAAAIWda7Y", "value": 4.0}
```

This proves backend ingest and API queryability for this validation run. The local debug-exporter output above is the processor-specific before/after evidence.

## Why This Configuration

The transform processor is useful when the telemetry should remain available but needs shape changes before export. `delete_key`, `replace_pattern`, `truncate_all`, and `limit` are documented OTTL editor functions and are scoped to signal-specific contexts.

`error_mode: ignore` prevents one malformed field from dropping an entire batch. That does not make invalid statements safe; it only keeps the Collector resilient while the error is logged.

## Troubleshooting

If a statement does not run, verify that the path is valid for the signal context. For example, `span.attributes` belongs in `trace_statements`, not `log_statements`.

If a replacement string emits a literal `$1`, check escaping. Collector YAML requires `$$1` for regex capture replacement.

If too many attributes disappear, reduce `limit` usage or expand the priority key list.

If a string body is not masked, confirm the body is actually a string. Structured log bodies need map-focused statements or the redaction processor.

## Scaling Recommendations

Keep transform statements specific and cheap. Regex-heavy rules on high-volume logs can become a Collector CPU bottleneck.

Apply broad normalization at gateways only when services share the same policy. Service-specific fixes are usually safer near the source.

Monitor Collector CPU, memory, dropped data, and exporter queue metrics after adding transform rules.

## Security and Operations Notes

Use transform rules as a guardrail, not as the only control for secret handling. Application teams should remove secrets before telemetry leaves the process.

Review regular expressions carefully. Overly broad masking can remove values needed for incident response, while narrow patterns can miss real secrets.

Keep a test payload for each rule in version control or CI so future Collector upgrades do not silently change behavior.

## Configuration Source Basis

This recipe builds on the upstream transform processor and OTTL editor functions for common normalization work: deleting unsafe header attributes, masking known key-value patterns, truncating large values, limiting attribute maps, and normalizing metric names. Those are real-world gateway policies used when telemetry should be retained but reshaped before export.

The exporter and resource-enrichment pattern follows existing local Collector examples in this backend. The actual statements should be treated as a starter policy and validated with synthetic payloads that match the service's real telemetry schema.

## Official Documentation

* [Splunk transform processor](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/transform-processor)
* [OpenTelemetry Collector transform processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor)
* [OpenTelemetry Transformation Language functions](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/pkg/ottl/ottlfuncs)
