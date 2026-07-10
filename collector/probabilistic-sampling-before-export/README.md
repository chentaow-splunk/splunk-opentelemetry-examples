# Probabilistic Sampling Before Export

## Scenario

You already have a Collector receiving traces and logs and exporting them to Splunk Observability Cloud. In this scenario, you will add stateless probabilistic sampling to reduce export volume before telemetry leaves the Collector.

Use this when each trace or log record can be sampled independently and you need a simple baseline reduction policy. Do not use this when you must keep complete error traces or slow traces; use tail sampling for whole-trace decisions.

What you should capture before changing the Collector:

| Signal | Example before this config | What you see |
| --- | --- | --- |
| Traces | 100 ordinary successful request traces in a short test window | Nearly all 100 traces are exported. |
| Logs | 100 similar informational log records | Nearly all 100 logs are exported. |
| Errors | 5 error traces mixed with normal traces | Error traces are not specially protected by this policy. |

## Architecture Overview

```text
applications or agents
  -> existing Collector OTLP receiver
  -> probabilistic_sampler processors
  -> resource detection and Splunk context
  -> existing Splunk trace and log exporters
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Collector deployment that already receives OTLP traces and logs.
* Access to edit the Collector configuration and restart or roll out the Collector safely.
* An approved sampling percentage for each signal.
* A consistent `hash_seed` plan for Collectors at the same tier if deterministic sampling matters.
* A test workload that can send enough synthetic traces or logs to observe the approximate sample rate.

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
2. Copy the two `probabilistic_sampler` processors into your existing `processors` block.
3. Set `sampling_percentage` to your approved value; the example uses `20` as a demonstrable starting point, not a universal recommendation.
4. Place the sampler after `memory_limiter` and before enrichment/export processors in the affected pipelines.
5. Restart or roll out the Collector and send a known test volume.

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
  probabilistic_sampler/traces:
    mode: proportional
    sampling_percentage: 20
    hash_seed: 22
    fail_closed: true
  probabilistic_sampler/logs:
    sampling_percentage: 20
    hash_seed: 22
    fail_closed: false
    sampling_priority: sampling.priority
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
        value: probabilistic-sampling
  batch: {}

exporters:
  otlphttp:
    traces_endpoint: "${env:SPLUNK_INGEST_URL}/v2/trace/otlp"
    headers:
      X-SF-Token: "${env:SPLUNK_ACCESS_TOKEN}"
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
      processors: [memory_limiter, probabilistic_sampler/traces, resourcedetection, resource/splunk_context, batch]
      exporters: [otlphttp]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, probabilistic_sampler/logs, resourcedetection, resource/splunk_context, batch]
      exporters: [splunk_hec]
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Signal | Example before this config | What you see |
| --- | --- | --- |
| Traces | 100 ordinary successful request traces in a short test window | Nearly all 100 traces are exported. |
| Logs | 100 similar informational log records | Nearly all 100 logs are exported. |
| Errors | 5 error traces mixed with normal traces | Error traces are not specially protected by this policy. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Signal | Expected after applying this config | How to interpret it |
| --- | --- | --- |
| Traces | About 20 of 100 ordinary traces are exported with `sampling_percentage: 20`. | Small test windows can vary; larger windows should be closer to the configured percentage. |
| Logs | About 20 of 100 eligible log records are exported. | Logs without usable randomness can behave according to `fail_closed` and processor settings. |
| Errors | Error traces are sampled like any other trace. | This is expected for probabilistic sampling; use tail sampling if errors must always be retained. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

Probabilistic sampling is simple, fast, and stateless. It is useful for baseline volume reduction at high-throughput tiers, but it cannot make decisions based on complete trace outcome or latency.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Sample rate looks wrong | Use a larger test window and confirm the correct pipeline includes the sampler. | Validate `sampling_percentage` and `hash_seed` values. |
| Important traces are missing | Check whether this should be tail sampling instead. | Use policy-based tail sampling for errors, latency, or service-specific retention. |
| Logs are unexpectedly retained | Check whether records have trace IDs and whether `fail_closed` is false. | Set log-specific sampling behavior intentionally for your data shape. |

## Scaling Recommendations

* Keep sampling decisions consistent at the same Collector tier by using stable seed values.
* Roll out gradually and compare request/error rates before and after sampling.
* Do not stack multiple independent probabilistic samplers unless the combined effective rate is intentional.

## Security and Operations Notes

* Sampling is not redaction; sensitive data in retained telemetry is still exported.
* Document who approved each sampling percentage.
* Keep Splunk tokens in environment variables or your platform secret manager.

## Configuration Source Basis

This cookbook adapts the local `otelcol.yaml` example and the OpenTelemetry Collector probabilistic sampler processor behavior into an existing-Collector rollout flow.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/probabilisticsamplerprocessor
