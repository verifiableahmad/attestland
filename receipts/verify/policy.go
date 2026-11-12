// Package verify implements alverify's checks (spec 7.6).
package verify

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"

	"gopkg.in/yaml.v3"
)

// Policy holds what a verifier accepts: attestation service keys, allowed
// measurements and versions, and the expected image signer.
type Policy struct {
	CPU   CPUPolicy   `yaml:"cpu"`
	GPU   GPUPolicy   `yaml:"gpu"`
	Image ImagePolicy `yaml:"image"`
	dir   string
}

type CPUPolicy struct {
	Type string `yaml:"type"` // tdx | sev-snp | test; must equal the receipt's cpu_tee.type
	// Verifier is the attestation service whose token the receipt carries:
	// maa (Microsoft Azure Attestation, the default and only one).
	Verifier string `yaml:"verifier"`
	// Token issuer and its signing keys (JWKS file, pinned here: <MAA URL>/certs).
	Issuer   string `yaml:"issuer"`
	JWKSFile string `yaml:"jwks_file"`
	// Allowed SEV-SNP launch measurements (hex) of the qualified CVM image.
	// Empty list = any.
	AllowedLaunchMeasurements []string `yaml:"allowed_launch_measurements"`
	RequireCompliance         string   `yaml:"require_compliance"` // e.g. azure-compliant-cvm
	AllowDebug                bool     `yaml:"allow_debug"`
	// Require maps extra claim paths to exact values, on top of the verifier's
	// built-in checks (e.g. x-ms-azurevm-vmid: <VM id>).
	Require map[string]string `yaml:"require"`
	// Claim paths (dot separated). Defaults depend on Verifier.
	Claims CPUClaims `yaml:"claims"`
	// checks are the built-in claim -> value checks for Verifier (set by defaults).
	checks map[string]string
}

type CPUClaims struct {
	Nonce             string `yaml:"nonce"`
	LaunchMeasurement string `yaml:"launch_measurement"`
}

type GPUPolicy struct {
	// Verifier "nras" requires an NVIDIA-signed result; "local" accepts an
	// unsigned local-verifier result only if AllowUnsignedLocal is set.
	Verifier           string    `yaml:"verifier"`
	AllowUnsignedLocal bool      `yaml:"allow_unsigned_local"`
	Issuer             string    `yaml:"issuer"`
	JWKSFile           string    `yaml:"jwks_file"`
	AllowedDrivers     []string  `yaml:"allowed_driver_versions"`
	AllowedVBIOS       []string  `yaml:"allowed_vbios_versions"`
	Claims             GPUClaims `yaml:"claims"`
}

type GPUClaims struct {
	OverallResult string `yaml:"overall_result"`
	Nonce         string `yaml:"nonce"`
	Driver        string `yaml:"driver"`
	VBIOS         string `yaml:"vbios"`
	Measurement   string `yaml:"measurement"` // expected value "success"
	Debug         string `yaml:"debug"`       // expected value "disabled"
	// CCMode, when set, must equal CCModeValue. Empty: CC mode is implied by a
	// successful CC attestation (the report is only produced in CC mode).
	CCMode      string `yaml:"cc_mode"`
	CCModeValue string `yaml:"cc_mode_value"`
}

type ImagePolicy struct {
	TrustedRoot           string   `yaml:"trusted_root"` // Sigstore trusted_root.json
	CertificateIdentity   string   `yaml:"certificate_identity"`
	CertificateOIDCIssuer string   `yaml:"certificate_oidc_issuer"`
	AllowedRepositories   []string `yaml:"allowed_repositories"`
}

// LoadPolicy reads a policy file; relative paths inside it resolve from its directory.
func LoadPolicy(path string) (*Policy, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	p := &Policy{dir: filepath.Dir(path)}
	if err := yaml.Unmarshal(raw, p); err != nil {
		return nil, err
	}
	p.defaults()
	if v := p.CPU.Verifier; v != "maa" {
		return nil, fmt.Errorf("cpu.verifier %q: want maa", v)
	}
	return p, nil
}

func (p *Policy) defaults() {
	def := func(s *string, v string) {
		if *s == "" {
			*s = v
		}
	}
	c := &p.CPU
	def(&c.Verifier, "maa")
	c.checks = map[string]string{}
	switch c.Verifier {
	case "maa":
		// Microsoft Azure Attestation claims for an SEV-SNP confidential VM.
		def(&c.Claims.Nonce, "x-ms-runtime.client-payload.nonce")
		def(&c.Claims.LaunchMeasurement, "x-ms-isolation-tee.x-ms-sevsnpvm-launchmeasurement")
		c.checks["x-ms-isolation-tee.x-ms-attestation-type"] = "sevsnpvm"
		if c.RequireCompliance != "" {
			c.checks["x-ms-isolation-tee.x-ms-compliance-status"] = c.RequireCompliance
		}
		if !c.AllowDebug {
			c.checks["x-ms-isolation-tee.x-ms-sevsnpvm-is-debuggable"] = "false"
		}
	}
	for k, v := range c.Require {
		c.checks[k] = v
	}
	g := &p.GPU.Claims
	def(&g.OverallResult, "x-nvidia-overall-att-result")
	def(&g.Nonce, "eat_nonce")
	def(&g.Driver, "x-nvidia-gpu-driver-version")
	def(&g.VBIOS, "x-nvidia-gpu-vbios-version")
	def(&g.Measurement, "measres")
	def(&g.Debug, "dbgstat")
	def(&p.GPU.Verifier, "nras")
}

func (p *Policy) path(f string) string {
	if f == "" || filepath.IsAbs(f) {
		return f
	}
	return filepath.Join(p.dir, f)
}

// JWKS is a JSON Web Key Set file.
type JWKS struct {
	Keys []json.RawMessage `json:"keys"`
}

func loadJWKS(path string) (*JWKS, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("jwks: %w", err)
	}
	var j JWKS
	if err := json.Unmarshal(raw, &j); err != nil {
		return nil, fmt.Errorf("jwks: %w", err)
	}
	return &j, nil
}
