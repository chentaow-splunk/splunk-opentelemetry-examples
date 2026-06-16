# Filter Noisy Telemetry Before Export

## Scenario

Use this recipe when a platform team wants to drop low-value telemetry in the Collector before it reaches Splunk Observability Cloud.

The example drops health-check spans, low-severity logs, unwanted runtime metrics, and known noisy route datapoints. Do not use this recipe as the first response to an unknown data quality issue; validate that the dropped telemetry is truly low value and does not hide incident evidence.

## Architecture Overview

```text
applications and agents
  -> OTLP traces, metrics, and logs
  -> Splunk OTel Collector filter processor
  -> memory_limiter, resourcedetection, resource/splunk_context, batch
  -> Splunk APM, metrics ingest, and log ingest
```

The filter processor evaluates OTTL conditions by signal. When any condition matches, the matching span, metric, datapoint, or log record is dropped from the Collector pipeline.

## Prerequisites

* Splunk Observability Cloud access token, HEC token, API URL, ingest URL, and HEC URL.
* A Collector build that includes the `filter` processor and the exporters used in [otelcol.yaml](./otelcol.yaml).
* Agreement from service owners on the routes, severities, and metric names that can be dropped.
* A non-production validation environment where filter conditions can be tested against representative telemetry.
* A Collector version whose filter processor supports the documented `trace_conditions`, `metric_conditions`, and `log_conditions` syntax. Older syntax can still exist in local examples, but this recipe uses the current documented form.

## Installation Instructions

1. Copy [otelcol.yaml](./otelcol.yaml) to the Collector host or gateway.
2. Replace the example OTTL conditions with your approved drop policy.
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

Pin the Collector image version after validating the exact OTTL syntax.

## Proposed Configuration File

Use [otelcol.yaml](./otelcol.yaml). The core processor block is:

```yaml
processors:
  filter/noise:
    error_mode: ignore
    trace_conditions:
      - 'IsMatch(span.name, ".*/(health|ready|live|metrics).*")'
    metric_conditions:
      - 'IsMatch(metric.name, "^(go_|process_|promhttp_).*")'
    log_conditions:
      - 'log.severity_number < SEVERITY_NUMBER_WARN'
```

## Validation

### Before Applying

* In a non-production environment, send representative telemetry that should later be dropped: a health or readiness span, a `go_`, `process_`, or `promhttp_` metric, a datapoint with `http.route=/health`, and a low-severity or health-check log record.
* Send matching retained telemetry at the same time, such as an error span, an application metric not covered by the drop rules, and a warning or error log.
* Before enabling `filter/noise`, use Splunk APM, Metric Finder, and logs search to confirm both noisy and retained examples can be observed. This establishes that later absence is caused by the filter, not by missing instrumentation.
* Review the current Collector logs for OTLP receiver, processor, or exporter errors so existing ingestion problems are separated from filter behavior.

Expected baseline result:

```text
APM: a span named GET /health or GET /ready is visible.
Metric Finder: go_*, process_*, or promhttp_* metrics are visible when sent by the source.
Logs search: DEBUG/INFO health-check logs are visible alongside WARN/ERROR application logs.
Collector logs: no filter/noise processor is active for these pipelines.
```

### After Applying

* Start the Collector with [otelcol.yaml](./otelcol.yaml) and check logs for configuration, OTTL parse, or evaluation errors involving `filter/noise`. Also check for `otlphttp`, `signalfx`, or `splunk_hec` exporter errors.
* Re-send the same dropped and retained test telemetry. In Splunk APM, health-check spans should be absent while retained error or non-health spans remain visible.
* In Metric Finder, verify metrics matching the configured noisy names or route attributes are not newly exported, while retained service metrics still arrive with expected resource context.
* In logs search, verify low-severity or health-check logs are absent and warning or error logs from the same test source still arrive.

Expected post-change result:

```text
Collector logs: no OTTL parse errors for filter/noise.
APM: GET /health or GET /ready spans sent through this Collector are absent; retained non-health or error spans remain visible.
Metric Finder: go_*, process_*, and promhttp_* series are not newly exported by this Collector; allowed service metrics still arrive.
Logs search: low-severity health-check logs are absent; WARN/ERROR logs from the same source remain visible.
```

You can sanity-check the OTTL conditions with synthetic records in an OTTL playground such as `https://ottl.run/`. Use non-sensitive samples only. Expected OTTL behavior:

| Condition | Synthetic input | Expected result |
| --- | --- | --- |
| `IsMatch(span.name, ".*/(health|ready|live|metrics).*")` | `span.name = "GET /health"` | `true`, record is dropped. |
| `IsMatch(metric.name, "^(go_|process_|promhttp_).*")` | `metric.name = "process_cpu_seconds_total"` | `true`, metric is dropped. |
| `log.severity_number < SEVERITY_NUMBER_WARN` | `log.severity_number = SEVERITY_NUMBER_INFO` | `true`, log is dropped. |

### Live Local Validation Result

Validated with `scripts/validate_collector_cookbooks.py` using `quay.io/signalfx/splunk-otel-collector:latest`, synthetic OTLP traces, metrics, and logs, and the Collector `debug` exporter. This validates local Collector filter behavior before any Splunk export.

Status: `PASS`

Observed before:

```text
Synthetic batch included GET /health, process_cpu_seconds_total, healthcheck ok, GET /checkout, checkout_requests_total, and checkout failed.
```

