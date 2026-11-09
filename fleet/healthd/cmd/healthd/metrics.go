package main

import (
	"net/http"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	ctrlmetrics "sigs.k8s.io/controller-runtime/pkg/metrics"
)

func metricsHandler() http.Handler {
	return promhttp.HandlerFor(ctrlmetrics.Registry, promhttp.HandlerOpts{})
}
