package health

import (
	"context"
	"os"
	"path/filepath"
	"testing"
)

func loadShippedRules(t *testing.T) *Config {
	t.Helper()
	c, err := LoadRules(filepath.Join("..", "..", "rules.yaml"))
	if err != nil {
		t.Fatalf("shipped rules.yaml: %v", err)
	}
	return c
}

func TestShippedRulesParse(t *testing.T) {
	c := loadShippedRules(t)
	want := map[string]Severity{"xid79": Critical, "xid48": Critical, "row-remap-failure": Critical,
		"sbe-rate": Warning, "pcie-replay-rate": Warning, "periodic-diag-failed": Warning, "straggler": Warning,
		"rail-link-down": Warning, "link-flapping": Warning}
	got := map[string]Severity{}
	for _, r := range c.Rules {
		got[r.Name] = r.Severity
	}
	for name, sev := range want {
		if got[name] != sev {
			t.Errorf("rule %s: severity %q, want %q", name, got[name], sev)
		}
	}
}

func TestParseRejectsBadRules(t *testing.T) {
	for _, y := range []string{
		"rules: [{name: a, severity: fatal, expr: up, action: drain_now}]",
		"rules: [{name: a, severity: critical, expr: up, action: explode}]",
		"rules: [{name: a, severity: warning, type: straggler, action: drain_now}]",
		"rules: [{name: a, severity: warning, expr: up, action: drain_now}, {name: a, severity: warning, expr: up, action: drain_now}]",
	} {
		if _, err := ParseRules([]byte(y)); err == nil {
			t.Errorf("accepted bad rules: %s", y)
		}
	}
}

// Recorded series: XID 79 appears on node-b at evaluation 3.
func TestCriticalFiresOnFirstHit(t *testing.T) {
	src := newRecorded()
	src.set("DCGM_FI_DEV_XID_ERRORS == 79", nil, nil, []Sample{s("node-b", 79)})
	e := NewEngine(loadShippedRules(t), src)
	ctx := context.Background()
	for i := 0; i < 2; i++ {
		if f, _ := e.Evaluate(ctx, clockAt(i)); len(f) != 0 {
			t.Fatalf("eval %d: unexpected firing %+v", i, f)
		}
	}
	f, _ := e.Evaluate(ctx, clockAt(2))
	if len(f) != 1 || f[0].Node != "node-b" || f[0].Rule.Name != "xid79" {
		t.Fatalf("want xid79 on node-b, got %+v", f)
	}
}

func TestSingleTransientWarningDoesNotFire(t *testing.T) {
	src := newRecorded()
	// PCIe replay rate high for 2 evaluations, gone, then high for 2 again: never 3 in a row.
	hit := []Sample{s("node-a", 50)}
	src.set("DCGM_FI_DEV_PCIE_REPLAY_COUNTER", hit, hit, nil, hit, hit, nil)
	e := NewEngine(loadShippedRules(t), src)
	for i := 0; i < 6; i++ {
		f, _ := e.Evaluate(context.Background(), clockAt(i))
		for _, x := range f {
			if x.Rule.Name == "pcie-replay-rate" {
				t.Fatalf("eval %d: transient warning fired", i)
			}
		}
	}
}

func TestSustainedWarningFiresAfterFor(t *testing.T) {
	src := newRecorded()
	hit := []Sample{s("node-a", 50)}
	src.set("DCGM_FI_DEV_PCIE_REPLAY_COUNTER", hit, hit, hit)
	e := NewEngine(loadShippedRules(t), src)
	var fired bool
	for i := 0; i < 3; i++ {
		f, _ := e.Evaluate(context.Background(), clockAt(i))
		for _, x := range f {
			if x.Rule.Name == "pcie-replay-rate" {
				fired = true
				if i != 2 {
					t.Fatalf("fired at eval %d, want 2", i)
				}
				if x.Since != clockAt(0) {
					t.Errorf("Since = %v, want first hit", x.Since)
				}
			}
		}
	}
	if !fired {
		t.Fatal("sustained warning never fired")
	}
}

