package health

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

// Options are the controller's safety settings.
type Options struct {
	Interval time.Duration
	// DryRun logs decisions and records metrics but never touches a scheduler.
	DryRun bool
	// Drain budget: at most max(BudgetMin, floor(BudgetFraction x fleet)) drains
	// per BudgetWindow without a human.
	BudgetMin      int
	BudgetFraction float64
	BudgetWindow   time.Duration
	// Cooldown suppresses repeat incidents for the same rule and node.
	Cooldown time.Duration
	// FleetSize returns the number of GPU nodes the budget is computed over.
	FleetSize func(ctx context.Context) int
}

// DefaultOptions match the spec: 1 node or 25% of the fleet per hour.
func DefaultOptions() Options {
	return Options{Interval: 10 * time.Second, BudgetMin: 1, BudgetFraction: 0.25,
		BudgetWindow: time.Hour, Cooldown: 30 * time.Minute}
}

// Metrics exported by healthd itself.
type Metrics struct {
	TimeToDetect  *prometheus.HistogramVec
	TimeToDrain   *prometheus.HistogramVec
	Drains        *prometheus.CounterVec
	BudgetBlocked *prometheus.CounterVec
	Firing        *prometheus.GaugeVec
	NodeState     *prometheus.GaugeVec
	EvalErrors    prometheus.Counter
}

// NewMetrics registers healthd's metrics on reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	buckets := []float64{1, 2, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 300, 600}
	m := &Metrics{
		TimeToDetect: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "healthd_time_to_detect_seconds", Help: "Condition first seen true -> rule fired.",
			Buckets: buckets}, []string{"rule", "severity"}),
		TimeToDrain: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name: "healthd_time_to_drain_seconds", Help: "Rule fired -> node drained in every scheduler.",
			Buckets: buckets}, []string{"rule", "severity"}),
		Drains: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "healthd_drains_total", Help: "Nodes drained by healthd."}, []string{"rule", "severity", "dry_run"}),
		BudgetBlocked: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "healthd_budget_blocked_total", Help: "Drains refused by the drain budget."}, []string{"rule"}),
		Firing: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "healthd_rule_firing", Help: "1 while a rule fires on a node."}, []string{"rule", "node"}),
		NodeState: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "healthd_node_state", Help: "Lifecycle state per node (1 = current)."}, []string{"node", "state"}),
		EvalErrors: prometheus.NewCounter(prometheus.CounterOpts{
			Name: "healthd_eval_errors_total", Help: "Rule evaluation errors."}),
	}
	reg.MustRegister(m.TimeToDetect, m.TimeToDrain, m.Drains, m.BudgetBlocked, m.Firing, m.NodeState, m.EvalErrors)
	return m
}

type budgetEntry struct {
	node string
	at   time.Time
}

// Decision records what the controller did with one firing (for logs and tests).
type Decision struct {
	Firing   Firing
	Outcome  string // drained | dry-run | already-drained | budget-blocked | cooldown | error
	Incident string
	Err      error
}

// Controller turns firings into drains and incidents.
type Controller struct {
	Engine     *Engine
	Schedulers []Scheduler
	Incident   Incident
	Evidence   Evidence
	Opts       Options
	Metrics    *Metrics
	Log        *slog.Logger
	Now        func() time.Time

	mu        sync.Mutex
	drainLog  []budgetEntry        // nodes drained inside the budget window
	handled   map[string]bool      // rule/node acted on during the current streak
	lastIncid map[string]time.Time // rule/node -> last incident
	firingSet map[string]bool
}

// Start runs the loop until ctx ends (controller-runtime Runnable).
func (c *Controller) Start(ctx context.Context) error {
	t := time.NewTicker(c.Opts.Interval)
	defer t.Stop()
	for {
		c.Tick(ctx)
		select {
		case <-ctx.Done():
			return nil
		case <-t.C:
		}
	}
}

// NeedLeaderElection: only the leader acts.
func (c *Controller) NeedLeaderElection() bool { return true }

func (c *Controller) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	return time.Now()
}

// Tick evaluates every rule once and acts on what fires.
func (c *Controller) Tick(ctx context.Context) []Decision {
	now := c.now()
	firings, errs := c.Engine.Evaluate(ctx, now)
	for _, err := range errs {
		c.Metrics.EvalErrors.Inc()
		c.Log.Warn("rule evaluation failed", "err", err)
	}
	c.updateFiringGauge(firings)
	var out []Decision
	for _, f := range firings {
		d := c.handle(ctx, f, now)
		c.Log.Info("decision", "rule", f.Rule.Name, "node", f.Node, "severity", f.Rule.Severity,
			"outcome", d.Outcome, "value", f.Value, "incident", d.Incident, "err", d.Err)
		out = append(out, d)
	}
	return out
}

