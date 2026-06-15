# Prometheus Scraping to Splunk

## Scenario

Use this recipe when an operations team needs the Splunk Distribution of the OpenTelemetry Collector to scrape Prometheus-format `/metrics` endpoints and export those metrics to Splunk Observability Cloud.

This pattern fits application endpoints, vendor exporters, authenticated HTTPS appliances, and metric allow-listing before export. Do not use this recipe when you need Prometheus alerting rules, remote read, or remote write behavior inside the Collector; the upstream Prometheus receiver documentation calls out unsupported advanced Prometheus server features.

## Architecture Overview

```text
Prometheus /metrics targets
  -> Splunk OTel Collector prometheus receiver
  -> metric_relabel_configs allow-listing
  -> memory_limiter, resourcedetection, resource/splunk_context, batch
  -> signalfx exporter
  -> Splunk Observability Cloud metrics ingest
```

The Collector owns scraping, enrichment, and export. Application teams keep exposing Prometheus metrics, while platform teams centralize Splunk access token handling and consistent resource attributes.

## Prerequisites

* Splunk Observability Cloud realm, access token, API URL, and ingest URL.
* Network access from the Collector host to each scrape target and to `https://api.<realm>.signalfx.com` and `https://ingest.<realm>.signalfx.com`.
* A Collector build that includes the `prometheus` receiver, `memory_limiter`, `resourcedetection`, `resource`, `batch`, and `signalfx` components. The Splunk Distribution component list includes these components.
* For authenticated HTTPS targets, a readable bearer token file or another Prometheus-supported authentication block, plus any required CA certificate.
* A reviewed metric allow-list. The example allow-list is intentionally narrow and must be changed for your target names.

## Installation Instructions

1. Copy [otelcol.yaml](./otelcol.yaml) to the Collector host.
2. Replace the placeholder targets, CA path, bearer token path, and `metric_relabel_configs` with values for your environment.
3. Export Splunk and environment settings:

   ```bash
   export SPLUNK_ACCESS_TOKEN='<splunk_access_token>'
   export SPLUNK_API_URL='https://api.<realm>.signalfx.com'
   export SPLUNK_INGEST_URL='https://ingest.<realm>.signalfx.com'
   export DEPLOYMENT_ENVIRONMENT='<environment_name>'
   ```

4. Start the Collector:

   ```bash
   docker run --rm --name splunk-otel-collector \
     -p 8888:8888 \
     -e SPLUNK_CONFIG=/etc/collector/otelcol.yaml \
     -e SPLUNK_ACCESS_TOKEN \
     -e SPLUNK_API_URL \
     -e SPLUNK_INGEST_URL \
     -e DEPLOYMENT_ENVIRONMENT \
     -v "$(pwd)/otelcol.yaml:/etc/collector/otelcol.yaml:ro" \
     -v "$(pwd)/secrets:/etc/collector/secrets:ro" \
     -v "$(pwd)/certs:/etc/collector/certs:ro" \
     quay.io/signalfx/splunk-otel-collector:latest
   ```

Pin the Collector image version for production rollouts after testing the exact component syntax you deploy.

## Proposed Configuration File

Use [otelcol.yaml](./otelcol.yaml) as the starting point. The key receiver pattern is:

```yaml
receivers:
  prometheus/static_targets:
    config:
      scrape_configs:
        - job_name: app-metrics
          static_configs:
            - targets:
                - app-1.example.internal:8080
          metric_relabel_configs:
            - source_labels: [__name__]
              regex: "(http_server_request_duration_seconds.*|http_server_requests_total)"
              action: keep
```

## Validation

### Before Applying

* From the Collector host, confirm each planned target exposes the expected `/metrics` endpoint and that the metric names you intend to keep are present.
* Review the current Collector logs, if a Collector is already running, for scrape or export failures. Record whether there is already a Prometheus scrape job for these targets and whether exporter errors are present before changing the configuration.
* If Collector self-telemetry is enabled, check it for scrape failures before rollout so target reachability issues are not mistaken for Splunk export problems.
* In Splunk Observability Cloud Metric Finder, search for one metric expected to match the allow-list, such as `http_server_requests_total` or `appliance_requests_total`, and note whether it is absent or missing expected resource dimensions.

### After Applying

* Start the Collector with [otelcol.yaml](./otelcol.yaml) and review logs for configuration or startup errors involving `prometheus/static_targets`, `memory_limiter`, `resource/splunk_context`, or the `signalfx` exporter.
* Watch Collector logs, and self-telemetry when enabled, for scrape failures from the `app-metrics` or `https-appliance` jobs and for exporter send errors.
* In Metric Finder, search for a metric kept by `metric_relabel_configs` and confirm it appears with the expected environment context, such as `deployment.environment` and `service.namespace=prometheus-scrapes`.
* In a non-production environment, compare a metric name outside the allow-list with one inside the allow-list. The kept metric should be available for charting, while the intentionally excluded metric should not be newly exported by this Collector configuration.

## Why This Configuration

The `prometheus` receiver keeps scrape configuration close to Prometheus conventions, including static targets, HTTPS settings, authentication, and relabeling. The `metric_relabel_configs` block reduces volume before export rather than sending unwanted series to Splunk.

`memory_limiter` protects the Collector during scrape bursts. `resourcedetection` and `resource/splunk_context` add stable context, and `batch` improves export efficiency. The `signalfx` exporter follows existing local examples for Splunk Observability Cloud metrics.

## Troubleshooting

If no metrics arrive, confirm the target is reachable from the Collector host and that `metrics_path`, scheme, and port are correct.

If HTTPS scrapes fail, check the CA file, `server_name`, and bearer token file permissions before considering `insecure_skip_verify`. Leave `insecure_skip_verify: false` for production unless your security team approves otherwise.

If expected metrics are missing, inspect `metric_relabel_configs`. A `keep` action drops every metric name that does not match the regex.

If Splunk export fails, verify `SPLUNK_ACCESS_TOKEN`, `SPLUNK_API_URL`, and `SPLUNK_INGEST_URL`.

## Scaling Recommendations

Keep scrape intervals realistic for the endpoint cost and metric volume. Split unrelated high-volume target groups into separate receiver instances so changes can be rolled out independently.

For many targets, run Collectors close to the targets and forward through a Splunk OTel Collector gateway only when you need central egress control. Avoid running multiple identical Prometheus scraper replicas against the same targets unless duplicate scrapes are acceptable or you have a sharding plan.

Review cardinality before adding labels to the allow-list. Dropping unneeded series at scrape time is cheaper than filtering after export.

## Security and Operations Notes

Use files or Kubernetes secrets for scrape credentials. Do not put bearer tokens or passwords directly in the committed YAML.

Treat scraped labels as customer data until reviewed. Prometheus labels can include hostnames, user identifiers, tenant IDs, and request parameters depending on the exporter.

Keep Collector logs at `info` by default. Use debug logging only for short troubleshooting windows because scrape and OTTL diagnostics can be verbose.

## Official Documentation

* [Splunk Prometheus receiver](https://help.splunk.com/en/splunk-observability-cloud/manage-data/splunk-distribution-of-the-opentelemetry-collector/get-started-with-the-splunk-distribution-of-the-opentelemetry-collector/collector-components/receivers/prometheus-receiver)
* [OpenTelemetry Collector Prometheus receiver](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver)
* [Prometheus scrape configuration](https://prometheus.io/docs/prometheus/latest/configuration/configuration/#scrape_config)
