# Prometheus Scraping with Kubernetes Discovery

## Scenario

You already run the Splunk OpenTelemetry Collector Helm chart in Kubernetes. In this scenario, you will add a Helm values overlay so the Collector discovers selected Prometheus scrape targets in the cluster and exports those metrics to Splunk Observability Cloud.

Use this when workloads already expose Prometheus-format `/metrics` endpoints and you want a controlled discovery pattern. Do not use this as a full Prometheus server replacement for rules, Alertmanager, remote read, or remote write.

What you should capture before changing the Collector:

| Target | Example before this config | What you see |
| --- | --- | --- |
| Pod with `/metrics` | Deployment exposes `http_server_requests_total` but has no scrape label | Metric is absent from Splunk Metric Finder. |
| Annotated service | Service is not annotated for scraping | No `kubernetes-service-metrics` scrape data appears. |
| Cluster receiver logs | No Prometheus service discovery scrape job is configured | No scrape loop for annotated services. |

## Architecture Overview

```text
Kubernetes pods and services
  -> Splunk OTel Collector Helm chart receivers
  -> receiver_creator and prometheus receiver discovery
  -> memory limiter, resource detection, resource, batch
  -> signalfx exporter
```

This cookbook assumes the Collector is already installed. The work is to merge the relevant receiver, processor, exporter, and pipeline blocks into the configuration you already operate.

## Prerequisites

* An existing Splunk OTel Collector Helm release in the target cluster.
* Access to update the Helm values used by that release.
* Existing Splunk Observability Cloud realm, access token secret, cluster name, and environment value.
* RBAC that allows the enabled Collector components to observe pods and services.
* Workloads or services that expose Prometheus-format metrics and have an approved metric allow-list.

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

1. Download or copy `values.yaml` and compare it with your current Helm values.
2. Merge the `agent.config` and `clusterReceiver.config` sections into your existing values file.
3. Replace `<cluster_name>` and `<environment_name>` with your standard chart values.
4. Label pod-owning workloads with `observability.splunk.com/scrape=true` when using pod discovery.
5. Annotate services with `prometheus.io/scrape=true`, `prometheus.io/port`, and `prometheus.io/path` when using service discovery.
6. Run `helm upgrade` using your existing release name, namespace, secret management, and values file.

For host-based Collectors, validate the merged file with your existing Collector binary or service wrapper before restart. For Kubernetes Helm deployments, run a Helm template or diff workflow before applying changes.

## Proposed Configuration File

Download the reusable example file: [values.yaml](./values.yaml).

Use it as a reference or overlay, not as a blind replacement for your production Collector config. Keep your existing receivers, extensions, exporters, resource attributes, and secret references unless this scenario intentionally changes them.

Full example Helm values overlay:

```yaml
clusterName: "<cluster_name>"
environment: "<environment_name>"

secret:
  create: false
  name: splunk-secret

agent:
  discovery:
    enabled: true
  config:
    receivers:
      receiver_creator/prometheus_pods:
        watch_observers: [k8s_observer]
        receivers:
          prometheus:
            rule: type == "pod" && labels["observability.splunk.com/scrape"] == "true"
            config:
              config:
                scrape_configs:
                  - job_name: kubernetes-pod-metrics
                    scrape_interval: 30s
                    scrape_timeout: 10s
                    metrics_path: /metrics
                    static_configs:
                      - targets:
                          - '`endpoint`'
                    metric_relabel_configs:
                      - source_labels: [__name__]
                        regex: "(http_server_request_duration_seconds.*|http_server_requests_total|process_cpu_seconds_total|process_resident_memory_bytes)"
                        action: keep
    service:
      pipelines:
        metrics/prometheus-pods:
          receivers:
            - receiver_creator/prometheus_pods
          processors:
            - memory_limiter
            - resourcedetection
            - resource
            - batch
          exporters:
            - signalfx

clusterReceiver:
  config:
    receivers:
      prometheus/kubernetes_services:
        config:
          scrape_configs:
            - job_name: kubernetes-service-metrics
              scrape_interval: 30s
              scrape_timeout: 10s
              kubernetes_sd_configs:
                - role: service
              relabel_configs:
                - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_scrape]
                  regex: "true"
                  action: keep
                - source_labels: [__meta_kubernetes_service_annotation_prometheus_io_path]
                  regex: "(.+)"
                  target_label: __metrics_path__
                - source_labels:
                    - __address__
                    - __meta_kubernetes_service_annotation_prometheus_io_port
                  regex: "([^:]+)(?::\\d+)?;(\\d+)"
                  replacement: "$$1:$$2"
                  target_label: __address__
              metric_relabel_configs:
                - source_labels: [__name__]
                  regex: "(http_server_request_duration_seconds.*|http_server_requests_total|grpc_server_handled_total)"
                  action: keep
    service:
      pipelines:
        metrics/prometheus-services:
          receivers:
            - prometheus/kubernetes_services
          processors:
            - memory_limiter
            - resourcedetection
            - resource
            - batch
          exporters:
            - signalfx
```

