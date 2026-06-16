# Probabilistic Sampling Before Export

## Scenario

Use this recipe when you need stateless volume control for traces or logs before export to Splunk Observability Cloud.

Probabilistic sampling is appropriate for baseline reduction when every item can be sampled independently. Do not use this recipe when you need to retain all spans for error traces or slow traces; use tail sampling for whole-trace decisions.

## Architecture Overview

```text
applications and agents
  -> OTLP traces and logs
  -> probabilistic_sampler processors
  -> resourcedetection and Splunk context attributes
  -> batch
  -> Splunk APM and log ingest
```

Trace sampling decisions are based on trace ID. Log sampling can use trace ID when present, and the processor also supports log-specific priority behavior documented upstream.

## Prerequisites

* Splunk Observability Cloud access token, HEC token, ingest URL, and HEC URL.
* A Collector build that includes the `probabilistic_sampler` processor.
* A documented sampling policy approved by service owners.
* Consistent `hash_seed` values across Collectors at the same tier when you need consistent sampling behavior.
* For logs, an understanding of whether records have trace IDs. Records without usable randomness can pass through when `fail_closed: false`.

## Installation Instructions

1. Copy [otelcol.yaml](./otelcol.yaml) to the Collector host or gateway.
2. Replace `sampling_percentage` with the approved trace and log sampling rates.
3. Use the same `hash_seed` for Collectors at the same tier.
4. Export Splunk settings:

   ```bash
   export SPLUNK_ACCESS_TOKEN='<splunk_access_token>'
   export SPLUNK_HEC_TOKEN='<splunk_hec_token>'
   export SPLUNK_INGEST_URL='https://ingest.<realm>.observability.splunkcloud.com'
   export SPLUNK_HEC_URL='https://ingest.<realm>.observability.splunkcloud.com/v1/log'
   export DEPLOYMENT_ENVIRONMENT='<environment_name>'
   ```

5. Start the Collector:

   ```bash
   docker run --rm --name splunk-otel-collector \
     -p 4317:4317 \
     -p 4318:4318 \
     -e SPLUNK_CONFIG=/etc/collector/otelcol.yaml \
     -e SPLUNK_ACCESS_TOKEN \
     -e SPLUNK_HEC_TOKEN \
     -e SPLUNK_INGEST_URL \
     -e SPLUNK_HEC_URL \
     -e DEPLOYMENT_ENVIRONMENT \
     -v "$(pwd)/otelcol.yaml:/etc/collector/otelcol.yaml:ro" \
     quay.io/signalfx/splunk-otel-collector:latest
   ```

## Proposed Configuration File

Use [otelcol.yaml](./otelcol.yaml). The sampling blocks are:

```yaml
processors:
  probabilistic_sampler/traces:
    mode: proportional
    sampling_percentage: 20
    hash_seed: 22
  probabilistic_sampler/logs:
    sampling_percentage: 20
    hash_seed: 22
    fail_closed: false
    sampling_priority: sampling.priority
```

For logs without trace IDs, consider adding a stable log record attribute and configuring `attribute_source: record` plus `from_attribute`. Do not use a high-cardinality customer identifier without review.

## Validation

### Before Applying

* Capture a source-side baseline for traces and logs before enabling the sampler. Use a large enough non-production sample that a percentage-based comparison is meaningful.
* In Splunk APM and logs search, record the current trace and log volume for the same test window. If another sampler is already active, document it before attributing changes to this Collector.
* Send synthetic logs with the `sampling.priority` values your policy depends on and confirm how the current pipeline handles them before this processor is introduced.
* Review current Collector logs for OTLP receiver or exporter errors so missing data is not confused with sampling.

Expected baseline result:

```text
Source-side test: for example, 1,000 synthetic traces and 1,000 synthetic logs are emitted.
Splunk APM/logs: retained volume is near the existing baseline, often close to the source-side count if no sampler is already active.
Collector logs: no probabilistic_sampler/traces or probabilistic_sampler/logs processor is active in these pipelines.
```

### After Applying

