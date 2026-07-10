# Tail Sampling Error and Latency Traces

## Scenario

You already have a Collector gateway receiving OTLP traces from applications, agents, or SDKs. In this scenario, you will add tail sampling so complete error traces and slow traces are retained while ordinary successful traces are sampled before export to Splunk APM.

Use this for gateway deployments where all spans for a trace can reach the same Collector instance. Do not use this on independent node agents unless trace affinity is guaranteed; partial traces produce poor sampling decisions.

What you should capture before changing the Collector:

| Trace type | Example before this config | What you see |
| --- | --- | --- |
| Error trace | Trace with status `ERROR` | Exported only if your current pipeline exports all traces or samples it elsewhere. |
| Slow trace | Trace with duration greater than the configured threshold | Not specially retained by the Collector. |
| Normal success trace | High-volume successful request traces | Exported in full if no sampling exists. |

## Architecture Overview

```text
instrumented services
  -> existing Collector gateway OTLP receiver
  -> resource detection and Splunk context
  -> tail_sampling/error_and_latency
  -> batch
  -> Splunk APM OTLP ingest
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Collector gateway that receives complete traces or has load balancing with trace affinity.
* Working trace export to Splunk APM.
* Access to edit the gateway Collector configuration and restart or roll out the gateway safely.
* A memory budget for `decision_wait`, `num_traces`, and expected trace rate.
* An approved latency threshold; the example uses `1000` ms as a placeholder to demonstrate the policy.

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

1. Download or copy `otelcol.yaml` and compare it with your current gateway config.
2. Copy `tail_sampling/error_and_latency` into your existing `processors` block.
3. Tune `decision_wait`, `num_traces`, `expected_new_traces_per_sec`, and policy thresholds for your traffic.
4. Place tail sampling after resource/context processors and before `batch` in the traces pipeline.
5. Restart or roll out the gateway and send a mix of successful, error, and slow test traces.

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
    limit_mib: 1024
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
        value: tail-sampling
  tail_sampling/error_and_latency:
    decision_wait: 10s
    num_traces: 50000
    expected_new_traces_per_sec: 500
    decision_cache:
      sampled_cache_size: 100000
      non_sampled_cache_size: 100000
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
  batch: {}

exporters:
  otlphttp:
    traces_endpoint: "${env:SPLUNK_INGEST_URL}/v2/trace/otlp"
    headers:
      X-SF-Token: "${env:SPLUNK_ACCESS_TOKEN}"

service:
  telemetry:
    logs:
      level: info
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, resourcedetection, resource/splunk_context, tail_sampling/error_and_latency, batch]
      exporters: [otlphttp]
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Trace type | Example before this config | What you see |
| --- | --- | --- |
| Error trace | Trace with status `ERROR` | Exported only if your current pipeline exports all traces or samples it elsewhere. |
| Slow trace | Trace with duration greater than the configured threshold | Not specially retained by the Collector. |
| Normal success trace | High-volume successful request traces | Exported in full if no sampling exists. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Trace type | Expected after applying this config | Validation target |
| --- | --- | --- |
| Error trace | Complete error traces are retained by `keep-error-traces`. | Error traces are visible in APM. |
| Slow trace | Traces above `threshold_ms: 1000` are retained by `keep-slow-traces`. | Slow operation traces are visible in APM. |
| Normal success trace | Only a baseline percentage is retained by the probabilistic policy. | Successful trace count drops while service visibility remains. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

Tail sampling waits for enough spans to make a policy decision for the whole trace. This is more useful than probabilistic sampling when errors and latency matter, but it requires memory and trace affinity.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Error traces are missing | Check whether all spans for a trace reach the same gateway instance. | Use trace-aware load balancing or route SDKs/agents consistently. |
| Gateway memory grows | Review `num_traces`, `decision_wait`, and incoming trace rate. | Increase resources or reduce decision windows and retained trace count. |
| Too many normal traces remain | Check the baseline probabilistic policy. | Lower `sampling_percentage` after validating error/latency retention. |

## Scaling Recommendations

* Use gateway replicas with trace affinity for high-volume environments.
* Size memory for buffered traces before enabling high `num_traces` values.
* Monitor Collector refused/dropped spans and exporter queue metrics during rollout.

## Security and Operations Notes

* Sampling does not redact data; sensitive attributes in retained traces are still exported.
* Keep access tokens in your secret manager.
* Document sampling policies so incident responders understand retained and dropped trace classes.

## Configuration Source Basis

This cookbook adapts the local `otelcol.yaml` example and the OpenTelemetry tail sampling processor pattern for an existing Splunk APM Collector gateway.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/tailsamplingprocessor