func TestStragglerDetection(t *testing.T) {
	got := Stragglers([]Sample{s("a", 1.00), s("b", 1.02), s("c", 0.99), s("d", 1.30)}, 0.15, 2)
	if len(got) != 1 || got["d"] < 1.25 {
		t.Fatalf("want only d flagged, got %v", got)
	}
	if got := Stragglers([]Sample{s("a", 1), s("b", 5)}, 0.15, 3); got != nil {
		t.Fatalf("below min_peers must not flag, got %v", got)
	}
}

func TestStragglerRuleUsesWindowedAverage(t *testing.T) {
	src := newRecorded()
	src.set("avg_over_time(al_train_step_time_seconds[5m])", []Sample{s("a", 1.0), s("b", 1.0), s("c", 1.5)})
	cfg, _ := ParseRules([]byte(`rules: [{name: straggler, severity: warning, type: straggler,
  metric: al_train_step_time_seconds, margin: 0.15, window: 5m, for: 2, action: drain_after_jobs}]`))
	e := NewEngine(cfg, src)
	f1, _ := e.Evaluate(context.Background(), clockAt(0))
	f2, _ := e.Evaluate(context.Background(), clockAt(1))
	if len(f1) != 0 || len(f2) != 1 || f2[0].Node != "c" {
		t.Fatalf("want c flagged on 2nd eval: %v / %v", f1, f2)
	}
}

func TestEpilogEventFiresOnce(t *testing.T) {
	e := NewEngine(loadShippedRules(t), newRecorded())
	e.PushEvent(Event{Node: "node-c", XIDs: []int{13, 79}})
	f, _ := e.Evaluate(context.Background(), clockAt(0))
	if len(f) != 1 || f[0].Rule.Name != "epilog-xid-critical" || f[0].Node != "node-c" {
		t.Fatalf("want epilog rule on node-c, got %+v", f)
	}
	if f, _ := e.Evaluate(context.Background(), clockAt(1)); len(f) != 0 {
		t.Fatalf("event must be consumed once, got %+v", f)
	}
	e.PushEvent(Event{Node: "node-c", XIDs: []int{13}}) // XID 13 is not critical here
	if f, _ := e.Evaluate(context.Background(), clockAt(2)); len(f) != 0 {
		t.Fatalf("non-critical XID fired: %+v", f)
	}
}

func TestQueryErrorKeepsStreak(t *testing.T) {
	src := newRecorded()
	hit := []Sample{s("node-a", 50)}
	src.set("DCGM_FI_DEV_PCIE_REPLAY_COUNTER", hit, hit, hit, hit)
	e := NewEngine(loadShippedRules(t), src)
	e.Evaluate(context.Background(), clockAt(0))
	e.Evaluate(context.Background(), clockAt(1))
	src.fail["rate(DCGM_FI_DEV_PCIE_REPLAY_COUNTER[10m]) * 60 > 10"] = true
	_, errs := e.Evaluate(context.Background(), clockAt(2))
	if len(errs) == 0 {
		t.Fatal("want a query error")
	}
	delete(src.fail, "rate(DCGM_FI_DEV_PCIE_REPLAY_COUNTER[10m]) * 60 > 10")
	f, _ := e.Evaluate(context.Background(), clockAt(3))
	if len(f) != 1 {
		t.Fatalf("streak should survive a failed query, got %+v", f)
	}
}

// --- controller -----------------------------------------------------------

func TestCriticalDrainsBothSchedulersAndOpensIncident(t *testing.T) {
	src := newRecorded()
	src.set("DCGM_FI_DEV_XID_ERRORS == 79", []Sample{s("n1", 79)})
	slurm, k8s := newFakeSched("slurm", "n1", "n2"), newFakeSched("kubernetes", "n1", "n2")
	c, inc, _ := newController(loadShippedRules(t), src, 4, slurm, k8s)
	ds := c.Tick(context.Background())
	if len(ds) != 1 || ds[0].Outcome != "drained" {
		t.Fatalf("decisions: %+v", ds)
	}
	for _, f := range []*fakeSched{slurm, k8s} {
		if len(f.drains) != 1 || f.drains[0] != "n1:healthd:xid79:now" {
			t.Errorf("%s drains = %v", f.name, f.drains)
		}
	}
	if len(inc.reports) != 1 || inc.reports[0].Node != "n1" || inc.reports[0].Severity != Critical {
		t.Errorf("incidents = %+v", inc.reports)
	}
	// Next tick: still firing, but already drained -> no second drain, no second incident.
	ds = c.Tick(context.Background())
	if len(slurm.drains) != 1 || len(inc.reports) != 1 {
		t.Fatalf("not idempotent: drains=%v incidents=%d decisions=%+v", slurm.drains, len(inc.reports), ds)
	}
}

