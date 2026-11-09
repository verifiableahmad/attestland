// Package health holds healthd's rule engine, controller and the interfaces
// its adapters implement: MetricSource, Scheduler and Incident.
package health

import (
	"context"
	"time"
)

// Severity of a rule. Critical faults drain at once and evict; warnings wait
// for running work to finish.
type Severity string

const (
	Critical Severity = "critical"
	Warning  Severity = "warning"
)

// DrainMode tells a scheduler how hard to drain.
type DrainMode int

const (
	// DrainAfterJobs stops new work; running work finishes (warnings).
	DrainAfterJobs DrainMode = iota
	// DrainNow stops new work and evicts/requeues running work (critical).
	DrainNow
)

func (m DrainMode) String() string {
	if m == DrainNow {
		return "now"
	}
	return "after-jobs"
}

// Sample is one series value from a metric query, already mapped to a node.
type Sample struct {
	Node   string
	Value  float64
	Labels map[string]string
}

// MetricSource answers instant queries (Prometheus in production, fakes in tests).
type MetricSource interface {
	Query(ctx context.Context, expr string, at time.Time) ([]Sample, error)
}

// NodeState is what a scheduler reports about a node.
type NodeState struct {
	Exists  bool
	Drained bool
	Reason  string
}

// Scheduler drains and reports nodes. One adapter each for Slurm and Kubernetes.
type Scheduler interface {
	Name() string
	Drain(ctx context.Context, node, reason string, mode DrainMode) error
	Undrain(ctx context.Context, node string) error
	State(ctx context.Context, node string) (NodeState, error)
}

// IncidentReport is what healthd files when it acts (or is stopped from acting).
type IncidentReport struct {
	Node     string
	Rule     string
	Severity Severity
	Action   string
	Summary  string
	Evidence map[string]string // metric snapshot, dmesg excerpt, diag output
	Time     time.Time
}

// Incident files reports: Alertmanager, a GitHub issue, or a log line.
type Incident interface {
	Open(ctx context.Context, r IncidentReport) (string, error)
}

// Evidence gathers context to attach to an incident. Optional.
type Evidence interface {
	Collect(ctx context.Context, node string, at time.Time) map[string]string
}
