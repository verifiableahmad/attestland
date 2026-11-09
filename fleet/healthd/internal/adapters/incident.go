package adapters

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"sort"
	"strings"
	"time"

	"attestland/healthd/internal/health"
)

// Alertmanager posts an alert to /api/v2/alerts; its routing decides who hears.
type Alertmanager struct {
	URL    string
	Client *http.Client
}

func (a *Alertmanager) Open(ctx context.Context, r health.IncidentReport) (string, error) {
	ann := map[string]string{"summary": r.Summary, "action": r.Action}
	for k, v := range r.Evidence {
		ann[k] = truncate(v, 8000)
	}
	alert := []map[string]any{{
		"labels": map[string]string{
			"alertname": "HealthdIncident", "rule": r.Rule, "node": r.Node,
			"severity": string(r.Severity), "source": "healthd",
		},
		"annotations": ann,
		"startsAt":    r.Time.UTC().Format(time.RFC3339),
	}}
	id := fmt.Sprintf("am:%s/%s@%d", r.Rule, r.Node, r.Time.Unix())
	return id, postJSON(ctx, a.Client, strings.TrimRight(a.URL, "/")+"/api/v2/alerts", alert, nil)
}

// GitHubIssue opens an issue with the evidence in the body.
type GitHubIssue struct {
	Repo   string // owner/name
	Token  string
	API    string // default https://api.github.com
	Client *http.Client
}

func (g *GitHubIssue) Open(ctx context.Context, r health.IncidentReport) (string, error) {
	var b strings.Builder
	fmt.Fprintf(&b, "**%s** on `%s` (%s) - action: %s\n\n%s\n", r.Rule, r.Node, r.Severity, r.Action, r.Summary)
	keys := make([]string, 0, len(r.Evidence))
	for k := range r.Evidence {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Fprintf(&b, "\n### %s\n```\n%s\n```\n", k, truncate(r.Evidence[k], 20000))
	}
	api := g.API
	if api == "" {
		api = "https://api.github.com"
	}
	var resp struct {
		HTMLURL string `json:"html_url"`
	}
	err := postJSON(ctx, g.Client, fmt.Sprintf("%s/repos/%s/issues", api, g.Repo), map[string]any{
		"title":  fmt.Sprintf("[healthd] %s on %s", r.Rule, r.Node),
		"body":   b.String(),
		"labels": []string{"healthd", string(r.Severity)},
	}, map[string]string{"Authorization": "Bearer " + g.Token, "Accept": "application/vnd.github+json"}, &resp)
	return resp.HTMLURL, err
}

// LogIncident writes incidents to the log only (default, and dry-run).
type LogIncident struct{ Log *slog.Logger }

func (l *LogIncident) Open(_ context.Context, r health.IncidentReport) (string, error) {
	l.Log.Warn("incident", "rule", r.Rule, "node", r.Node, "severity", r.Severity,
		"action", r.Action, "summary", r.Summary, "evidence_keys", len(r.Evidence))
	return "log", nil
}

// Multi sends to every sink; the first non-empty id wins.
type Multi []health.Incident

func (m Multi) Open(ctx context.Context, r health.IncidentReport) (string, error) {
	var id string
	var errs []string
	for _, s := range m {
		got, err := s.Open(ctx, r)
		if err != nil {
			errs = append(errs, err.Error())
		}
		if id == "" || id == "log" {
			if got != "" {
				id = got
			}
		}
	}
	if len(errs) > 0 {
		return id, fmt.Errorf("incident: %s", strings.Join(errs, "; "))
	}
	return id, nil
}

func postJSON(ctx context.Context, c *http.Client, url string, body any, headers map[string]string, out ...any) error {
	raw, err := json.Marshal(body)
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	if c == nil {
		c = &http.Client{Timeout: 10 * time.Second}
	}
	resp, err := c.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return fmt.Errorf("POST %s: %d %s", url, resp.StatusCode, truncate(string(data), 300))
	}
	if len(out) > 0 && out[0] != nil {
		return json.Unmarshal(data, out[0])
	}
	return nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "\n…(truncated)"
}
