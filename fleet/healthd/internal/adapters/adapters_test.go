package adapters

import (
	"context"
	"encoding/json"

	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/prometheus/client_golang/prometheus"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"

	"attestland/healthd/internal/health"
)

var jwtKey = []byte("0123456789abcdef0123456789abcdef")

// fakeProm serves /api/v1/query from a map expr -> result vector.
func fakeProm(t *testing.T, results map[string]string) *httptest.Server {
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/query" {
			http.NotFound(w, r)
			return
		}
		q := r.URL.Query().Get("query")
		res, ok := results[q]
		if !ok {
			res = "[]"
		}
		io.WriteString(w, `{"status":"success","data":{"resultType":"vector","result":`+res+`}}`)
	}))
}

// fakeSlurmrestd tracks node state and checks the JWT on every call.
type fakeSlurmrestd struct {
	mu    sync.Mutex
	nodes map[string]map[string]any
	calls []string
	down  bool
}

func (f *fakeSlurmrestd) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.down {
		http.Error(w, "unavailable", http.StatusServiceUnavailable)
		return
	}
	tok, err := jwt.Parse(r.Header.Get("X-SLURM-USER-TOKEN"), func(*jwt.Token) (any, error) { return jwtKey, nil },
		jwt.WithValidMethods([]string{"HS256"}))
	if err != nil || !tok.Valid || tok.Claims.(jwt.MapClaims)["sun"] != r.Header.Get("X-SLURM-USER-NAME") {
		http.Error(w, "bad token", http.StatusUnauthorized)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/slurm/v0.0.42/node/")
	f.calls = append(f.calls, r.Method+" "+name)
	switch r.Method {
	case http.MethodPost:
		var body map[string]any
		json.NewDecoder(r.Body).Decode(&body)
		n := f.nodes[name]
		if n == nil {
			http.Error(w, `{"errors":[{"error":"Invalid node name"}]}`, http.StatusBadRequest)
			return
		}
		st := body["state"].([]any)[0].(string)
		if st == "RESUME" {
			n["state"] = []string{"IDLE"}
			n["reason"] = ""
		} else {
			n["state"] = []string{"IDLE", st}
			n["reason"] = body["reason"]
		}
		io.WriteString(w, `{"errors":[]}`)
	case http.MethodGet:
		var nodes []any
		if n := f.nodes[name]; n != nil {
			nodes = append(nodes, n)
		}
		json.NewEncoder(w).Encode(map[string]any{"nodes": nodes})
	}
}

func TestPrometheusQueryMapsNodes(t *testing.T) {
	srv := fakeProm(t, map[string]string{
		"DCGM_FI_DEV_XID_ERRORS == 79": `[{"metric":{"node":"al-gpu-02","gpu":"0"},"value":[1,"79"]},
		                                   {"metric":{"Hostname":"al-gpu-03"},"value":[1,"79"]}]`,
	})
	defer srv.Close()
	p := &Prometheus{URL: srv.URL}
	got, err := p.Query(context.Background(), "DCGM_FI_DEV_XID_ERRORS == 79", time.Now())
	if err != nil || len(got) != 2 || got[0].Node != "al-gpu-02" || got[1].Node != "al-gpu-03" || got[0].Value != 79 {
		t.Fatalf("got %+v err %v", got, err)
	}
}

func TestPrometheusErrorSurfaces(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		io.WriteString(w, `{"status":"error","errorType":"bad_data","error":"parse error"}`)
	}))
	defer srv.Close()
	if _, err := (&Prometheus{URL: srv.URL}).Query(context.Background(), "(", time.Now()); err == nil {
		t.Fatal("want error")
	}
}

func TestSlurmDrainViaRestWithJWT(t *testing.T) {
	f := &fakeSlurmrestd{nodes: map[string]map[string]any{"al-gpu-01": {"name": "al-gpu-01", "state": []string{"IDLE"}}}}
	srv := httptest.NewServer(f)
	defer srv.Close()
	s := &Slurm{URL: srv.URL, APIVersion: "v0.0.42", User: "slurm", Key: jwtKey}
	ctx := context.Background()
	if err := s.Drain(ctx, "al-gpu-01", "healthd:xid79", health.DrainAfterJobs); err != nil {
		t.Fatal(err)
	}
	st, err := s.State(ctx, "al-gpu-01")
	if err != nil || !st.Drained || st.Reason != "healthd:xid79" {
		t.Fatalf("state %+v err %v", st, err)
	}
	if st, _ := s.State(ctx, "nope"); st.Exists {
		t.Fatal("unknown node reported as existing")
	}
}

