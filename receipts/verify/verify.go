package verify

import (
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"slices"
	"strings"

	"attestland/receipts/receipt"
	"attestland/receipts/schema"
)

// Check is one row of alverify's output.
type Check struct {
	Name     string `json:"check"`
	Pass     bool   `json:"pass"`
	Required bool   `json:"required"`
	Detail   string `json:"detail"`
}

// Report is the full result. OK only if every required check passes.
type Report struct {
	OK     bool    `json:"ok"`
	Checks []Check `json:"checks"`
}

// Inputs are the optional files supplied for the content check.
type Inputs struct {
	ModelDir    string
	InputsFile  string
	OutputsFile string
}

// ImageVerifier checks the cosign bundle for the image digest under the
// policy. The production one uses sigstore-go (image.go); tests inject theirs.
type ImageVerifier func(bundle []byte, digestHex string, p ImagePolicy) error

// Verifier runs every check.
type Verifier struct {
	Policy *Policy
	Image  ImageVerifier
}

// Verify checks raw receipt JSON.
func (v *Verifier) Verify(raw []byte, in Inputs) Report {
	var rep Report
	add := func(name string, required bool, err error, ok string) {
		c := Check{Name: name, Required: required, Pass: err == nil, Detail: ok}
		if err != nil {
			c.Detail = err.Error()
		}
		rep.Checks = append(rep.Checks, c)
	}

	if err := schema.Validate(raw); err != nil {
		add("schema", true, err, "")
		rep.OK = false
		return rep // nothing else is meaningful
	}
	add("schema", true, nil, "valid against receipt v1 schema")
	var r receipt.Receipt
	if err := json.Unmarshal(raw, &r); err != nil {
		add("schema", true, err, "")
		return rep
	}

	add("signature", true, receipt.VerifySignature(raw, r.SigningKey.PublicKey, r.Signature),
		"ECDSA P-256 over the JCS body with signing_key")

	nonce, nerr := r.ExpectedNonce()
	if nerr == nil && nonce != r.Nonce {
		nerr = fmt.Errorf("receipt nonce %s, recomputed %s", r.Nonce, nonce)
	}
	cpuNonce, cpuErr := v.cpu(&r)
	gpuNonce, gpuErr := v.gpu(&r)
	if nerr == nil {
		var bad []string
		if cpuErr == nil && !containsFold(cpuNonce, nonce) {
			bad = append(bad, "CPU attestation nonce "+strings.Join(cpuNonce, ","))
		}
		if gpuErr == nil && !strings.EqualFold(gpuNonce, nonce) {
			bad = append(bad, "GPU attestation nonce "+gpuNonce)
		}
		if len(bad) > 0 {
			nerr = fmt.Errorf("recomputed %s, but %s", nonce, strings.Join(bad, "; "))
		}
	}
	add("nonce", true, nerr, "recomputed nonce matches the receipt and both attestations")
	add("cpu_attestation", true, cpuErr, fmt.Sprintf("%s %s token chains to the pinned %s keys; policy claims match", r.Platform.CPU.Type, v.Policy.CPU.Verifier, v.Policy.CPU.Issuer))
	add("gpu_attestation", true, gpuErr, fmt.Sprintf("NVIDIA result verified (%s); driver %s, VBIOS %s allowed", r.Platform.GPU.Verifier, r.Platform.GPU.DriverVersion, r.Platform.GPU.VBIOSVersion))
	add("image", true, v.image(&r), "cosign bundle verifies for the expected workflow identity and digest")

	if in.ModelDir != "" || in.InputsFile != "" || in.OutputsFile != "" {
		add("content", true, content(&r, in), "recomputed hashes of supplied files match")
	}
	rep.OK = true
	for _, c := range rep.Checks {
		if c.Required && !c.Pass {
			rep.OK = false
		}
	}
	return rep
}

// cpu verifies the CPU attestation token and returns the nonce(s) it vouches for.
func (v *Verifier) cpu(r *receipt.Receipt) ([]string, error) {
	p := v.Policy.CPU
	if r.Platform.CPU.Type != p.Type {
		return nil, fmt.Errorf("cpu_tee.type %q, policy wants %q", r.Platform.CPU.Type, p.Type)
	}
	if r.Platform.CPU.VerifierToken == "" {
		return nil, fmt.Errorf("no verifier token (raw %s evidence verification is not implemented in v1)", r.Platform.CPU.Type)
	}
	set, err := loadJWKS(v.Policy.path(p.JWKSFile))
	if err != nil {
		return nil, err
	}
	c, err := verifyJWT(r.Platform.CPU.VerifierToken, set, p.Issuer)
	if err != nil {
		return nil, fmt.Errorf("token: %w", err)
	}
	keys := make([]string, 0, len(p.checks))
	for k := range p.checks {
		keys = append(keys, k)
	}
	slices.Sort(keys)
	for _, k := range keys {
		if got, err := claimString(c, k); err != nil || got != p.checks[k] {
			return nil, fmt.Errorf("claim %s = %q, policy wants %q", k, got, p.checks[k])
		}
	}
	ns, err := claimStrings(c, p.Claims.Nonce)
	if err != nil {
		return nil, err
	}
	for i := range ns {
		ns[i] = normaliseNonce(ns[i])
	}
	return ns, nil
}

