// healthd watches GPU health signals, applies rules, drains bad nodes from
// Slurm and Kubernetes, and opens an incident with evidence (spec 1.3).
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	"k8s.io/client-go/kubernetes"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/manager"
	ctrlmetrics "sigs.k8s.io/controller-runtime/pkg/metrics"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	"attestland/healthd/internal/adapters"
	"attestland/healthd/internal/health"
)

func main() {
	var (
		rulesPath    = flag.String("rules", "/etc/healthd/rules.yaml", "rules file")
		promURL      = flag.String("prometheus-url", "http://prometheus:9090", "Prometheus base URL")
		lokiURL      = flag.String("loki-url", "", "Loki base URL for dmesg/diag evidence (optional)")
		slurmURL     = flag.String("slurm-url", "", "slurmrestd base URL; empty disables the Slurm adapter")
		slurmAPI     = flag.String("slurm-api", "v0.0.42", "slurmrestd API version")
		slurmUser    = flag.String("slurm-user", "slurm", "user name in Slurm JWTs")
		slurmKey     = flag.String("slurm-jwt-key", "/etc/healthd/jwt_hs256.key", "Slurm jwt_hs256.key")
		scontrolFB   = flag.Bool("scontrol-fallback", false, "fall back to scontrol/squeue (needs Slurm client binaries)")
		noK8s        = flag.Bool("no-kubernetes", false, "disable the Kubernetes adapter")
		alertmanager = flag.String("alertmanager-url", "", "Alertmanager base URL for incidents")
		ghRepo       = flag.String("github-repo", "", "owner/name for incident issues (token in GITHUB_TOKEN)")
		dryRun       = flag.Bool("dry-run", false, "decide and log, never drain")
		interval     = flag.Duration("interval", 10*time.Second, "evaluation interval")
		budgetMin    = flag.Int("budget-min", 1, "drain budget: minimum nodes per window")
		budgetFrac   = flag.Float64("budget-fraction", 0.25, "drain budget: fraction of fleet per window")
		budgetWindow = flag.Duration("budget-window", time.Hour, "drain budget window")
		fleetSize    = flag.Int("fleet-size", 0, "fixed fleet size for the budget (0 = count GPU nodes in k8s)")
		metricsAddr  = flag.String("metrics-addr", ":8081", "metrics listen address")
		eventsAddr   = flag.String("events-addr", ":8082", "listen address for /v1/events (Slurm epilog)")
		leaderElect  = flag.Bool("leader-elect", true, "use leader election (run as a Deployment)")
		namespace    = flag.String("leader-namespace", "fleet-system", "namespace for the leader lease")
	)
	flag.Parse()
	// HEALTHD_DRY_RUN lets the goodput experiment flip modes with `kubectl set env`.
	if v := os.Getenv("HEALTHD_DRY_RUN"); v != "" {
		*dryRun = v == "true" || v == "1"
	}
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(log, opts{
		*rulesPath, *promURL, *lokiURL, *slurmURL, *slurmAPI, *slurmUser, *slurmKey, *scontrolFB, *noK8s,
		*alertmanager, *ghRepo, *dryRun, *interval, *budgetMin, *budgetFrac, *budgetWindow, *fleetSize,
		*metricsAddr, *eventsAddr, *leaderElect, *namespace,
	}); err != nil {
		log.Error("healthd exited", "err", err)
		os.Exit(1)
	}
}

type opts struct {
	rules, prom, loki, slurmURL, slurmAPI, slurmUser, slurmKey string
	scontrolFallback, noK8s                                    bool
	alertmanager, ghRepo                                       string
	dryRun                                                     bool
	interval                                                   time.Duration
	budgetMin                                                  int
	budgetFrac                                                 float64
	budgetWindow                                               time.Duration
	fleetSize                                                  int
	metricsAddr, eventsAddr                                    string
	leaderElect                                                bool
	namespace                                                  string
}

