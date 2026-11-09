// Package evidence collects CPU and GPU attestation bound to a nonce.
//
// Every collector shells out to the vendor's own tool, so the receipt code
// carries no vendor SDK and tool changes stay behind this interface (risk
// table: "attestation tooling changes"). Tool and version are recorded in the
// receipt.
package evidence

import (
	"context"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// CPU is what a CPU TEE collector returns.
type CPU struct {
	Type          string // sev-snp | tdx
	Evidence      []byte // raw report / quote, when the platform exposes it
	VerifierToken string // attestation service token (JWT), when used
	Tool          string
}

// GPU is what a GPU collector returns.
type GPU struct {
	Evidence       []byte // raw attestation report + certificate chain
	VerifierResult string // NRAS EAT JSON, or the local verifier's JSON
	Verifier       string // nras | local
	DriverVersion  string
	VBIOSVersion   string
	Tool           string
}

type CPUAttester interface {
	AttestCPU(ctx context.Context, nonceHex string) (CPU, error)
}

type GPUAttester interface {
	AttestGPU(ctx context.Context, nonceHex string) (GPU, error)
}

// Runner executes a command (swappable in tests).
type Runner func(ctx context.Context, name string, args ...string) ([]byte, error)

func execRunner(ctx context.Context, name string, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		return out, fmt.Errorf("%s: %w: %s", name, err, strings.TrimSpace(stderr.String()))
	}
	return out, nil
}

// GoogleCA gets a Google Cloud Attestation token for the Intel TDX
// Confidential VM (GCP a3-highgpu-1g, the primary platform). go-tpm-tools'
// gotpm sends the vTPM quote, event log and a TDX quote to GCA, which returns a
// signed JWT carrying our nonce in eat_nonce. Flags are from go-tpm-tools
// cmd/token.go (pinned GO_TPM_TOOLS_VERSION):
//
//	gotpm token --tee-technology tdx --custom-nonce <nonce> --audience <aud> --output <file>
//
// ASSUMPTION (confirm at bring-up): GCA issues TDX tokens on a3-highgpu-1g;
// Google documents TDX attestation for C3 only. Quote, when set, also embeds a
// raw TDX quote over the nonce, kept as evidence for a later verifier.
type GoogleCA struct {
	Binary   string // path to gotpm
	Audience string // custom aud claim; must not be sts.googleapis.com
	Quote    *TDXConfigFS
	Run      Runner
}

func (g *GoogleCA) AttestCPU(ctx context.Context, nonce string) (CPU, error) {
	run := g.Run
	if run == nil {
		run = execRunner
	}
	bin := g.Binary
	if bin == "" {
		bin = "gotpm"
	}
	f, err := os.CreateTemp("", "gca-token-*")
	if err != nil {
		return CPU{}, err
	}
	f.Close()
	defer os.Remove(f.Name())
	args := []string{"token", "--tee-technology", "tdx", "--custom-nonce", nonce, "--output", f.Name()}
	if g.Audience != "" {
		args = append(args, "--audience", g.Audience)
	}
	if _, err := run(ctx, bin, args...); err != nil {
		return CPU{}, err
	}
	raw, err := os.ReadFile(f.Name())
	if err != nil {
		return CPU{}, err
	}
	tok := strings.TrimSpace(string(raw))
	if strings.Count(tok, ".") != 2 {
		return CPU{}, fmt.Errorf("gotpm did not return a JWT: %.80q", tok)
	}
	out := CPU{Type: "tdx", VerifierToken: tok, Tool: "go-tpm-tools gotpm token"}
	if g.Quote != nil {
		q, err := g.Quote.AttestCPU(ctx, nonce)
		if err != nil {
			return CPU{}, fmt.Errorf("raw TDX quote (disable with --tdx-quote=false): %w", err)
		}
		out.Evidence = q.Evidence
		out.Tool += " + configfs-tsm quote"
	}
	return out, nil
}