* Start the Collector with [otelcol.yaml](./otelcol.yaml) and check logs for configuration errors involving `probabilistic_sampler/traces` or `probabilistic_sampler/logs`, plus `otlphttp` or `splunk_hec` exporter errors.
* Re-run the same trace and log test with a sufficiently large sample. Compare source-side counts with Splunk-side counts and confirm the retained volume is broadly consistent with the configured `sampling_percentage`; do not use a tiny sample to validate a probabilistic result.
* In Splunk APM, inspect retained traces and confirm expected resource context, including `deployment.environment` and `service.namespace=probabilistic-sampling`, is still present.
* In logs search, verify synthetic records with `sampling.priority` values behave according to the policy you validated for your deployed Collector version, and that retained logs still contain expected resource context.
* Confirm dashboards or alert thresholds that depend on sampled data are interpreted using the effective sampling policy.

Expected post-change result:

```text
Collector logs: probabilistic_sampler/traces and probabilistic_sampler/logs start without configuration errors.
Splunk APM/logs: over a large non-production sample, retained telemetry is roughly consistent with sampling_percentage=20.
Splunk APM/logs: retained telemetry still includes deployment.environment and service.namespace=probabilistic-sampling.
```

Do not validate this with a tiny sample. With percentage-based sampling, small batches can vary substantially from the configured percentage.

### Live Local Validation Result

Validated with `scripts/validate_collector_cookbooks.py` using `quay.io/signalfx/splunk-otel-collector:latest`, 100 synthetic OTLP log records, and the Collector `debug` exporter. This validates local probabilistic sampler behavior before any Splunk export.

Status: `PASS`

Observed before:

```text
Synthetic source sent 100 log records.
```

Observed after:

```text
debug exporter output retained 36 unique records, consistent with percentage sampling over a small local test.
```

### Splunk Backend Payload Validation Status

Checked with `scripts/validate_collector_cookbooks.py --backend-cookbooks --realm us0`. The local Collector payload validation passed, but backend payload validation for this signal was not performed in this environment.

```text
Not performed.
This cookbook processes logs. No SPLUNK_HEC_TOKEN or Splunk log-query endpoint is configured in .env, so I cannot honestly query the ingested log body or log attributes in Splunk.
The local Collector validation above still inspects the actual processed debug-exporter payload, including log/span bodies and attributes.
Backend validation is required; local health alone does not prove ingestion.
```

## Why This Configuration

The trace sampler uses `mode: proportional` for predictable ratio-based trace reduction. The log sampler keeps `fail_closed: false` so logs without sampling randomness are not dropped unexpectedly during initial rollout.

Separate named processors make trace and log behavior explicit. Metrics are not included because this processor is documented for spans and log records, not metric sampling.

## Troubleshooting

If sampled trace counts are unexpectedly high or low, confirm all Collectors at the same tier use the same `hash_seed` and sampling percentage.

If logs are not reduced, check whether records have trace IDs. Without usable randomness and with `fail_closed: false`, erroneous records pass through.

If important logs are dropped, use `sampling.priority` or a separate routing policy for critical sources before broad sampling.

If traces look incomplete, confirm SDK sampling and Collector sampling are not fighting each other. Stateless processor sampling does not make whole-trace keep decisions like tail sampling.

## Scaling Recommendations

Apply probabilistic sampling as close to the source as operationally safe. This reduces network and gateway load.

Use the same sampling settings per tier. Mixed percentages can be valid, but document why each tier differs.

Keep sampling percentages high enough for low-traffic services. A 1 percent sample on a service with few requests can remove almost all diagnostic value.

## Security and Operations Notes

Sampling drops data. Make sure retention requirements, audit expectations, and incident response needs are reviewed before production rollout.

Do not use sampling as redaction. Sensitive fields in retained telemetry still require redaction or transform rules.

Record the effective sampling policy in service runbooks so dashboards and alert thresholds are interpreted correctly.

## Configuration Source Basis

This recipe is derived from the upstream probabilistic sampler processor behavior for stateless percentage-based reduction of spans and logs. The real-world scenario is cost and volume control for high-throughput services where retaining every ordinary request or log line is not operationally necessary.

The recipe deliberately excludes metrics because the referenced processor is documented for traces and logs. Use metric aggregation, scrape-time relabeling, or receiver-specific controls for metric volume instead.

## Official Documentation

* [Splunk probabilistic sampler processor](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/probabilistic-sampler-processor)
* [OpenTelemetry Collector probabilistic sampler processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/probabilisticsamplerprocessor)
* [OpenTelemetry sampling concepts](https://opentelemetry.io/docs/concepts/sampling/)
