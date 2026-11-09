package health

import (
	"context"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

// Rule is one entry in rules.yaml: a condition, a severity, how many
// consecutive evaluations it must hold, and an action.
type Rule struct {
	Name     string   `yaml:"name"`
	Severity Severity `yaml:"severity"`
	// Type: "expr" (default, PromQL; every returned series is a hit),
	// "straggler" (per-node step time vs. the median), or "event" (pushed
	// by the Slurm epilog to /v1/events).
	Type string `yaml:"type"`
	Expr string `yaml:"expr"`
	For  int    `yaml:"for"`
	// Action: drain_now | drain_after_jobs | drain_now_repair
	Action string `yaml:"action"`
	// Straggler parameters.
	Metric string   `yaml:"metric"`
	Margin float64  `yaml:"margin"`
	Window Duration `yaml:"window"`
	// MinPeers is the fewest nodes needed before a median means anything.
	MinPeers int `yaml:"min_peers"`
	// Event parameters: XIDs that fire this rule.
	XIDs []int `yaml:"xids"`
	// Description shows up in incidents.
	Description string `yaml:"description"`
}

// Config is the whole rules file.
type Config struct {
	Rules []Rule `yaml:"rules"`
}

// LoadRules reads and validates a rules file.
func LoadRules(path string) (*Config, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return ParseRules(raw)
}

// ParseRules validates rules and fills defaults.
func ParseRules(raw []byte) (*Config, error) {
	var c Config
	if err := yaml.Unmarshal(raw, &c); err != nil {
		return nil, err
	}
	seen := map[string]bool{}
	for i := range c.Rules {
		r := &c.Rules[i]
		if r.Name == "" || seen[r.Name] {
			return nil, fmt.Errorf("rule %d: empty or duplicate name %q", i, r.Name)
		}
		seen[r.Name] = true
		if r.Type == "" {
			r.Type = "expr"
		}
		if r.For < 1 {
			r.For = 1
		}
		switch r.Severity {
		case Critical, Warning:
		default:
			return nil, fmt.Errorf("rule %s: severity must be critical or warning", r.Name)
		}
		switch r.Action {
		case "drain_now", "drain_after_jobs", "drain_now_repair":
		default:
			return nil, fmt.Errorf("rule %s: unknown action %q", r.Name, r.Action)
		}
		switch r.Type {
		case "expr":
			if r.Expr == "" {
				return nil, fmt.Errorf("rule %s: expr required", r.Name)
			}
		case "straggler":
			if r.Metric == "" || r.Margin <= 0 || r.Window <= 0 {
				return nil, fmt.Errorf("rule %s: straggler needs metric, margin > 0, window > 0", r.Name)
			}
			if r.MinPeers < 2 {
				r.MinPeers = 2
			}
		case "event":
			if len(r.XIDs) == 0 {
				return nil, fmt.Errorf("rule %s: event rule needs xids", r.Name)
			}
		default:
			return nil, fmt.Errorf("rule %s: unknown type %q", r.Name, r.Type)
		}
	}
	return &c, nil
}

// Duration parses "5m"-style strings in YAML.
type Duration time.Duration

// UnmarshalYAML implements yaml.Unmarshaler.
func (d *Duration) UnmarshalYAML(n *yaml.Node) error {
	v, err := time.ParseDuration(n.Value)
	if err != nil {
		return err
	}
	*d = Duration(v)
	return nil
}

// Mode maps a rule's action to a drain mode.
func (r Rule) Mode() DrainMode {
	if r.Action == "drain_after_jobs" {
		return DrainAfterJobs
	}
	return DrainNow
}

// Firing is a rule that has held for its required evaluations on a node.
type Firing struct {
	Rule  Rule
	Node  string
	Value float64
	// Since is when the condition was first seen true in this streak.
	Since time.Time
	At    time.Time
}

// Event is pushed to /v1/events (Slurm epilog XID report).
type Event struct {
	Node   string `json:"node"`
	Source string `json:"source"`
	Job    string `json:"job"`
	XIDs   []int  `json:"xids"`
	Count  int    `json:"count"`
	At     time.Time
}

type streak struct {
	count int
	since time.Time
	value float64
}

// Engine evaluates rules every tick and keeps per-(rule,node) streaks.
type Engine struct {
	cfg     *Config
	src     MetricSource
	mu      sync.Mutex
	streaks map[string]*streak
	events  []Event
}

// NewEngine builds an engine over a metric source.
func NewEngine(cfg *Config, src MetricSource) *Engine {
	return &Engine{cfg: cfg, src: src, streaks: map[string]*streak{}}
}

// PushEvent queues an epilog event for the next evaluation.
func (e *Engine) PushEvent(ev Event) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if ev.At.IsZero() {
		ev.At = time.Now()
	}
	e.events = append(e.events, ev)
}

