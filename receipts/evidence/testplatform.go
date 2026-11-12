package evidence

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/rsa"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// TestPlatform stands in for the CPU attestation service (Microsoft Azure
// Attestation) and NVIDIA NRAS on a machine with
// no TEE: it signs tokens with the same claim shapes, using its own keys. A
// test-platform receipt passes only a policy that pins these keys
// (receipts/policy/test), never a production policy.
type TestPlatform struct {
	cpuKey *rsa.PrivateKey
	gpuKey *ecdsa.PrivateKey
	// CPUVerifier picks the CPU token shape: maa (default, only).
	CPUVerifier string
	// Values the fake services attest to.
	LaunchMeasurement string
	Driver, VBIOS     string
	Debuggable        bool
	Now               func() time.Time
}

const (
	TestCPUIssuer  = "https://test-cpu.attestland.local"
	TestNRASIssuer = "https://test-nras.attestland.local"
)

// NewTestPlatform creates fresh keys, or loads them from dir if present.
func NewTestPlatform(dir string) (*TestPlatform, error) {
	t := &TestPlatform{LaunchMeasurement: "a1b2c3" + strings.Repeat("0", 90), Driver: "580.196.07",
		VBIOS: "96.00.9F.00.04", Now: time.Now}
	if dir != "" {
		if err := t.load(dir); err == nil {
			return t, nil
		}
	}
	var err error
	if t.cpuKey, err = rsa.GenerateKey(rand.Reader, 2048); err != nil {
		return nil, err
	}
	if t.gpuKey, err = ecdsa.GenerateKey(elliptic.P384(), rand.Reader); err != nil {
		return nil, err
	}
	if dir != "" {
		return t, t.save(dir)
	}
	return t, nil
}

func (t *TestPlatform) AttestCPU(_ context.Context, nonce string) (CPU, error) {
	now := t.Now()
	claims := jwt.MapClaims{"iss": TestCPUIssuer, "iat": now.Unix(), "exp": now.Add(time.Hour).Unix()}
	switch t.CPUVerifier {
	case "", "maa":
		claims["x-ms-isolation-tee"] = map[string]any{
			"x-ms-attestation-type":           "sevsnpvm",
			"x-ms-compliance-status":          "azure-compliant-cvm",
			"x-ms-sevsnpvm-launchmeasurement": t.LaunchMeasurement,
			"x-ms-sevsnpvm-is-debuggable":     t.Debuggable,
		}
		claims["x-ms-runtime"] = map[string]any{"client-payload": map[string]any{"nonce": nonce}}
	default:
		return CPU{}, fmt.Errorf("test platform: unknown CPU verifier %q", t.CPUVerifier)
	}
	tok := jwt.NewWithClaims(jwt.SigningMethodRS256, claims)
	tok.Header["kid"] = "test-cpu"
	s, err := tok.SignedString(t.cpuKey)
	return CPU{Type: "sev-snp", VerifierToken: s, Tool: "test-platform"}, err
}

func (t *TestPlatform) AttestGPU(_ context.Context, nonce string) (GPU, error) {
	now := t.Now()
	sign := func(c jwt.MapClaims) (string, error) {
		c["iss"], c["iat"] = TestNRASIssuer, now.Unix()
		tk := jwt.NewWithClaims(jwt.SigningMethodES384, c)
		tk.Header["kid"] = "test-nras"
		return tk.SignedString(t.gpuKey)
	}
	gpu0, err := sign(jwt.MapClaims{"eat_nonce": nonce, "measres": "success", "dbgstat": "disabled",
		"x-nvidia-gpu-driver-version": t.Driver, "x-nvidia-gpu-vbios-version": t.VBIOS,
		"hwmodel": "GH100 A01 GSP BROM"})
	if err != nil {
		return GPU{}, err
	}
	overall, err := sign(jwt.MapClaims{"eat_nonce": nonce, "x-nvidia-overall-att-result": true,
		"submods": map[string]any{"GPU-0": []string{"DIGEST", "SHA256", "test"}}})
	if err != nil {
		return GPU{}, err
	}
	eat, _ := json.Marshal([]any{[]string{"JWT", overall}, map[string]string{"GPU-0": gpu0}})
	return GPU{Evidence: []byte("test-evidence"), VerifierResult: string(eat), Verifier: "nras",
		DriverVersion: t.Driver, VBIOSVersion: t.VBIOS, Tool: "test-platform"}, nil
}

// WriteJWKS writes cpu-jwks.json and nras-jwks.json for the test policies.
func (t *TestPlatform) WriteJWKS(dir string) error {
	b64u := func(b []byte) string { return base64.RawURLEncoding.EncodeToString(b) }
	cpu := map[string]any{"keys": []any{map[string]any{"kty": "RSA", "kid": "test-cpu",
		"n": b64u(t.cpuKey.N.Bytes()), "e": b64u(big.NewInt(int64(t.cpuKey.E)).Bytes())}}}
	pad := func(b []byte) []byte { return append(make([]byte, 48-len(b)), b...) }
	nras := map[string]any{"keys": []any{map[string]any{"kty": "EC", "kid": "test-nras", "crv": "P-384",
		"x": b64u(pad(t.gpuKey.X.Bytes())), "y": b64u(pad(t.gpuKey.Y.Bytes()))}}}
	for name, v := range map[string]any{"cpu-jwks.json": cpu, "nras-jwks.json": nras} {
		raw, _ := json.MarshalIndent(v, "", "  ")
		if err := os.WriteFile(filepath.Join(dir, name), raw, 0o644); err != nil {
			return err
		}
	}
	return nil
}

type savedKeys struct {
	CPU []byte `json:"cpu"`
	GPU []byte `json:"gpu"`
}

func (t *TestPlatform) save(dir string) error {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	gpu, err := marshalEC(t.gpuKey)
	if err != nil {
		return err
	}
	raw, _ := json.Marshal(savedKeys{CPU: marshalRSA(t.cpuKey), GPU: gpu})
	if err := os.WriteFile(filepath.Join(dir, "test-platform-keys.json"), raw, 0o600); err != nil {
		return err
	}
	return t.WriteJWKS(dir)
}

func (t *TestPlatform) load(dir string) error {
	raw, err := os.ReadFile(filepath.Join(dir, "test-platform-keys.json"))
	if err != nil {
		return err
	}
	var k savedKeys
	if err := json.Unmarshal(raw, &k); err != nil {
		return err
	}
	if t.cpuKey, err = parseRSA(k.CPU); err != nil {
		return err
	}
	t.gpuKey, err = parseEC(k.GPU)
	return err
}