// gpu verifies the NVIDIA result (NRAS EAT: [["JWT", overall], {"GPU-0": jwt}]).
func (v *Verifier) gpu(r *receipt.Receipt) (string, error) {
	p := v.Policy.GPU
	g := r.Platform.GPU
	if g.Verifier != "nras" {
		if !p.AllowUnsignedLocal {
			return "", fmt.Errorf("GPU result from %q verifier is not NVIDIA-signed; policy requires nras", g.Verifier)
		}
	}
	var eat []json.RawMessage
	if err := json.Unmarshal([]byte(g.VerifierResult), &eat); err != nil || len(eat) < 2 {
		return "", fmt.Errorf("verifier_result is not an NRAS EAT: %v", err)
	}
	var overall []string
	if err := json.Unmarshal(eat[0], &overall); err != nil || len(overall) != 2 {
		return "", fmt.Errorf("EAT overall token: %v", err)
	}
	var perGPU map[string]string
	if err := json.Unmarshal(eat[1], &perGPU); err != nil || len(perGPU) == 0 {
		return "", fmt.Errorf("EAT per-GPU tokens: %v", err)
	}
	set, err := loadJWKS(v.Policy.path(p.JWKSFile))
	if err != nil {
		return "", err
	}
	oc, err := verifyJWT(overall[1], set, p.Issuer)
	if err != nil {
		return "", fmt.Errorf("overall token: %w", err)
	}
	if res, _ := claim(oc, p.Claims.OverallResult); res != true {
		return "", fmt.Errorf("%s = %v", p.Claims.OverallResult, res)
	}
	nonce, err := claimString(oc, p.Claims.Nonce)
	if err != nil {
		return "", err
	}
	for id, tok := range perGPU {
		c, err := verifyJWT(tok, set, p.Issuer)
		if err != nil {
			return "", fmt.Errorf("%s token: %w", id, err)
		}
		drv, _ := claimString(c, p.Claims.Driver)
		vb, _ := claimString(c, p.Claims.VBIOS)
		if drv != g.DriverVersion || !strings.EqualFold(vb, g.VBIOSVersion) {
			return "", fmt.Errorf("%s: attested driver %s / VBIOS %s differ from receipt %s / %s", id, drv, vb, g.DriverVersion, g.VBIOSVersion)
		}
		if len(p.AllowedDrivers) > 0 && !slices.Contains(p.AllowedDrivers, drv) {
			return "", fmt.Errorf("%s: driver %s not allowed by policy", id, drv)
		}
		if len(p.AllowedVBIOS) > 0 && !containsFold(p.AllowedVBIOS, vb) {
			return "", fmt.Errorf("%s: VBIOS %s not allowed by policy", id, vb)
		}
		if m, _ := claimString(c, p.Claims.Measurement); m != "success" {
			return "", fmt.Errorf("%s: %s = %q", id, p.Claims.Measurement, m)
		}
		if d, _ := claimString(c, p.Claims.Debug); d != "disabled" {
			return "", fmt.Errorf("%s: %s = %q", id, p.Claims.Debug, d)
		}
		if p.Claims.CCMode != "" {
			if cc, _ := claimString(c, p.Claims.CCMode); cc != p.Claims.CCModeValue {
				return "", fmt.Errorf("%s: CC mode %s = %q", id, p.Claims.CCMode, cc)
			}
		}
		if n, err := claimString(c, p.Claims.Nonce); err == nil && !strings.EqualFold(n, nonce) {
			return "", fmt.Errorf("%s: per-GPU nonce differs from overall", id)
		}
	}
	return normaliseNonce(nonce), nil
}

func (v *Verifier) image(r *receipt.Receipt) error {
	d, err := r.ImageDigest()
	if err != nil {
		return err
	}
	repo := r.Software.Image[:strings.LastIndex(r.Software.Image, "@")]
	if a := v.Policy.Image.AllowedRepositories; len(a) > 0 && !slices.Contains(a, repo) {
		return fmt.Errorf("repository %s not allowed by policy", repo)
	}
	b, err := base64.StdEncoding.DecodeString(r.Software.CosignBundle)
	if err != nil {
		return fmt.Errorf("cosign_bundle: %w", err)
	}
	if v.Image == nil {
		return fmt.Errorf("no image verifier configured")
	}
	ip := v.Policy.Image
	ip.TrustedRoot = v.Policy.path(ip.TrustedRoot)
	return v.Image(b, d, ip)
}

func content(r *receipt.Receipt, in Inputs) error {
	var bad []string
	if in.ModelDir != "" {
		root, _, err := receipt.FilesRoot(in.ModelDir)
		if err != nil {
			return err
		}
		if root != r.Model.FilesRootSHA256 {
			bad = append(bad, "model files")
		}
	}
	for _, f := range []struct {
		path string
		c    receipt.Content
		name string
	}{{in.InputsFile, r.Inputs, "inputs"}, {in.OutputsFile, r.Outputs, "outputs"}} {
		if f.path == "" {
			continue
		}
		n, root, err := receipt.JSONLRoot(f.path)
		if err != nil {
			return fmt.Errorf("%s: %w", f.name, err)
		}
		if root != f.c.RootSHA256 || n != f.c.Count {
			bad = append(bad, fmt.Sprintf("%s (count %d vs %d)", f.name, n, f.c.Count))
		}
	}
	if len(bad) > 0 {
		return fmt.Errorf("hash mismatch: %s", strings.Join(bad, ", "))
	}
	return nil
}

func containsFold(list []string, s string) bool {
	for _, x := range list {
		if strings.EqualFold(x, s) {
			return true
		}
	}
	return false
}

// normaliseNonce accepts hex or base64 renderings of the 32-byte nonce.
func normaliseNonce(s string) string {
	if b, err := hex.DecodeString(s); err == nil && len(b) == 32 {
		return strings.ToLower(s)
	}
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.RawURLEncoding, base64.URLEncoding} {
		if b, err := enc.DecodeString(s); err == nil && len(b) == 32 {
			return hex.EncodeToString(b)
		}
	}
	return s
}