func run(log *slog.Logger, o opts) error {
	cfg, err := health.LoadRules(o.rules)
	if err != nil {
		return fmt.Errorf("rules: %w", err)
	}
	prom := &adapters.Prometheus{URL: o.prom, NodeLabel: "node"}
	engine := health.NewEngine(cfg, prom)

	var schedulers []health.Scheduler
	if o.slurmURL != "" {
		key, err := os.ReadFile(o.slurmKey)
		if err != nil {
			return fmt.Errorf("slurm jwt key: %w", err)
		}
		s := &adapters.Slurm{URL: o.slurmURL, APIVersion: o.slurmAPI, User: o.slurmUser, Key: key}
		if o.scontrolFallback {
			s.Exec = adapters.ExecCommand
		}
		schedulers = append(schedulers, s)
	}

	restCfg, err := ctrl.GetConfig()
	if err != nil && !o.noK8s {
		return fmt.Errorf("kubeconfig: %w", err)
	}
	var kc kubernetes.Interface
	if !o.noK8s {
		kc, err = kubernetes.NewForConfig(restCfg)
		if err != nil {
			return err
		}
		schedulers = append(schedulers, &adapters.Kubernetes{Client: kc})
	}

	incidents := adapters.Multi{&adapters.LogIncident{Log: log}}
	if o.alertmanager != "" {
		incidents = append(incidents, &adapters.Alertmanager{URL: o.alertmanager})
	}
	if o.ghRepo != "" {
		incidents = append(incidents, &adapters.GitHubIssue{Repo: o.ghRepo, Token: os.Getenv("GITHUB_TOKEN")})
	}

	opt := health.DefaultOptions()
	opt.Interval, opt.DryRun = o.interval, o.dryRun
	opt.BudgetMin, opt.BudgetFraction, opt.BudgetWindow = o.budgetMin, o.budgetFrac, o.budgetWindow
	switch {
	case o.fleetSize > 0:
		n := o.fleetSize
		opt.FleetSize = func(context.Context) int { return n }
	case kc != nil:
		opt.FleetSize = adapters.GPUFleetSize(kc)
	}

	c := &health.Controller{
		Engine: engine, Schedulers: schedulers, Incident: incidents,
		Evidence: &adapters.PromLokiEvidence{Prom: prom, LokiURL: o.loki},
		Opts: opt, Metrics: health.NewMetrics(ctrlmetrics.Registry), Log: log,
	}

	go serveEvents(log, o.eventsAddr, engine)

	if o.noK8s {
		// Standalone mode (no cluster): plain loop, metrics on their own server.
		log.Info("healthd starting without Kubernetes", "dry_run", o.dryRun, "schedulers", len(schedulers))
		go func() {
			mux := http.NewServeMux()
			mux.Handle("/metrics", metricsHandler())
			_ = http.ListenAndServe(o.metricsAddr, mux)
		}()
		return c.Start(ctrl.SetupSignalHandler())
	}

	mgr, err := ctrl.NewManager(restCfg, manager.Options{
		Metrics:                 metricsserver.Options{BindAddress: o.metricsAddr},
		LeaderElection:          o.leaderElect,
		LeaderElectionID:        "healthd.fleet.attestland",
		LeaderElectionNamespace: o.namespace,
		HealthProbeBindAddress:  ":8083",
	})
	if err != nil {
		return err
	}
	if err := mgr.Add(c); err != nil {
		return err
	}
	_ = mgr.AddHealthzCheck("ping", func(*http.Request) error { return nil })
	log.Info("healthd starting", "dry_run", o.dryRun, "schedulers", len(schedulers), "rules", len(cfg.Rules))
	return mgr.Start(ctrl.SetupSignalHandler())
}

// serveEvents accepts XID reports from the Slurm epilog.
func serveEvents(log *slog.Logger, addr string, engine *health.Engine) {
	mux := http.NewServeMux()
	mux.HandleFunc("/v1/events", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "POST only", http.StatusMethodNotAllowed)
			return
		}
		var ev health.Event
		if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<16)).Decode(&ev); err != nil || strings.TrimSpace(ev.Node) == "" {
			http.Error(w, "bad event", http.StatusBadRequest)
			return
		}
		ev.At = time.Now()
		engine.PushEvent(ev)
		log.Info("event", "node", ev.Node, "source", ev.Source, "job", ev.Job, "xids", ev.XIDs)
		w.WriteHeader(http.StatusAccepted)
	})
	if err := http.ListenAndServe(addr, mux); err != nil {
		log.Error("events server", "err", err)
	}
}