// TDXConfigFS reads a TDX quote through the kernel's configfs-tsm interface.
// The nonce goes into the 64-byte report data (32-byte nonce + 32 zero bytes).
// GoogleCA attaches it as raw evidence; alverify v1 checks the GCA token, not
// the quote.
type TDXConfigFS struct {
	Root string // default /sys/kernel/config/tsm/report
}

func (t *TDXConfigFS) AttestCPU(_ context.Context, nonce string) (CPU, error) {
	root := t.Root
	if root == "" {
		root = "/sys/kernel/config/tsm/report"
	}
	nb, err := hex.DecodeString(nonce)
	if err != nil || len(nb) != 32 {
		return CPU{}, errors.New("nonce must be 32 bytes hex")
	}
	dir := filepath.Join(root, fmt.Sprintf("al-%d", time.Now().UnixNano()))
	if err := os.Mkdir(dir, 0o700); err != nil {
		return CPU{}, fmt.Errorf("configfs-tsm: %w", err)
	}
	defer os.Remove(dir)
	reportData := append(nb, make([]byte, 32)...)
	if err := os.WriteFile(filepath.Join(dir, "inblob"), reportData, 0o600); err != nil {
		return CPU{}, err
	}
	quote, err := os.ReadFile(filepath.Join(dir, "outblob"))
	if err != nil {
		return CPU{}, err
	}
	return CPU{Type: "tdx", Evidence: quote, Tool: "configfs-tsm"}, nil
}

// NVAT runs NVIDIA's Attestation SDK CLI. ASSUMPTION (confirm against the
// pinned NVAT release, spec 7.3 / open questions): `nvattest attest --device
// gpu --verifier <remote|local> --nonce <hex> --format json` prints JSON with
// the raw evidence, the verifier's result (an NRAS EAT when remote) and the
// driver/VBIOS versions. The field names below are mapped in parseNVAT.
type NVAT struct {
	Binary   string
	Verifier string // remote (NRAS, NVIDIA-signed) | local
	Version  string
	Run      Runner
}

func (n *NVAT) AttestGPU(ctx context.Context, nonce string) (GPU, error) {
	run := n.Run
	if run == nil {
		run = execRunner
	}
	bin := n.Binary
	if bin == "" {
		bin = "nvattest"
	}
	v := n.Verifier
	if v == "" {
		v = "remote"
	}
	out, err := run(ctx, bin, "attest", "--device", "gpu", "--verifier", v, "--nonce", nonce, "--format", "json")
	if err != nil {
		return GPU{}, err
	}
	g, err := parseNVAT(out)
	if err != nil {
		return GPU{}, err
	}
	g.Verifier = map[string]string{"remote": "nras", "local": "local"}[v]
	g.Tool = "nvattest " + n.Version
	return g, nil
}

func parseNVAT(out []byte) (GPU, error) {
	var r struct {
		Evidence      []struct{ Evidence, Certificate string } `json:"evidences"`
		Result        json.RawMessage                          `json:"detached_eat"`
		Claims        json.RawMessage                          `json:"claims"`
		DriverVersion string                                   `json:"driver_version"`
		VBIOSVersion  string                                   `json:"vbios_version"`
	}
	if err := json.Unmarshal(out, &r); err != nil {
		return GPU{}, fmt.Errorf("nvattest output: %w", err)
	}
	ev, _ := json.Marshal(r.Evidence)
	res := r.Result
	if len(res) == 0 {
		res = r.Claims
	}
	if len(res) == 0 {
		return GPU{}, errors.New("nvattest output has no verifier result")
	}
	return GPU{Evidence: ev, VerifierResult: string(res), DriverVersion: r.DriverVersion, VBIOSVersion: r.VBIOSVersion}, nil
}

// B64 is a small helper for receipt fields.
func B64(b []byte) string { return base64.StdEncoding.EncodeToString(b) }
