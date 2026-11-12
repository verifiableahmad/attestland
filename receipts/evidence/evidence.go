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
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"strings"
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

// AzureMAA gets a Microsoft Azure Attestation token for the SEV-SNP
// confidential VM (Azure Standard_NCC40ads_H100_v5, ADR 010). On Azure the SNP
// report sits behind the paravisor and is exposed through the vTPM; the guest
// attestation client sends it to MAA with our nonce and gets back a signed JWT.
// ASSUMPTION (confirm at bring-up, spec 7.3): the client is Azure's
// cvm-guest-attestation "AttestationClient" sample, run as
//
//	AttestationClient -a <MAA URL> -n <nonce> -o token
//
// and MAA echoes the nonce in x-ms-runtime.client-payload.nonce.
type AzureMAA struct {
	Binary string // path to AttestationClient
	MAAURL string // e.g. https://sharedeus2.eus2.attest.azure.net
	Run    Runner
}

func (a *AzureMAA) AttestCPU(ctx context.Context, nonce string) (CPU, error) {
	run := a.Run
	if run == nil {
		run = execRunner
	}
	bin := a.Binary
	if bin == "" {
		bin = "AttestationClient"
	}
	if a.MAAURL == "" {
		return CPU{}, errors.New("MAA URL not set")
	}
	out, err := run(ctx, bin, "-a", a.MAAURL, "-n", nonce, "-o", "token")
	if err != nil {
		return CPU{}, err
	}
	tok := strings.TrimSpace(string(out))
	if strings.Count(tok, ".") != 2 {
		return CPU{}, fmt.Errorf("AttestationClient did not return a JWT: %.80q", tok)
	}
	return CPU{Type: "sev-snp", VerifierToken: tok, Tool: "azure-cvm-guest-attestation AttestationClient"}, nil
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
