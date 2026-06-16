# Tail Sampling Error and Latency Traces

## Scenario

Use this recipe when you need to keep complete traces for errors and slow requests while sampling ordinary successful traces before export to Splunk APM.

Tail sampling fits gateway deployments where all spans for a trace can reach the same Collector instance. Do not use this recipe on independent node agents unless trace affinity is guaranteed; partial traces lead to poor sampling decisions.

## Architecture Overview

```text
instrumented services
  -> OTLP traces
  -> Collector gateway with trace affinity
  -> resourcedetection and Splunk context attributes
  -> tail_sampling policies
  -> batch
  -> Splunk APM ingest
```

The tail sampling processor buffers spans by trace ID, waits for enough of the trace to arrive, and then applies policies for errors, latency, and baseline probabilistic sampling.

## Prerequisites

* Splunk Observability Cloud access token and ingest URL.
* A Collector build that includes the `tail_sampling` processor.
* A gateway topology or load-balancing strategy that sends all spans for the same trace to the same Collector instance.
* Enough Collector memory for `decision_wait`, `num_traces`, and traffic rate.
* A tested threshold for slow traces. The example uses `1000` milliseconds as a placeholder, not a universal recommendation.

## Installation Instructions

1. Deploy the Collector as a gateway receiving OTLP traffic from agents or SDKs.
2. Copy [otelcol.yaml](./otelcol.yaml) to the gateway.
3. Tune `decision_wait`, `num_traces`, `expected_new_traces_per_sec`, and policy thresholds for your traffic.
4. Export Splunk settings:

   ```bash
   export SPLUNK_ACCESS_TOKEN='<splunk_access_token>'
   export SPLUNK_INGEST_URL='https://ingest.<realm>.observability.splunkcloud.com'
   export DEPLOYMENT_ENVIRONMENT='<environment_name>'
   ```

5. Start the Collector:

   ```bash
   docker run --rm --name splunk-otel-collector \
     -p 4317:4317 \
     -p 4318:4318 \
     -e SPLUNK_CONFIG=/etc/collector/otelcol.yaml \
     -e SPLUNK_ACCESS_TOKEN \
     -e SPLUNK_INGEST_URL \
     -e DEPLOYMENT_ENVIRONMENT \
     -v "$(pwd)/otelcol.yaml:/etc/collector/otelcol.yaml:ro" \
     quay.io/signalfx/splunk-otel-collector:latest
   ```

## Proposed Configuration File

Use [otelcol.yaml](./otelcol.yaml). The policy set is:

```yaml
processors:
  tail_sampling/error_and_latency:
    decision_wait: 10s
    policies:
      - name: keep-error-traces
        type: status_code
        status_code:
          status_codes: [ERROR]
      - name: keep-slow-traces
        type: latency
        latency:
          threshold_ms: 1000
      - name: baseline-probabilistic-sample
        type: probabilistic
        probabilistic:
          sampling_percentage: 10
```

## Validation

### Before Applying

* Confirm the gateway topology can route all spans for a trace to the same Collector instance. If trace affinity is not in place, fix that before validating tail sampling.
* Generate a non-production error request, a request slower than the planned threshold, and a larger batch of ordinary successful requests. Record the source-side trace IDs or request IDs where your instrumentation makes that possible.
* In Splunk APM, note the current retention behavior for those traces before `tail_sampling/error_and_latency` is enabled. If SDK or upstream sampling already drops traces, document that baseline.
* Review current gateway logs for OTLP receiver or `otlphttp` exporter errors before enabling tail sampling.

Expected baseline result:

```text
Gateway topology: all spans for a trace are routed to one gateway instance, or the validation is blocked until trace affinity is fixed.
Splunk APM: error, slow, and ordinary successful traces follow the current sampling policy, which may drop important traces if no tail sampler is active.
Collector logs: no tail_sampling/error_and_latency processor is active, and existing OTLP/export errors are documented before rollout.
```

### After Applying