// Evaluate runs every rule once and returns the rules that now fire.
// A condition must hold for Rule.For consecutive evaluations; one false
// evaluation resets the streak (flap damping for warnings).
func (e *Engine) Evaluate(ctx context.Context, now time.Time) ([]Firing, []error) {
	e.mu.Lock()
	events := e.events
	e.events = nil
	e.mu.Unlock()

	var out []Firing
	var errs []error
	for _, r := range e.cfg.Rules {
		hits, err := e.hits(ctx, r, now, events)
		if err != nil {
			errs = append(errs, fmt.Errorf("rule %s: %w", r.Name, err))
			continue // keep existing streaks: a failed query is not evidence of health
		}
		e.mu.Lock()
		for node, v := range hits {
			k := r.Name + "/" + node
			s := e.streaks[k]
			if s == nil {
				s = &streak{since: now}
				e.streaks[k] = s
			}
			s.count++
			s.value = v
			if s.count >= r.For {
				out = append(out, Firing{Rule: r, Node: node, Value: v, Since: s.since, At: now})
			}
		}
		for k := range e.streaks {
			if len(k) > len(r.Name) && k[:len(r.Name)+1] == r.Name+"/" {
				if _, still := hits[k[len(r.Name)+1:]]; !still {
					delete(e.streaks, k)
				}
			}
		}
		e.mu.Unlock()
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Rule.Severity != out[j].Rule.Severity {
			return out[i].Rule.Severity == Critical
		}
		return out[i].Node < out[j].Node
	})
	return out, errs
}

func (e *Engine) hits(ctx context.Context, r Rule, now time.Time, events []Event) (map[string]float64, error) {
	hits := map[string]float64{}
	switch r.Type {
	case "expr":
		samples, err := e.src.Query(ctx, r.Expr, now)
		if err != nil {
			return nil, err
		}
		for _, s := range samples {
			if s.Node != "" {
				hits[s.Node] = s.Value
			}
		}
	case "straggler":
		expr := fmt.Sprintf("avg by (node) (avg_over_time(%s[%s]))", r.Metric, promDuration(time.Duration(r.Window)))
		samples, err := e.src.Query(ctx, expr, now)
		if err != nil {
			return nil, err
		}
		for node, v := range Stragglers(samples, r.Margin, r.MinPeers) {
			hits[node] = v
		}
	case "event":
		want := map[int]bool{}
		for _, x := range r.XIDs {
			want[x] = true
		}
		for _, ev := range events {
			for _, x := range ev.XIDs {
				if want[x] {
					hits[ev.Node] = float64(x)
				}
			}
		}
	}
	return hits, nil
}

// Stragglers returns nodes whose value exceeds the median of all nodes by more
// than margin (0.15 = 15% slower). Returns the ratio to the median per node.
func Stragglers(samples []Sample, margin float64, minPeers int) map[string]float64 {
	if len(samples) < minPeers {
		return nil
	}
	vals := make([]float64, 0, len(samples))
	for _, s := range samples {
		vals = append(vals, s.Value)
	}
	sort.Float64s(vals)
	n := len(vals)
	med := vals[n/2]
	if n%2 == 0 {
		med = (vals[n/2-1] + vals[n/2]) / 2
	}
	out := map[string]float64{}
	if med <= 0 {
		return out
	}
	for _, s := range samples {
		if ratio := s.Value / med; ratio > 1+margin {
			out[s.Node] = ratio
		}
	}
	return out
}

func promDuration(d time.Duration) string {
	if d%time.Minute == 0 {
		return fmt.Sprintf("%dm", int(d/time.Minute))
	}
	return fmt.Sprintf("%ds", int(d/time.Second))
}
