package health

import (
	"context"
	"io"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

// recorded is a MetricSource that replays recorded series: for each expr,
// a list of per-evaluation results (index = evaluation number).
type recorded struct {
	mu     sync.Mutex
	series map[string][][]Sample
	calls  map[string]int
	fail   map[string]bool
}

func newRecorded() *recorded {
	return &recorded{series: map[string][][]Sample{}, calls: map[string]int{}, fail: map[string]bool{}}
}

func (r *recorded) set(expr string, steps ...[]Sample) { r.series[expr] = steps }

func (r *recorded) Query(_ context.Context, expr string, _ time.Time) ([]Sample, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.fail[expr] {
		return nil, context.DeadlineExceeded
	}
	for k, steps := range r.series {
		if strings.Contains(expr, k) {
			i := r.calls[k]
			r.calls[k]++
			if i < len(steps) {
				return steps[i], nil
			}
			return steps[len(steps)-1], nil
		}
	}
	return nil, nil
}

func s(node string, v float64) Sample { return Sample{Node: node, Value: v} }

type fakeSched struct {
	name   string
	mu     sync.Mutex
	state  map[string]NodeState
	drains []string
	fail   bool
}

func newFakeSched(name string, nodes ...string) *fakeSched {
	f := &fakeSched{name: name, state: map[string]NodeState{}}
	for _, n := range nodes {
		f.state[n] = NodeState{Exists: true}
	}
	return f
}

func (f *fakeSched) Name() string { return f.name }
func (f *fakeSched) Drain(_ context.Context, node, reason string, mode DrainMode) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail {
		return io.ErrUnexpectedEOF
	}
	f.drains = append(f.drains, node+":"+reason+":"+mode.String())
	f.state[node] = NodeState{Exists: true, Drained: true, Reason: reason}
	return nil
}
func (f *fakeSched) Undrain(_ context.Context, node string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.state[node] = NodeState{Exists: true}
	return nil
}
func (f *fakeSched) State(_ context.Context, node string) (NodeState, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.state[node], nil
}

type fakeIncident struct {
	mu      sync.Mutex
	reports []IncidentReport
}

func (f *fakeIncident) Open(_ context.Context, r IncidentReport) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.reports = append(f.reports, r)
	return "inc-" + r.Node, nil
}

type clock struct{ t time.Time }

func (c *clock) now() time.Time { return c.t }
func (c *clock) tick()          { c.t = c.t.Add(10 * time.Second) }

func newController(cfg *Config, src MetricSource, fleet int, scheds ...Scheduler) (*Controller, *fakeIncident, *clock) {
	inc := &fakeIncident{}
	clk := &clock{t: time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC)}
	opt := DefaultOptions()
	opt.FleetSize = func(context.Context) int { return fleet }
	c := &Controller{
		Engine: NewEngine(cfg, src), Schedulers: scheds, Incident: inc, Opts: opt,
		Metrics: NewMetrics(prometheus.NewRegistry()),
		Log:     slog.New(slog.NewTextHandler(io.Discard, nil)), Now: clk.now,
	}
	return c, inc, clk
}

func clockAt(i int) time.Time {
	return time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC).Add(time.Duration(i) * 10 * time.Second)
}