func TestSlurmFallsBackToScontrol(t *testing.T) {
	f := &fakeSlurmrestd{down: true}
	srv := httptest.NewServer(f)
	defer srv.Close()
	var ran []string
	s := &Slurm{URL: srv.URL, APIVersion: "v0.0.42", User: "slurm", Key: jwtKey,
		Exec: func(_ context.Context, name string, args ...string) ([]byte, error) {
			ran = append(ran, name+" "+strings.Join(args, " "))
			if name == "squeue" {
				return []byte("101\n102\n"), nil
			}
			if name == "scontrol" && args[0] == "show" {
				return []byte("NodeName=al-gpu-01 State=IDLE+DRAIN Reason=healthd:xid79 [slurm@2026-11-07T12:00:00]"), nil
			}
			return nil, nil
		}}
	if err := s.Drain(context.Background(), "al-gpu-01", "healthd:xid79", health.DrainNow); err != nil {
		t.Fatal(err)
	}
	want := []string{"scontrol update nodename=al-gpu-01 state=drain reason=healthd:xid79",
		"squeue -h -w al-gpu-01 -t RUNNING -o %i", "scontrol requeue 101", "scontrol requeue 102"}
	if strings.Join(ran, "|") != strings.Join(want, "|") {
		t.Fatalf("ran %q\nwant %q", ran, want)
	}
	st, _ := s.State(context.Background(), "al-gpu-01")
	if !st.Drained || st.Reason != "healthd:xid79" {
		t.Fatalf("scontrol state parse: %+v", st)
	}
}

func TestKubernetesTaintsAndCordons(t *testing.T) {
	cs := fake.NewSimpleClientset(&corev1.Node{
		ObjectMeta: metav1.ObjectMeta{Name: "al-gpu-04"},
		Spec:       corev1.NodeSpec{Taints: []corev1.Taint{{Key: "other", Effect: corev1.TaintEffectNoSchedule}}},
	})
	k := &Kubernetes{Client: cs}
	ctx := context.Background()
	if err := k.Drain(ctx, "al-gpu-04", "healthd:sbe-rate", health.DrainAfterJobs); err != nil {
		t.Fatal(err)
	}
	if err := k.Drain(ctx, "al-gpu-04", "healthd:xid79", health.DrainNow); err != nil {
		t.Fatal(err)
	}
	n, _ := cs.CoreV1().Nodes().Get(ctx, "al-gpu-04", metav1.GetOptions{})
	var ours []corev1.Taint
	for _, tt := range n.Spec.Taints {
		if tt.Key == TaintKey {
			ours = append(ours, tt)
		}
	}
	if !n.Spec.Unschedulable || len(ours) != 1 || ours[0].Effect != corev1.TaintEffectNoExecute || ours[0].Value != "xid79" {
		t.Fatalf("unschedulable=%v taints=%+v", n.Spec.Unschedulable, n.Spec.Taints)
	}
	if n.Labels[StateLabel] != "Draining" || len(n.Spec.Taints) != 2 {
		t.Fatalf("labels=%v taints=%v", n.Labels, n.Spec.Taints)
	}
	st, _ := k.State(ctx, "al-gpu-04")
	if !st.Drained || st.Reason != "healthd:xid79" {
		t.Fatalf("state %+v", st)
	}
	if err := k.Undrain(ctx, "al-gpu-04"); err != nil {
		t.Fatal(err)
	}
	if st, _ := k.State(ctx, "al-gpu-04"); st.Drained {
		t.Fatal("still drained after undrain")
	}
}

func TestAlertmanagerIncident(t *testing.T) {
	var got []map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v2/alerts" {
			http.NotFound(w, r)
			return
		}
		json.NewDecoder(r.Body).Decode(&got)
	}))
	defer srv.Close()
	a := &Alertmanager{URL: srv.URL}
	_, err := a.Open(context.Background(), health.IncidentReport{Node: "n1", Rule: "xid79", Severity: health.Critical,
		Action: "drained", Summary: "s", Evidence: map[string]string{"dmesg_excerpt": "NVRM: Xid 79"}, Time: time.Now()})
	if err != nil || len(got) != 1 {
		t.Fatalf("err %v got %v", err, got)
	}
	ann := got[0]["annotations"].(map[string]any)
	if ann["dmesg_excerpt"] != "NVRM: Xid 79" || got[0]["labels"].(map[string]any)["node"] != "n1" {
		t.Fatalf("alert %+v", got[0])
	}
}

// Integration: fake Prometheus replays an XID 79 on one node; the real
// controller drains it in a fake slurmrestd and a fake Kubernetes API, and
// posts an incident with evidence to a fake Alertmanager.
func TestIntegrationCriticalFaultEndToEnd(t *testing.T) {
	prom := fakeProm(t, map[string]string{
		"DCGM_FI_DEV_XID_ERRORS == 79":             `[{"metric":{"node":"al-gpu-02"},"value":[1,"79"]}]`,
		`DCGM_FI_DEV_XID_ERRORS{node="al-gpu-02"}`: `[{"metric":{"node":"al-gpu-02","gpu":"0"},"value":[1,"79"]}]`,
	})
	defer prom.Close()
	sr := &fakeSlurmrestd{nodes: map[string]map[string]any{
		"al-gpu-01": {"name": "al-gpu-01", "state": []string{"IDLE"}},
		"al-gpu-02": {"name": "al-gpu-02", "state": []string{"ALLOCATED"}}}}
	slurmSrv := httptest.NewServer(sr)
	defer slurmSrv.Close()
	var alerts []map[string]any
	var amMu sync.Mutex
	am := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		amMu.Lock()
		defer amMu.Unlock()
		var a []map[string]any
		json.NewDecoder(r.Body).Decode(&a)
		alerts = append(alerts, a...)
	}))
	defer am.Close()
	cs := fake.NewSimpleClientset(&corev1.Node{ObjectMeta: metav1.ObjectMeta{Name: "al-gpu-01"}},
		&corev1.Node{ObjectMeta: metav1.ObjectMeta{Name: "al-gpu-02"}})

	rules, err := health.LoadRules("../../rules.yaml")
	if err != nil {
		t.Fatal(err)
	}
	p := &Prometheus{URL: prom.URL}
	opt := health.DefaultOptions()
	opt.FleetSize = func(context.Context) int { return 4 }
	c := &health.Controller{
		Engine: health.NewEngine(rules, p),
		Schedulers: []health.Scheduler{
			&Slurm{URL: slurmSrv.URL, APIVersion: "v0.0.42", User: "slurm", Key: jwtKey},
			&Kubernetes{Client: cs},
		},
		Incident: &Alertmanager{URL: am.URL},
		Evidence: &PromLokiEvidence{Prom: p},
		Opts:     opt, Metrics: health.NewMetrics(prometheus.NewRegistry()),
		Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	ds := c.Tick(context.Background())
	if len(ds) != 1 || ds[0].Outcome != "drained" || ds[0].Err != nil {
		t.Fatalf("decisions %+v", ds)
	}
	if st := sr.nodes["al-gpu-02"]; st["reason"] != "healthd:xid79" {
		t.Fatalf("slurm node %+v", st)
	}
	n, _ := cs.CoreV1().Nodes().Get(context.Background(), "al-gpu-02", metav1.GetOptions{})
	if !n.Spec.Unschedulable || n.Spec.Taints[0].Effect != corev1.TaintEffectNoExecute {
		t.Fatalf("k8s node %+v", n.Spec)
	}
	if n1, _ := cs.CoreV1().Nodes().Get(context.Background(), "al-gpu-01", metav1.GetOptions{}); n1.Spec.Unschedulable {
		t.Fatal("healthy node was touched")
	}
	amMu.Lock()
	defer amMu.Unlock()
	if len(alerts) != 1 || !strings.Contains(alerts[0]["annotations"].(map[string]any)["metric_snapshot"].(string), "DCGM_FI_DEV_XID_ERRORS") {
		t.Fatalf("alerts %+v", alerts)
	}
	// Second tick: idempotent.
	c.Tick(context.Background())
	if len(alerts) != 1 {
		t.Fatalf("duplicate incident: %d", len(alerts))
	}
}
