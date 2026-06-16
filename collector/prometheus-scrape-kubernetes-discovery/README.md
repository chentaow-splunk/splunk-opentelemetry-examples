# Prometheus Scraping with Kubernetes Discovery

## Scenario

Use this recipe when Kubernetes workloads expose Prometheus-format metrics and you want the Splunk OTel Collector Helm deployment to discover scrape targets inside the cluster.

This pattern covers label-selected pod scraping with `receiver_creator` and annotation-selected service scraping with the Prometheus receiver's Kubernetes service discovery. Do not use this recipe when you need full Prometheus server features such as rules, Alertmanager configuration, remote read, or remote write in the Collector.

## Architecture Overview

```text
Kubernetes pods and services
  -> k8s observer or Prometheus kubernetes_sd_configs
  -> prometheus receiver scrape jobs
  -> memory_limiter, resourcedetection, resource, batch
  -> signalfx exporter from the Splunk OTel Collector chart
  -> Splunk Observability Cloud metrics ingest
```

The agent pattern keeps pod scraping close to each node. The cluster receiver pattern is useful for service discovery where a single cluster-level scrape loop is acceptable.

## Prerequisites

* A Kubernetes cluster where the Splunk OTel Collector Helm chart is already used or approved.
* Splunk Observability Cloud realm, access token, cluster name, and environment value.
* Kubernetes RBAC that lets the Collector observe pods and services. The required permissions depend on your Helm chart configuration and enabled receivers.
* Workloads labelled with `observability.splunk.com/scrape=true` for pod scraping, or services annotated with `prometheus.io/scrape=true` for service scraping.
* A reviewed metric allow-list for each scrape job.

## Installation Instructions

1. Create or reuse a Kubernetes secret for the Splunk access token according to your existing chart practice.
2. Review [values.yaml](./values.yaml) and replace `<cluster_name>` and `<environment_name>`.
3. Label a pod-owning workload for pod scraping:

   ```bash
   kubectl label deployment <deployment_name> observability.splunk.com/scrape=true
   ```

4. Annotate a service for service scraping when needed:

   ```bash
   kubectl annotate service <service_name> prometheus.io/scrape=true
   kubectl annotate service <service_name> prometheus.io/port='<metrics_port>'
   kubectl annotate service <service_name> prometheus.io/path='/metrics'
   ```

5. Apply the Helm values:

   ```bash
   helm upgrade --install splunk-otel-collector \
     splunk-otel-collector-chart/splunk-otel-collector \
     --namespace splunk-otel-collector \
     --create-namespace \
     --set splunkObservability.realm='<realm>' \
     --set secret.create=false \
     --set secret.name='splunk-secret' \
     -f values.yaml
   ```

Adjust the command to match your existing Helm release name and secret management.

## Proposed Configuration File

Use [values.yaml](./values.yaml) as the chart overlay. The pod discovery section follows the same `receiver_creator` pattern used by local GPU metric examples:

```yaml
agent:
  config:
    receivers:
      receiver_creator/prometheus_pods:
        watch_observers: [k8s_observer]
        receivers:
          prometheus:
            rule: type == "pod" && labels["observability.splunk.com/scrape"] == "true"
```

## Validation

### Before Applying

* Confirm the workloads selected for scraping expose `/metrics` from inside the cluster, for example by using an existing debug pod or approved cluster troubleshooting workflow.
* Check the current labels and annotations on the target workload and service. Record whether `observability.splunk.com/scrape=true` or `prometheus.io/scrape=true` is already present.
* Review existing Collector pod logs for Kubernetes authorization, discovery, scrape, or `signalfx` export errors before changing Helm values.
* In Splunk Observability Cloud Metric Finder, search for one allowed metric from the workload, such as `http_server_requests_total`, and note whether it is absent, duplicated, or missing expected Kubernetes dimensions.

Expected baseline result:

```text
kubectl logs: no receiver_creator/prometheus_pods or prometheus/kubernetes_services receiver for the target, or discovery/RBAC errors are visible.
Metric Finder: selected workload metrics are absent, duplicated by a different scraper, or missing Kubernetes dimensions.
Kubernetes metadata: pods lack observability.splunk.com/scrape=true, or services lack prometheus.io/scrape=true.
```

