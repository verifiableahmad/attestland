package adapters

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os/exec"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"attestland/healthd/internal/health"
)

// Slurm drains nodes through slurmrestd, falling back to scontrol when the
// REST call fails (spec 1.3). Tokens are HS256 JWTs signed with the cluster's
// jwt_hs256.key, as Slurm's auth/jwt expects.
type Slurm struct {
	URL        string // http://al-control:6820
	APIVersion string // v0.0.42 for Slurm 25.05
	User       string // token subject (sun)
	Key        []byte // jwt_hs256.key
	Client     *http.Client
	// Exec runs scontrol/squeue for the fallback path; nil disables the fallback.
	Exec func(ctx context.Context, name string, args ...string) ([]byte, error)
}

// ExecCommand is the real fallback runner.
func ExecCommand(ctx context.Context, name string, args ...string) ([]byte, error) {
	return exec.CommandContext(ctx, name, args...).CombinedOutput()
}

func (s *Slurm) Name() string { return "slurm" }

func (s *Slurm) token() (string, error) {
	now := time.Now()
	t := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{
		"iat": now.Unix(), "exp": now.Add(5 * time.Minute).Unix(), "sun": s.User,
	})
	return t.SignedString(s.Key)
}

func (s *Slurm) do(ctx context.Context, method, path string, body any) ([]byte, error) {
	var rdr io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		rdr = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method,
		strings.TrimRight(s.URL, "/")+"/slurm/"+s.APIVersion+path, rdr)
	if err != nil {
		return nil, err
	}
	tok, err := s.token()
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-SLURM-USER-NAME", s.User)
	req.Header.Set("X-SLURM-USER-TOKEN", tok)
	req.Header.Set("Content-Type", "application/json")
	c := s.Client
	if c == nil {
		c = &http.Client{Timeout: 10 * time.Second}
	}
	resp, err := c.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	out, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return out, fmt.Errorf("slurmrestd %s %s: %d %s", method, path, resp.StatusCode, strings.TrimSpace(string(out)))
	}
	var e struct {
		Errors []struct {
			Description string `json:"description"`
			Error       string `json:"error"`
		} `json:"errors"`
	}
	if json.Unmarshal(out, &e) == nil && len(e.Errors) > 0 {
		return out, fmt.Errorf("slurmrestd: %s %s", e.Errors[0].Error, e.Errors[0].Description)
	}
	return out, nil
}

// Drain sets DRAIN with reason healthd:<rule>. DrainNow also requeues jobs
// running on the node (they would die with the GPU anyway).
func (s *Slurm) Drain(ctx context.Context, node, reason string, mode health.DrainMode) error {
	_, err := s.do(ctx, http.MethodPost, "/node/"+node, map[string]any{
		"state": []string{"DRAIN"}, "reason": reason,
	})
	if err != nil {
		if s.Exec == nil {
			return err
		}
		if out, ferr := s.Exec(ctx, "scontrol", "update", "nodename="+node, "state=drain", "reason="+reason); ferr != nil {
			return fmt.Errorf("rest: %v; scontrol: %v %s", err, ferr, out)
		}
	}
	if mode == health.DrainNow {
		return s.requeueJobsOn(ctx, node)
	}
	return nil
}

func (s *Slurm) requeueJobsOn(ctx context.Context, node string) error {
	if s.Exec == nil {
		return nil
	}
	out, err := s.Exec(ctx, "squeue", "-h", "-w", node, "-t", "RUNNING", "-o", "%i")
	if err != nil {
		return fmt.Errorf("squeue: %v %s", err, out)
	}
	for _, id := range strings.Fields(string(out)) {
		if o, err := s.Exec(ctx, "scontrol", "requeue", id); err != nil {
			return fmt.Errorf("requeue %s: %v %s", id, err, o)
		}
	}
	return nil
}

// Undrain resumes the node (used by the burn-in hand-back, not by healthd itself).
func (s *Slurm) Undrain(ctx context.Context, node string) error {
	_, err := s.do(ctx, http.MethodPost, "/node/"+node, map[string]any{"state": []string{"RESUME"}})
	if err != nil && s.Exec != nil {
		if out, ferr := s.Exec(ctx, "scontrol", "update", "nodename="+node, "state=resume"); ferr != nil {
			return fmt.Errorf("rest: %v; scontrol: %v %s", err, ferr, out)
		}
		return nil
	}
	return err
}

// State reads node state and reason.
func (s *Slurm) State(ctx context.Context, node string) (health.NodeState, error) {
	out, err := s.do(ctx, http.MethodGet, "/node/"+node, nil)
	if err != nil {
		if s.Exec == nil {
			return health.NodeState{}, err
		}
		o, ferr := s.Exec(ctx, "scontrol", "show", "node", node, "-o")
		if ferr != nil {
			return health.NodeState{}, fmt.Errorf("rest: %v; scontrol: %v", err, ferr)
		}
		return parseScontrol(string(o)), nil
	}
	var r struct {
		Nodes []struct {
			Name   string   `json:"name"`
			State  []string `json:"state"`
			Reason string   `json:"reason"`
		} `json:"nodes"`
	}
	if err := json.Unmarshal(out, &r); err != nil {
		return health.NodeState{}, err
	}
	if len(r.Nodes) == 0 {
		return health.NodeState{Exists: false}, nil
	}
	n := r.Nodes[0]
	drained := false
	for _, st := range n.State {
		if st == "DRAIN" || st == "DOWN" {
			drained = true
		}
	}
	return health.NodeState{Exists: true, Drained: drained, Reason: n.Reason}, nil
}

func parseScontrol(line string) health.NodeState {
	st := health.NodeState{Exists: strings.Contains(line, "NodeName=")}
	for _, f := range strings.Fields(line) {
		switch {
		case strings.HasPrefix(f, "State="):
			v := f[len("State="):]
			st.Drained = strings.Contains(v, "DRAIN") || strings.HasPrefix(v, "DOWN")
		case strings.HasPrefix(f, "Reason="):
			st.Reason = strings.SplitN(f[len("Reason="):], "[", 2)[0]
		}
	}
	return st
}