* Start the gateway Collector with [otelcol.yaml](./otelcol.yaml) and check logs for configuration errors involving `tail_sampling/error_and_latency`, memory pressure, dropped traces, or `otlphttp` exporter errors.
* Re-run the error and slow request tests, then wait longer than `decision_wait` plus normal ingest delay before checking Splunk APM. The error and slow traces should be retained as complete traces when all spans reach the same gateway instance.
* Re-run the ordinary successful request batch and compare retained traces with the source-side count. The retained volume should be broadly consistent with the baseline probabilistic policy over a large enough sample.
* Inspect retained traces in APM for expected resource context, including `deployment.environment` and `service.namespace=tail-sampling`.
* If traces are incomplete or policy results look inconsistent, check gateway load balancing for span fan-out before changing sampling thresholds.

Expected post-change result:

```text
Collector logs: tail_sampling/error_and_latency starts without configuration errors and memory pressure is within gateway limits.
Splunk APM: synthetic ERROR traces are retained.
Splunk APM: synthetic traces slower than 1000 ms are retained.
Splunk APM: ordinary successful traces are retained at roughly the baseline probabilistic policy over a large sample.
```

### Live Local Validation Result

Validated with `scripts/validate_collector_cookbooks.py` using `quay.io/signalfx/splunk-otel-collector:latest`, synthetic OTLP traces, and the Collector `debug` exporter. The local validation sets the baseline probabilistic policy to 0 percent so the ordinary trace drop is deterministic.

Status: `PASS`

Observed before:

```text
Synthetic batch included GET /error, GET /slow, and GET /ordinary.
```

Observed after:

```text
debug exporter output retained GET /error and GET /slow; dropped GET /ordinary with baseline sampling set to 0 for deterministic validation.
```

## Why This Configuration

The `status_code` policy keeps error traces. The `latency` policy keeps slow traces. The probabilistic policy keeps a baseline sample of ordinary traces so service maps and latency trends still have data.

Resource enrichment runs before tail sampling so policy decisions can use resource context later if you add attribute-based policies. `batch` runs after tail sampling because the processor reassembles spans into new batches.

## Troubleshooting

If error traces are incomplete, verify trace affinity across gateways and confirm all services propagate trace context.

If memory usage is high, reduce `decision_wait`, tune `num_traces`, or scale gateway capacity with trace-aware routing.

If slow traces are not retained, confirm the threshold is lower than the trace duration and that all spans reached the same processor instance.

If ordinary traces are over-retained, review policy interaction. Tail sampling samples a trace when any sample policy matches and no drop policy overrides it.

## Scaling Recommendations

Run tail sampling in a gateway tier with enough memory headroom. It buffers traces, so capacity planning must account for request rate and `decision_wait`.

Use trace-aware load balancing before multiple tail-sampling gateway replicas. The processor documentation states that all spans for a trace must reach the same Collector instance for effective decisions.

Start with conservative thresholds and monitor retained trace volume before lowering baseline sampling.

## Security and Operations Notes

Tail sampling changes observability completeness. Document that ordinary successful traces may be absent by design.

Do not route regulated data to a gateway solely because it samples. Sampling does not redact retained spans.

Keep emergency procedures for temporarily increasing sampling during incidents.

## Configuration Source Basis

This recipe follows the upstream tail sampling processor policy model for whole-trace decisions after spans have been buffered. The error, latency, and baseline probabilistic policies are common gateway-side production controls: keep high-value traces, keep slow traces for performance analysis, and retain a small ordinary baseline.

The topology warning comes directly from the tail-sampling requirement that all spans for a trace must reach the same Collector instance. Without that, expected before/after results are not meaningful.

## Official Documentation

* [Splunk tail sampling processor](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/processors/tail-sampling-processor)
* [OpenTelemetry Collector tail sampling processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/tailsamplingprocessor)
* [OpenTelemetry sampling concepts](https://opentelemetry.io/docs/concepts/sampling/)