### After Applying

* After the Helm upgrade, run `kubectl logs` for the agent and cluster receiver pods and check for configuration, RBAC, discovery, scrape, or exporter errors involving `receiver_creator/prometheus_pods`, `prometheus/kubernetes_services`, `k8s_observer`, or `signalfx`.
* Verify that labelled pods and annotated services are being selected by the intended discovery path. If both pod and service scraping are enabled for the same endpoint, check for duplicate series before rolling out broadly.
* In Metric Finder, search for a metric allowed by the relevant `metric_relabel_configs` rule and confirm it has the expected Kubernetes and environment context from the chart and resource processors.
* In a non-production namespace, remove the scrape label or annotation from a test target and confirm future samples from that target stop arriving after the scrape interval and ingest delay.

Expected post-change result:

```text
kubectl logs: receiver_creator/prometheus_pods and prometheus/kubernetes_services load without discovery or scrape errors.
Metric Finder: allowed workload metrics arrive with cluster, namespace, pod/service, and environment context.
Metric Finder: removing the scrape label or annotation from a test target stops new samples from that target after normal scrape and ingest delay.
```

### Live Local Validation Result

Validated with `scripts/validate_collector_cookbooks.py` using `quay.io/signalfx/splunk-otel-collector:latest`, a synthetic Prometheus endpoint, and the Collector `debug` exporter. The local run validates the scrape, relabel, processor, and export behavior with a static target equivalent; Kubernetes API discovery still requires cluster validation.

Status: `PASS`

Observed before:

```text
Synthetic endpoint exposed an application metric and a runtime metric.
```

Observed after:

```text
debug exporter output contained the application metric and excluded the runtime metric by relabel rule.
```

## Why This Configuration

The recipe separates pod and service discovery because their ownership models differ. Pod scraping is node-local and label-driven. Service scraping uses Prometheus Kubernetes service discovery and standard Prometheus relabeling.

Metric allow-listing is done in `metric_relabel_configs` so unwanted series are dropped before they enter later Collector processors or the Splunk exporter.

## Troubleshooting

If no pod metrics arrive, confirm discovery is enabled and the pod has the exact `observability.splunk.com/scrape=true` label.

If service metrics do not arrive, confirm the service annotations and port are correct. The relabel rule rewrites `__address__` using `prometheus.io/port`; a missing or wrong annotation points the scrape at the wrong port.

If the Collector reports Kubernetes authorization errors, review the Helm chart RBAC settings before changing receiver config.

If duplicate metrics appear, check whether both the pod and service jobs are scraping the same endpoint.

## Scaling Recommendations

Start with narrow labels and annotations. Do not enable broad namespace-wide scraping until metric volume and cardinality have been reviewed.

For high-volume targets, prefer node-local agent scraping to reduce cross-node traffic. For service-level targets that should be scraped once per cluster, keep them in the cluster receiver.

Avoid multiple identical cluster receiver replicas scraping the same service set unless you intentionally want duplicate scrapes or have a target allocation strategy.

## Security and Operations Notes

Treat Kubernetes labels and annotations as operational control surfaces. Limit who can add scrape-enabling labels or annotations in production namespaces.

Do not put application credentials in service annotations. Use Kubernetes secrets and supported Prometheus authentication settings for authenticated targets.

Keep metric allow-lists reviewed. Kubernetes and application labels can create high-cardinality dimensions quickly.

## Configuration Source Basis

This recipe combines two real-world Kubernetes scrape patterns: pod selection through Collector receiver creation and service selection through Prometheus Kubernetes service discovery. The `kubernetes_sd_configs`, annotation relabeling, and `metric_relabel_configs` blocks follow Prometheus discovery conventions and the OpenTelemetry Collector Prometheus receiver documentation.

The Helm shape follows local Splunk OTel Collector values examples in this backend, where agent and cluster receiver config are extended under chart values. The labels and annotations are deliberately explicit so platform teams can control which workloads become scrape targets.

## Official Documentation

* [Splunk Prometheus receiver](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/receivers/prometheus-receiver)
* [OpenTelemetry Collector Prometheus receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver)
* [Prometheus Kubernetes service discovery configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#kubernetes_sd_config)