func (c *Controller) handle(ctx context.Context, f Firing, now time.Time) Decision {
	d := Decision{Firing: f}
	key := f.Rule.Name + "/" + f.Node
	c.mu.Lock()
	done := c.handled[key]
	c.mu.Unlock()
	if done {
		d.Outcome = "handled"
		return d
	}
	defer func() {
		if d.Outcome != "error" {
			c.mu.Lock()
			if c.handled == nil {
				c.handled = map[string]bool{}
			}
			c.handled[key] = true
			c.mu.Unlock()
		}
	}()

	// Idempotency: if every scheduler already has the node drained by healthd, do nothing.
	if c.alreadyDrained(ctx, f.Node) {
		d.Outcome = "already-drained"
		return d
	}
	reason := "healthd:" + f.Rule.Name
	sev := string(f.Rule.Severity)
	c.Metrics.TimeToDetect.WithLabelValues(f.Rule.Name, sev).Observe(now.Sub(f.Since).Seconds())

	if c.Opts.DryRun {
		d.Outcome = "dry-run"
		c.Metrics.Drains.WithLabelValues(f.Rule.Name, sev, "true").Inc()
		return d
	}
	if !c.takeBudget(ctx, f.Node, now) {
		d.Outcome = "budget-blocked"
		c.Metrics.BudgetBlocked.WithLabelValues(f.Rule.Name).Inc()
		d.Incident, d.Err = c.openIncident(ctx, f, now, "budget-blocked",
			fmt.Sprintf("Drain budget exhausted: %s wants to drain %s but needs a human.", f.Rule.Name, f.Node), key)
		return d
	}
	var errs []string
	for _, s := range c.Schedulers {
		if err := s.Drain(ctx, f.Node, reason, f.Rule.Mode()); err != nil {
			errs = append(errs, s.Name()+": "+err.Error())
		}
	}
	drainedAt := c.now()
	if len(errs) > 0 {
		d.Outcome = "error"
		d.Err = fmt.Errorf("%s", strings.Join(errs, "; "))
	} else {
		d.Outcome = "drained"
		c.Metrics.TimeToDrain.WithLabelValues(f.Rule.Name, sev).Observe(drainedAt.Sub(now).Seconds())
		c.Metrics.Drains.WithLabelValues(f.Rule.Name, sev, "false").Inc()
		state := "Draining"
		if f.Rule.Action == "drain_now_repair" {
			state = "Repair"
		}
		c.setNodeState(f.Node, state)
	}
	summary := fmt.Sprintf("%s on %s (value %g): drained %s in %d scheduler(s). %s",
		f.Rule.Name, f.Node, f.Value, f.Rule.Mode(), len(c.Schedulers), f.Rule.Description)
	incID, err := c.openIncident(ctx, f, now, d.Outcome, summary, key)
	d.Incident = incID
	if d.Err == nil {
		d.Err = err
	}
	return d
}

func (c *Controller) alreadyDrained(ctx context.Context, node string) bool {
	if len(c.Schedulers) == 0 {
		return false
	}
	for _, s := range c.Schedulers {
		st, err := s.State(ctx, node)
		if err != nil || !st.Exists {
			continue // a scheduler that does not know the node cannot veto
		}
		if !st.Drained || !strings.HasPrefix(st.Reason, "healthd:") {
			return false
		}
	}
	return true
}

// takeBudget records a drain if the rolling window still has room. The budget
// counts distinct nodes, so re-draining a node already drained is free.
func (c *Controller) takeBudget(ctx context.Context, node string, now time.Time) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	cut := now.Add(-c.Opts.BudgetWindow)
	kept := c.drainLog[:0]
	for _, e := range c.drainLog {
		if e.at.After(cut) {
			kept = append(kept, e)
		}
	}
	c.drainLog = kept
	for _, e := range c.drainLog {
		if e.node == node {
			return true
		}
	}
	fleet := 0
	if c.Opts.FleetSize != nil {
		fleet = c.Opts.FleetSize(ctx)
	}
	limit := int(c.Opts.BudgetFraction * float64(fleet))
	if limit < c.Opts.BudgetMin {
		limit = c.Opts.BudgetMin
	}
	if len(c.drainLog) >= limit {
		return false
	}
	c.drainLog = append(c.drainLog, budgetEntry{node: node, at: now})
	return true
}

func (c *Controller) openIncident(ctx context.Context, f Firing, now time.Time, action, summary, key string) (string, error) {
	c.mu.Lock()
	if c.lastIncid == nil {
		c.lastIncid = map[string]time.Time{}
	}
	if t, ok := c.lastIncid[key]; ok && now.Sub(t) < c.Opts.Cooldown {
		c.mu.Unlock()
		return "", nil
	}
	c.lastIncid[key] = now
	c.mu.Unlock()
	if c.Incident == nil {
		return "", nil
	}
	ev := map[string]string{}
	if c.Evidence != nil {
		ev = c.Evidence.Collect(ctx, f.Node, now)
	}
	return c.Incident.Open(ctx, IncidentReport{
		Node: f.Node, Rule: f.Rule.Name, Severity: f.Rule.Severity, Action: action,
		Summary: summary, Evidence: ev, Time: now,
	})
}

func (c *Controller) setNodeState(node, state string) {
	for _, s := range []string{"Discovered", "Provisioning", "Burn-in", "Ready", "Draining", "Repair", "Retired"} {
		v := 0.0
		if s == state {
			v = 1
		}
		c.Metrics.NodeState.WithLabelValues(node, s).Set(v)
	}
}

func (c *Controller) updateFiringGauge(firings []Firing) {
	c.mu.Lock()
	defer c.mu.Unlock()
	now := map[string]bool{}
	for _, f := range firings {
		now[f.Rule.Name+"\x00"+f.Node] = true
		c.Metrics.Firing.WithLabelValues(f.Rule.Name, f.Node).Set(1)
	}
	for k := range c.firingSet {
		if !now[k] {
			p := strings.SplitN(k, "\x00", 2)
			c.Metrics.Firing.WithLabelValues(p[0], p[1]).Set(0)
			delete(c.handled, p[0]+"/"+p[1]) // streak ended: a new one is a new incident
		}
	}
	c.firingSet = now
}