Observed after:

```text
debug exporter output retained GET /checkout, checkout_requests_total, and checkout failed; dropped the noisy samples.
```

### Splunk Backend Payload Validation Result

Validated with `scripts/validate_collector_cookbooks.py --backend-cookbooks --realm us0`. The harness exported synthetic before/after telemetry through live Collector instances and queried Splunk Observability Cloud `/v2/metrictimeseries` for the actual ingested metric dimensions.

```text
Splunk realm: us0

Before health-route metric:
  API: /v2/metrictimeseries
  HTTP status: 200
  found: True
  query: sf_metric:test_requests_total AND validation_run_id:filter-noisy-telemetry-before-export-before-health-1781592848
  count: 1
  dimensions: {"deployment.environment": "validation", "host.name": "0c0d08999401", "http.route": "/health", "os.type": "linux", "service.name": "codex-filter-before", "sf_metric": null, "validation_run_id": "filter-noisy-telemetry-before-export-before-health-1781592848"}
  customProperties: {"deployment.environment": "validation", "host.name": "0c0d08999401", "http.route": "/health", "os.type": "linux", "service.name": "codex-filter-before", "validation_run_id": "filter-noisy-telemetry-before-export-before-health-1781592848"}

Before checkout-route metric:
  API: /v2/metrictimeseries
  HTTP status: 200
  found: True
  query: sf_metric:test_requests_total AND validation_run_id:filter-noisy-telemetry-before-export-before-checkout-1781592848
  count: 1
  dimensions: {"deployment.environment": "validation", "host.name": "0c0d08999401", "http.route": "/checkout", "os.type": "linux", "service.name": "codex-filter-before", "sf_metric": null, "validation_run_id": "filter-noisy-telemetry-before-export-before-checkout-1781592848"}
  customProperties: {"deployment.environment": "validation", "host.name": "0c0d08999401", "http.route": "/checkout", "os.type": "linux", "service.name": "codex-filter-before", "validation_run_id": "filter-noisy-telemetry-before-export-before-checkout-1781592848"}

After health-route lookup:
  API: /v2/metrictimeseries
  HTTP status: 200
  found: False
  query: sf_metric:test_requests_total AND validation_run_id:filter-noisy-telemetry-before-export-after-health-1781592848
  count: 0
  evidence: count=0
  evidence: metric=None
  evidence: dimensions={}

After checkout-route metric:
  API: /v2/metrictimeseries
  HTTP status: 200
  found: True
  query: sf_metric:test_requests_total AND validation_run_id:filter-noisy-telemetry-before-export-after-checkout-1781592848
  count: 1
  dimensions: {"deployment.environment": "validation", "host.name": "acd7bf48e79e", "http.route": "/checkout", "os.type": "linux", "service.name": "codex-filter-after", "sf_metric": null, "validation_run_id": "filter-noisy-telemetry-before-export-after-checkout-1781592848"}
  customProperties: {"deployment.environment": "validation", "host.name": "acd7bf48e79e", "http.route": "/checkout", "os.type": "linux", "service.name": "codex-filter-after", "validation_run_id": "filter-noisy-telemetry-before-export-after-checkout-1781592848"}
```

## Why This Configuration

`error_mode: ignore` keeps valid telemetry flowing if a condition cannot evaluate on a particular record. The filter processor is placed early, after `memory_limiter`, so dropped telemetry does not consume later processor and exporter capacity.

Keep conditions specific and easy to test. If you add latency or duration-based rules, validate the exact OTTL expression against your deployed Collector version before rollout.

## Troubleshooting

If the Collector fails to start, check the filter processor error for the exact OTTL statement and compare it with the processor documentation for your Collector version.

If too much data is dropped, disable one condition at a time and validate with known trace IDs, metric names, or log messages.

If expected low-severity logs still arrive, check whether the source populates `severity_number`. Some log sources only populate text severity until parsed upstream.

If route filtering is ineffective for metrics, inspect the datapoint attributes produced by your receiver. Not every metric has `http.route` or `http.target`.

## Scaling Recommendations

Roll out filter changes gradually. Start with a single service, namespace, or Collector gateway before applying broad policies.

Keep a review process for every new drop rule. Dropping telemetry reduces cost and noise but also removes forensic data.

For high-volume Prometheus metrics, prefer scrape-time `metric_relabel_configs` when possible. Use the filter processor when the decision requires OTTL context across signals.

## Security and Operations Notes

Treat filter rules as production controls. A bad rule can hide outages, failed authentication, or suspicious traffic.

Keep the configuration in source control and require review from service owners for route, status, and severity based drops.

Do not use filters to redact secrets. Use the redaction or transform recipes when data must be retained but masked.

## Configuration Source Basis

This recipe is based on the upstream filter processor model where matching OTTL conditions drop telemetry by signal. The local Cisco AI Ready Pods examples already use filter processors for health and metric-selection patterns; this cookbook generalizes that operational pattern to common application telemetry noise.

The examples intentionally use simple span-name, metric-name, datapoint-attribute, and severity predicates. They are realistic production controls, but each rule must be reviewed against the source telemetry schema before rollout because filtering removes data.

## Official Documentation

* [Splunk filter processor](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/filter-processor)
* [OpenTelemetry Collector filter processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/filterprocessor)
* [OpenTelemetry Transformation Language](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/pkg/ottl)
