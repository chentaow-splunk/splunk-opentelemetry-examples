# Transform and Normalize Telemetry Before Export

## Scenario

You already have a Collector receiving traces, metrics, and logs and exporting them to Splunk Observability Cloud. In this scenario, you will add a transform processor to normalize metadata, remove high-risk attributes, and reduce high-cardinality telemetry before export.

Use this when telemetry is valuable but needs light cleanup before it reaches Splunk. Do not use this as a substitute for application-side data hygiene; producers should still avoid emitting secrets and unstable labels.

What you should capture before changing the Collector:

| Signal | Example before this config | Why it is a problem |
| --- | --- | --- |
| Trace span | `http.request.header.authorization=Bearer synthetic-token` | Sensitive header can appear in APM metadata. |
| Trace span | `db.statement="select * from users where password='synthetic'"` | Query text can expose secret-like values. |
| Metric | `kube_pod_container_status_restarts_total{pod_uid="...",container_id="..."}` | Unstable IDs increase metric cardinality. |
| Log | `token=synthetic-token` | Secret-like value can appear in log search. |

## Architecture Overview

```text
applications, agents, or SDKs
  -> existing Collector OTLP receiver
  -> transform/normalize processor
  -> resource detection and Splunk context
  -> existing Splunk trace, metrics, and log exporters
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Collector deployment receiving the signals you want to normalize.
* Access to edit the Collector configuration and restart or roll out the Collector safely.
* A reviewed list of attributes to delete, keep, truncate, or normalize.
* Representative synthetic telemetry for every transform statement.
* An agreed environment and namespace fallback policy.

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
2. Copy `transform/normalize` into your existing `processors` block.
3. Add the processor after `memory_limiter` and before resource/export processors in each affected pipeline.
4. Replace namespace defaults, metric rename rules, attribute allow-lists, and regexes with your approved policy.
5. Restart or roll out the Collector and send synthetic before/after examples.

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
  transform/normalize:
    error_mode: ignore
    trace_statements:
      - 'set(resource.attributes["deployment.environment"], "unknown") where resource.attributes["deployment.environment"] == nil'
      - 'set(resource.attributes["service.namespace"], "customer-facing") where resource.attributes["service.namespace"] == nil'
      - 'delete_key(span.attributes, "http.request.header.authorization")'
      - 'delete_key(span.attributes, "http.request.header.cookie")'
      - 'replace_pattern(span.attributes["db.statement"], "(?i)(password|token|api_key)\\s*=\\s*''[^'']*''", "$$1=''***''") where span.attributes["db.statement"] != nil'
      - 'truncate_all(span.attributes, 2048)'
      - 'limit(span.attributes, 128, ["http.method", "http.route", "http.status_code"])'
    metric_statements:
      - 'replace_pattern(metric.name, "^kube_([0-9A-Za-z]+_)", "k8s.$$1.")'
      - 'delete_key(datapoint.attributes, "pod_uid")'
      - 'delete_key(datapoint.attributes, "container_id")'
      - 'limit(datapoint.attributes, 64, ["service.name", "k8s.namespace.name", "k8s.pod.name"])'
    log_statements:
      - 'delete_key(log.attributes, "http.request.header.authorization")'
      - 'delete_key(log.attributes, "http.request.header.cookie")'
      - 'replace_pattern(log.body, "(?i)(password|token|api[_-]?key)=([^\\s]+)", "$$1=***") where IsString(log.body)'
      - 'truncate_all(log.attributes, 2048)'
      - 'limit(log.attributes, 128, ["service.name", "deployment.environment", "severity"])'
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
        value: normalized-telemetry
  batch: {}

exporters:
  otlphttp:
    traces_endpoint: "${env:SPLUNK_INGEST_URL}/v2/trace/otlp"
    headers:
      X-SF-Token: "${env:SPLUNK_ACCESS_TOKEN}"
  signalfx:
    access_token: "${env:SPLUNK_ACCESS_TOKEN}"
    api_url: "${env:SPLUNK_API_URL}"
    ingest_url: "${env:SPLUNK_INGEST_URL}"
    sync_host_metadata: true
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
      processors: [memory_limiter, transform/normalize, resourcedetection, resource/splunk_context, batch]
      exporters: [otlphttp]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, transform/normalize, resourcedetection, resource/splunk_context, batch]
      exporters: [signalfx]
    logs:
      receivers: [otlp]
      processors: [memory_limiter, transform/normalize, resourcedetection, resource/splunk_context, batch]
      exporters: [splunk_hec]
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Signal | Example before this config | Why it is a problem |
| --- | --- | --- |
| Trace span | `http.request.header.authorization=Bearer synthetic-token` | Sensitive header can appear in APM metadata. |
| Trace span | `db.statement="select * from users where password='synthetic'"` | Query text can expose secret-like values. |
| Metric | `kube_pod_container_status_restarts_total{pod_uid="...",container_id="..."}` | Unstable IDs increase metric cardinality. |
| Log | `token=synthetic-token` | Secret-like value can appear in log search. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Signal | Expected after applying this config | Validation target |
| --- | --- | --- |
| Trace span | Authorization and cookie header attributes are deleted. | Those keys are absent from APM span metadata. |
| Trace span | Password-like value in `db.statement` is masked by the transform expression. | Original synthetic password value is absent. |
| Metric | `pod_uid` and `container_id` datapoint attributes are deleted. | Metric dimensions no longer include those unstable IDs. |
| Log | `token=synthetic-token` becomes `token=***` for string log bodies. | Original synthetic token is absent from logs. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

The transform processor is useful for targeted cleanup when data is already structurally correct but needs policy enforcement. The tradeoff is complexity: OTTL statements must be tested carefully, and broad limits or deletes can remove context needed for troubleshooting.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Collector fails to start | Check Collector logs for OTTL parse errors. | Fix statement syntax and test with synthetic records. |
| Expected attribute remains | Check the signal context and exact attribute key. | Use the correct span, datapoint, log, or resource context. |
| Useful context disappears | Review `limit` and `delete_key` statements. | Add important keys to the retained-key lists or narrow delete rules. |

## Scaling Recommendations

* Keep statement count small and targeted.
* Review metric cardinality before and after rollout.
* Roll out to a subset of services first when transforms affect shared gateways.

## Security and Operations Notes

* Use synthetic examples for validation only.
* Do not rely on transform rules as the only secret-control layer.
* Keep Splunk tokens in environment variables or your platform secret manager.

## Configuration Source Basis

This cookbook adapts the local `otelcol.yaml` example, OpenTelemetry transform processor OTTL patterns, and Splunk exporter patterns in the examples backend.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor
