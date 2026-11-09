// Package adapters implements healthd's MetricSource, Scheduler, Incident and
// Evidence interfaces against Prometheus, Loki, slurmrestd, Kubernetes,
// Alertmanager and GitHub.
package adapters

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"time"

	"attestland/healthd/internal/health"
)

// Prometheus is a MetricSource over the Prometheus HTTP API (instant queries).
// NodeLabel names the series label carrying the node name; file_sd targets
// add `node` to every series (observability/prometheus).
type Prometheus struct {
	URL       string
	NodeLabel string
	Client    *http.Client
}

type promResp struct {
	Status    string `json:"status"`
	ErrorType string `json:"errorType"`
	Error     string `json:"error"`
	Data      struct {
		ResultType string `json:"resultType"`
		Result     []struct {
			Metric map[string]string `json:"metric"`
			Value  [2]any            `json:"value"`
		} `json:"result"`
	} `json:"data"`
}

func (p *Prometheus) client() *http.Client {
	if p.Client != nil {
		return p.Client
	}
	return &http.Client{Timeout: 10 * time.Second}
}

// Query runs an instant query and maps each series to a node.
func (p *Prometheus) Query(ctx context.Context, expr string, at time.Time) ([]health.Sample, error) {
	q := url.Values{"query": {expr}, "time": {strconv.FormatFloat(float64(at.UnixNano())/1e9, 'f', 3, 64)}}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(p.URL, "/")+"/api/v1/query?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	resp, err := p.client().Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	var r promResp
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return nil, fmt.Errorf("prometheus: decode: %w", err)
	}
	if r.Status != "success" {
		return nil, fmt.Errorf("prometheus: %s: %s", r.ErrorType, r.Error)
	}
	if r.Data.ResultType != "vector" {
		return nil, fmt.Errorf("prometheus: want vector, got %s", r.Data.ResultType)
	}
	label := p.NodeLabel
	if label == "" {
		label = "node"
	}
	out := make([]health.Sample, 0, len(r.Data.Result))
	for _, s := range r.Data.Result {
		str, _ := s.Value[1].(string)
		v, err := strconv.ParseFloat(str, 64)
		if err != nil {
			continue
		}
		node := s.Metric[label]
		if node == "" {
			node = s.Metric["Hostname"] // DCGM exporter's own label
		}
		out = append(out, health.Sample{Node: node, Value: v, Labels: s.Metric})
	}
	return out, nil
}

// PromLokiEvidence attaches a metric snapshot, a kernel log (dmesg) excerpt
// and the last periodic diag result to incidents.
type PromLokiEvidence struct {
	Prom    *Prometheus
	LokiURL string
	Client  *http.Client
}

var snapshotMetrics = []string{
	"DCGM_FI_DEV_XID_ERRORS", "DCGM_FI_DEV_ECC_DBE_VOL_TOTAL", "DCGM_FI_DEV_ECC_SBE_VOL_TOTAL",
	"DCGM_FI_DEV_ROW_REMAP_FAILURE", "DCGM_FI_DEV_PCIE_REPLAY_COUNTER", "DCGM_FI_DEV_GPU_TEMP",
	"DCGM_FI_DEV_CLOCKS_EVENT_REASONS", "al_dcgm_diag_pass", "al_train_step_time_seconds",
}

// Collect never fails: missing evidence is reported inside the map.
func (e *PromLokiEvidence) Collect(ctx context.Context, node string, at time.Time) map[string]string {
	out := map[string]string{}
	if e.Prom != nil {
		var lines []string
		for _, m := range snapshotMetrics {
			ss, err := e.Prom.Query(ctx, fmt.Sprintf(`%s{node=%q}`, m, node), at)
			if err != nil {
				lines = append(lines, fmt.Sprintf("%s: error %v", m, err))
				continue
			}
			for _, s := range ss {
				lines = append(lines, fmt.Sprintf("%s%s %g", m, fmtLabels(s.Labels), s.Value))
			}
		}
		out["metric_snapshot"] = strings.Join(lines, "\n")
	}
	if e.LokiURL != "" {
		out["dmesg_excerpt"] = e.loki(ctx, fmt.Sprintf(`{host=%q, transport="kernel"}`, node), at, 50)
		out["diag_output"] = e.loki(ctx, fmt.Sprintf(`{host=%q, unit="al-periodic-diag.service"}`, node), at, 30)
	}
	return out
}

func (e *PromLokiEvidence) loki(ctx context.Context, query string, at time.Time, limit int) string {
	q := url.Values{
		"query": {query}, "limit": {strconv.Itoa(limit)}, "direction": {"backward"},
		"start": {strconv.FormatInt(at.Add(-30*time.Minute).UnixNano(), 10)},
		"end":   {strconv.FormatInt(at.UnixNano(), 10)},
	}
	c := e.Client
	if c == nil {
		c = &http.Client{Timeout: 10 * time.Second}
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, strings.TrimRight(e.LokiURL, "/")+"/loki/api/v1/query_range?"+q.Encode(), nil)
	resp, err := c.Do(req)
	if err != nil {
		return "loki error: " + err.Error()
	}
	defer resp.Body.Close()
	var r struct {
		Data struct {
			Result []struct {
				Values [][2]string `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&r); err != nil {
		return "loki decode error: " + err.Error()
	}
	var lines []string
	for _, s := range r.Data.Result {
		for _, v := range s.Values {
			lines = append(lines, v[1])
		}
	}
	return strings.Join(lines, "\n")
}

func fmtLabels(l map[string]string) string {
	keys := make([]string, 0, len(l))
	for k := range l {
		if k != "__name__" {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s=%q", k, l[k]))
	}
	return "{" + strings.Join(parts, ",") + "}"
}