func TestDrainBudgetStopsBurst(t *testing.T) {
	src := newRecorded()
	src.set("DCGM_FI_DEV_XID_ERRORS == 79", []Sample{s("n1", 79), s("n2", 79), s("n3", 79), s("n4", 79)})
	slurm := newFakeSched("slurm", "n1", "n2", "n3", "n4")
	c, inc, clk := newController(loadShippedRules(t), src, 4, slurm) // 25% of 4 = 1 per hour
	ds := c.Tick(context.Background())
	var drained, blocked int
	for _, d := range ds {
		switch d.Outcome {
		case "drained":
			drained++
		case "budget-blocked":
			blocked++
		}
	}
	if drained != 1 || blocked != 3 || len(slurm.drains) != 1 {
		t.Fatalf("drained=%d blocked=%d drains=%v", drained, blocked, slurm.drains)
	}
	if len(inc.reports) != 4 { // 1 drain + 3 "needs a human"
		t.Fatalf("want 4 incidents, got %d", len(inc.reports))
	}
	// An hour later the budget has room again, but the blocked streaks were
	// already handled; a new streak is needed (no silent retry storm).
	clk.t = clk.t.Add(61 * 60e9)
	src.set("DCGM_FI_DEV_XID_ERRORS == 79", nil)
	c.Tick(context.Background())
	src.set("DCGM_FI_DEV_XID_ERRORS == 79", []Sample{s("n2", 79)})
	src.calls = map[string]int{}
	ds = c.Tick(context.Background())
	if len(ds) != 1 || ds[0].Outcome != "drained" {
		t.Fatalf("after window: %+v", ds)
	}
}

func TestDryRunNeverDrains(t *testing.T) {
	src := newRecorded()
	src.set("DCGM_FI_DEV_XID_ERRORS == 79", []Sample{s("n1", 79)})
	slurm := newFakeSched("slurm", "n1")
	c, inc, _ := newController(loadShippedRules(t), src, 4, slurm)
	c.Opts.DryRun = true
	ds := c.Tick(context.Background())
	if ds[0].Outcome != "dry-run" || len(slurm.drains) != 0 || len(inc.reports) != 0 {
		t.Fatalf("dry-run acted: %+v drains=%v", ds, slurm.drains)
	}
}

func TestWarningDrainsAfterJobs(t *testing.T) {
	src := newRecorded()
	hit := []Sample{s("n1", 50)}
	src.set("DCGM_FI_DEV_PCIE_REPLAY_COUNTER", hit, hit, hit)
	slurm := newFakeSched("slurm", "n1")
	c, _, clk := newController(loadShippedRules(t), src, 4, slurm)
	for i := 0; i < 3; i++ {
		c.Tick(context.Background())
		clk.tick()
	}
	if len(slurm.drains) != 1 || slurm.drains[0] != "n1:healthd:pcie-replay-rate:after-jobs" {
		t.Fatalf("drains = %v", slurm.drains)
	}
}

func TestSchedulerErrorIsRetried(t *testing.T) {
	src := newRecorded()
	src.set("DCGM_FI_DEV_XID_ERRORS == 79", []Sample{s("n1", 79)})
	slurm := newFakeSched("slurm", "n1")
	slurm.fail = true
	c, _, clk := newController(loadShippedRules(t), src, 8, slurm)
	if ds := c.Tick(context.Background()); ds[0].Outcome != "error" {
		t.Fatalf("want error, got %+v", ds)
	}
	slurm.fail = false
	clk.tick()
	if ds := c.Tick(context.Background()); ds[0].Outcome != "drained" {
		t.Fatalf("retry should drain, got %+v", ds)
	}
}

func TestMain(m *testing.M) { os.Exit(m.Run()) }
