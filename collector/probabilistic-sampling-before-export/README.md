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

### After Applying

* Start the Collector with [otelcol.yaml](./otelcol.yaml) and check logs for configuration errors involving `probabilistic_sampler/traces` or `probabilistic_sampler/logs`, plus `otlphttp` or `splunk_hec` exporter errors.
* Re-run the same trace and log test with a sufficiently large sample. Compare source-side counts with Splunk-side counts and confirm the retained volume is broadly consistent with the configured `sampling_percentage`; do not use a tiny sample to validate a probabilistic result.
* In Splunk APM, inspect retained traces and confirm expected resource context, including `deployment.environment` and `service.namespace=probabilistic-sampling`, is still present.
* In logs search, verify synthetic records with `sampling.priority` values behave according to the policy you validated for your deployed Collector version, and that retained logs still contain expected resource context.
* Confirm dashboards or alert thresholds that depend on sampled data are interpreted using the effective sampling policy.

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

## Official Documentation

* [Splunk probabilistic sampler processor](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/probabilistic-sampler-processor)
* [OpenTelemetry Collector probabilistic sampler processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/probabilisticsamplerprocessor)
* [OpenTelemetry sampling concepts](https://opentelemetry.io/docs/concepts/sampling/)