## Validation

### Before Applying

1. Send or observe the synthetic examples from the Scenario section through your current Collector path.
2. Confirm the baseline behavior in Collector logs and Splunk Observability Cloud.
3. Save a screenshot, query result, or metric/log/span example so you can compare after the change.

Baseline examples to look for:

| Target | Example before this config | What you see |
| --- | --- | --- |
| Pod with `/metrics` | Deployment exposes `http_server_requests_total` but has no scrape label | Metric is absent from Splunk Metric Finder. |
| Annotated service | Service is not annotated for scraping | No `kubernetes-service-metrics` scrape data appears. |
| Cluster receiver logs | No Prometheus service discovery scrape job is configured | No scrape loop for annotated services. |

### After Applying

1. Confirm the Collector starts without configuration, receiver, processor, or exporter errors.
2. Send the same synthetic examples again.
3. Compare the post-change output to the expected result below.

| Target | Expected after applying this config | How to validate |
| --- | --- | --- |
| Labelled pod | Metrics matching the allow-list, such as `http_server_requests_total`, are exported. | Search Metric Finder for the metric and Kubernetes dimensions. |
| Annotated service | `kubernetes-service-metrics` scrape job exports allowed service metrics. | Confirm metrics appear with service/cluster labels. |
| Unlabelled workload | No metrics are scraped by this rule. | Remove or omit the label and confirm the target is ignored. |

If an example depends on OTTL syntax, you can sanity-check non-sensitive sample expressions with `https://ottl.run/`. That does not replace testing the exact Collector build and configuration you deploy.

## Why This Configuration

Discovery-based scraping lets platform teams opt in workloads without hard-coding every pod IP. The allow-list keeps noisy or accidental metrics from expanding cardinality unexpectedly.

## Troubleshooting

| Symptom | First check | Likely fix |
| --- | --- | --- |
| Metrics do not appear | Check pod labels, service annotations, and Collector pod logs for scrape errors. | Fix labels/annotations, port, or metrics path. |
| Too many metrics appear | Review `metric_relabel_configs`. | Narrow the allow-list before broad rollout. |
| Collector cannot watch resources | Check service account RBAC. | Update Helm/RBAC settings according to your chart deployment policy. |

## Scaling Recommendations

* Use pod-level scraping for node-local workloads and service discovery for cluster-level scrape loops intentionally.
* Keep metric allow-lists small at first and expand only after reviewing cardinality.
* Watch Collector CPU and memory when increasing scrape target count or reducing scrape interval.

## Security and Operations Notes

* Do not put Splunk tokens directly in values files.
* Avoid scraping endpoints that expose secrets or customer data as labels.
* Use Kubernetes secrets and existing chart secret-management conventions.

## Configuration Source Basis

This cookbook adapts the local Helm values file, Splunk OTel Collector Helm chart patterns, and Prometheus receiver Kubernetes discovery concepts into an existing Helm release workflow.

## Official Documentation

* https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-for-kubernetes
* https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver
