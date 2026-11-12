package receipts_test

// End-to-end: issue a receipt with the test platform (MAA-shaped CPU
// token and an NRAS-shaped GPU result, signed by local keys) and a virtual
// Sigstore image attestation, verify it, then run every negative test from spec
// 7.6. Each must fail verification.

import (
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/sigstore/sigstore-go/pkg/testing/ca"
	sgverify "github.com/sigstore/sigstore-go/pkg/verify"

	"attestland/receipts/evidence"
	"attestland/receipts/issuer"
	"attestland/receipts/receipt"
	"attestland/receipts/verify"
)

const (
	ciIdentity = "ci@attestland.test"
	ciIssuer   = "https://token.actions.githubusercontent.com"
	imageRepo  = "ghcr.io/test/vllm-receipts"
)

type fixture struct {
	dir      string
	tp       *evidence.TestPlatform
	policy   *verify.Policy
	verifier *verify.Verifier
	entities map[string]*ca.TestEntity
	vs       *ca.VirtualSigstore
	digest   string
}

// shapes maps each CPU token shape to its test policy.
var shapes = map[string]string{"maa": "policy/test/policy.yaml"}

func forEachShape(t *testing.T, fn func(t *testing.T, f *fixture)) {
	for _, shape := range []string{"maa"} {
		t.Run(shape, func(t *testing.T) { fn(t, newFixture(t, shape)) })
	}
}

func newFixture(t *testing.T, shape string) *fixture {
	t.Helper()
	dir := t.TempDir()
	tp, err := evidence.NewTestPlatform(filepath.Join(dir, "policy"))
	if err != nil {
		t.Fatal(err)
	}
	tp.CPUVerifier = shape
	pol, _ := os.ReadFile(shapes[shape])
	os.WriteFile(filepath.Join(dir, "policy", "policy.yaml"), pol, 0o644)
	p, err := verify.LoadPolicy(filepath.Join(dir, "policy", "policy.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	vs, err := ca.NewVirtualSigstore()
	if err != nil {
		t.Fatal(err)
	}
	f := &fixture{dir: dir, tp: tp, policy: p, vs: vs, entities: map[string]*ca.TestEntity{}}
	sv, err := sgverify.NewVerifier(vs, sgverify.WithTransparencyLog(1), sgverify.WithObserverTimestamps(1))
	if err != nil {
		t.Fatal(err)
	}
	// Bundles in tests are {"test_entity": id}; the real path parses a Sigstore bundle.
	f.verifier = &verify.Verifier{Policy: p, Image: func(b []byte, digest string, ip verify.ImagePolicy) error {
		var ref struct {
			ID string `json:"test_entity"`
		}
		if err := json.Unmarshal(b, &ref); err != nil {
			return err
		}
		e, ok := f.entities[ref.ID]
		if !ok {
			return fmt.Errorf("unknown entity")
		}
		return verify.VerifyEntity(sv, e, digest, ip)
	}}

	// Model, inputs, outputs.
	md := filepath.Join(dir, "model")
	os.MkdirAll(md, 0o755)
	os.WriteFile(filepath.Join(md, "config.json"), []byte(`{"model_type":"gpt2"}`), 0o644)
	os.WriteFile(filepath.Join(md, "model.safetensors"), []byte("weights-bytes"), 0o644)
	os.WriteFile(filepath.Join(dir, "in.jsonl"), []byte("{\"prompt\":\"hi\", \"id\":1}\n{\"id\":2,\"prompt\":\"yo\"}\n"), 0o644)
	os.WriteFile(filepath.Join(dir, "out.jsonl"), []byte("{\"id\":1,\"text\":\"hello\"}\n{\"id\":2,\"text\":\"hey\"}\n"), 0o644)
	sum := sha256.Sum256([]byte("image-manifest"))
	f.digest = hex.EncodeToString(sum[:])
	return f
}

// provenance makes a virtual-Sigstore SLSA provenance attestation for digest.
func (f *fixture) provenance(t *testing.T, id, identity, digest string) []byte {
	stmt := fmt.Sprintf(`{"_type":"https://in-toto.io/Statement/v1","subject":[{"name":%q,"digest":{"sha256":%q}}],"predicateType":"https://slsa.dev/provenance/v1","predicate":{}}`, imageRepo, digest)
	e, err := f.vs.Attest(identity, ciIssuer, []byte(stmt))
	if err != nil {
		t.Fatal(err)
	}
	f.entities[id] = e
	return []byte(fmt.Sprintf(`{"test_entity":%q}`, id))
}

func (f *fixture) issue(t *testing.T, out string) *receipt.Receipt {
	t.Helper()
	is := &issuer.Issuer{CPU: f.tp, GPU: f.tp}
	r, err := is.Issue(context.Background(), issuer.Request{
		JobID: "bench-" + out, Kind: "inference", Scheduler: "k8s",
		StartedAt: time.Date(2026, 11, 7, 12, 0, 0, 0, time.UTC), FinishedAt: time.Date(2026, 11, 7, 12, 5, 0, 0, time.UTC),
		Cloud: "test", VMSize: "test", Image: imageRepo + "@sha256:" + f.digest,
		Bundle:    f.provenance(t, out, ciIdentity, f.digest),
		ModelName: "al-gpt-124m", ModelDir: filepath.Join(f.dir, "model"),
		Inputs: filepath.Join(f.dir, "in.jsonl"), Outputs: filepath.Join(f.dir, "out.jsonl"),
		OutDir: filepath.Join(f.dir, out),
	})
	if err != nil {
		t.Fatal(err)
	}
	return r
}

func marshal(r *receipt.Receipt) []byte { b, _ := json.MarshalIndent(r, "", "  "); return b }

func (f *fixture) check(raw []byte, out string) verify.Report {
	return f.verifier.Verify(raw, verify.Inputs{ModelDir: filepath.Join(f.dir, "model"),
		InputsFile: filepath.Join(f.dir, out, "inputs.jsonl"), OutputsFile: filepath.Join(f.dir, out, "outputs.jsonl")})
}

func failed(rep verify.Report) []string {
	var out []string
	for _, c := range rep.Checks {
		if !c.Pass {
			out = append(out, c.Name)
		}
	}
	return out
}

func TestValidReceiptPassesEveryCheck(t *testing.T) {
	forEachShape(t, testValidReceipt)
}

func testValidReceipt(t *testing.T, f *fixture) {
	r := f.issue(t, "r1")
	rep := f.check(marshal(r), "r1")
	if !rep.OK {
		t.Fatalf("valid receipt rejected: %+v", rep.Checks)
	}
	names := map[string]bool{}
	for _, c := range rep.Checks {
		names[c.Name] = true
	}
	for _, n := range []string{"schema", "signature", "nonce", "cpu_attestation", "gpu_attestation", "image", "content"} {
		if !names[n] {
			t.Errorf("check %s missing", n)
		}
	}
}

func TestNegative(t *testing.T) {
	forEachShape(t, testNegative)
}

func testNegative(t *testing.T, f *fixture) {
	good := f.issue(t, "r1")
	older := f.issue(t, "r0") // a different, earlier job
	cases := []struct {
		name   string
		mutate func(t *testing.T, raw []byte) []byte
		files  func()
		want   string // a check that must fail
	}{
		{"one byte changed in an output", nil, func() {
			p := filepath.Join(f.dir, "r1", "outputs.jsonl")
			b, _ := os.ReadFile(p)
			b[10] ^= 0x01
			os.WriteFile(p, b, 0o644)
		}, "content"},
		{"image digest swapped", func(t *testing.T, raw []byte) []byte {
			other := sha256.Sum256([]byte("evil-image"))
			return []byte(strings.Replace(string(raw), f.digest, hex.EncodeToString(other[:]), 1))
		}, nil, "nonce"},
		{"attestation copied from an older receipt", func(t *testing.T, raw []byte) []byte {
			r := *good
			r.Platform = older.Platform
			return marshal(&r)
		}, nil, "nonce"},
		{"signature from a different key", func(t *testing.T, raw []byte) []byte {
			k, _ := receipt.NewKey()
			r := *good
			r.Sign(k)
			return marshal(&r)
		}, nil, "signature"},
		{"driver version outside policy", func(t *testing.T, raw []byte) []byte {
			f.policy.GPU.AllowedDrivers = []string{"999.99.99"}
			return raw
		}, nil, "gpu_attestation"},
		{"receipt body edited after signing", func(t *testing.T, raw []byte) []byte {
			return []byte(strings.Replace(string(raw), `"bench-r1"`, `"bench-r9"`, 1))
		}, nil, "signature"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			saved := f.policy.GPU.AllowedDrivers
			defer func() { f.policy.GPU.AllowedDrivers = saved }()
			// fresh copies of the content files for each case
			f.issueFilesReset(t, good)
			raw := marshal(good)
			if tc.mutate != nil {
				raw = tc.mutate(t, raw)
			}
			if tc.files != nil {
				tc.files()
			}
			rep := f.check(raw, "r1")
			if rep.OK {
				t.Fatalf("tampered receipt accepted: %+v", rep.Checks)
			}
			if !contains(failed(rep), tc.want) {
				t.Fatalf("want %s to fail, failed: %v", tc.want, failed(rep))
			}
		})
	}
}

// issueFilesReset rewrites r1's canonical content files from the originals.
func (f *fixture) issueFilesReset(t *testing.T, r *receipt.Receipt) {
	for _, x := range [][2]string{{"in.jsonl", "inputs.jsonl"}, {"out.jsonl", "outputs.jsonl"}} {
		in, _ := os.Open(filepath.Join(f.dir, x[0]))
		out, _ := os.Create(filepath.Join(f.dir, "r1", x[1]))
		receipt.CanonicalJSONL(in, out)
		in.Close()
		out.Close()
	}
}

func TestImageFromWrongWorkflowRejected(t *testing.T) {
	forEachShape(t, testWrongWorkflow)
}

func testWrongWorkflow(t *testing.T, f *fixture) {
	r := f.issue(t, "r1")
	r2 := *r
	r2.Software.CosignBundle = base64.StdEncoding.EncodeToString(f.provenance(t, "evil", "attacker@example.com", f.digest))
	k, _ := receipt.NewKey()
	pub, spki, _ := receipt.PublicKeyB64(&k.PublicKey)
	r2.SigningKey.PublicKey = pub
	_ = spki
	r2.Sign(k)
	rep := f.check(marshal(&r2), "r1")
	if rep.OK || !contains(failed(rep), "image") {
		t.Fatalf("wrong signer accepted: %v", failed(rep))
	}
}

// A debug VM, or a policy claim the token does not satisfy, fails the CPU check.
func TestCPUPolicyClaims(t *testing.T) {
	forEachShape(t, func(t *testing.T, f *fixture) {
		f.tp.Debuggable = true
		rep := f.check(marshal(f.issue(t, "r1")), "r1")
		if rep.OK || !contains(failed(rep), "cpu_attestation") {
			t.Fatalf("debug VM accepted: %v", failed(rep))
		}
	})
	f := newFixture(t, "maa")
	r := marshal(f.issue(t, "r1"))
	reloadWith(t, f, `allowed_launch_measurements: ["a1b2c3`, `allowed_launch_measurements: ["ffffff`)
	if rep := f.check(r, "r1"); rep.OK || !contains(failed(rep), "cpu_attestation") {
		t.Fatalf("launch measurement off the allow-list accepted: %v", failed(rep))
	}
	f = newFixture(t, "maa")
	r = marshal(f.issue(t, "r1"))
	reloadWith(t, f, "require_compliance: azure-compliant-cvm", "require_compliance: other-status")
	if rep := f.check(r, "r1"); rep.OK || !contains(failed(rep), "cpu_attestation") {
		t.Fatalf("token with the wrong compliance status accepted: %v", failed(rep))
	}
}

// reloadWith rewrites the fixture's policy file with one substitution and reloads it.
func reloadWith(t *testing.T, f *fixture, old, new string) {
	t.Helper()
	path := filepath.Join(f.dir, "policy", "policy.yaml")
	b, _ := os.ReadFile(path)
	if !strings.Contains(string(b), old) {
		t.Fatalf("policy has no %q", old)
	}
	os.WriteFile(path, []byte(strings.Replace(string(b), old, new, 1)), 0o644)
	p, err := verify.LoadPolicy(path)
	if err != nil {
		t.Fatal(err)
	}
	f.policy, f.verifier.Policy = p, p
}

func TestPolicyRejectsUnknownCPUVerifier(t *testing.T) {
	path := filepath.Join(t.TempDir(), "policy.yaml")
	os.WriteFile(path, []byte("cpu: {type: sev-snp, verifier: tpm}\n"), 0o644)
	if _, err := verify.LoadPolicy(path); err == nil {
		t.Fatal("unknown cpu.verifier accepted")
	}
}

func TestIssuerRefusesUnverifiedImage(t *testing.T) {
	f := newFixture(t, "maa")
	is := &issuer.Issuer{CPU: f.tp, GPU: f.tp, VerifyImage: func([]byte, string) error { return fmt.Errorf("identity mismatch") }}
	_, err := is.Issue(context.Background(), issuer.Request{JobID: "x", Kind: "inference", Scheduler: "k8s",
		Cloud: "test", Image: imageRepo + "@sha256:" + f.digest, Bundle: []byte("{}"),
		ModelDir: filepath.Join(f.dir, "model"), Inputs: filepath.Join(f.dir, "in.jsonl"),
		Outputs: filepath.Join(f.dir, "out.jsonl"), OutDir: filepath.Join(f.dir, "x")})
	if err == nil || !strings.Contains(err.Error(), "refusing") {
		t.Fatalf("want refusal, got %v", err)
	}
}

// The model root must match what workloads/train/export_hf.py computes.
func TestFilesRootMatchesPythonExport(t *testing.T) {
	py, err := exec.LookPath(filepath.Join("..", ".venv", "bin", "python"))
	if err != nil {
		t.Skip("no repo venv")
	}
	dir := t.TempDir()
	os.WriteFile(filepath.Join(dir, "a.bin"), []byte("alpha"), 0o644)
	os.WriteFile(filepath.Join(dir, "config.json"), []byte("{}"), 0o644)
	out, err := exec.Command(py, "-c", "import sys; sys.path.insert(0,'../workloads/train'); from export_hf import files_root; print(files_root(__import__('pathlib').Path(sys.argv[1])))", dir).Output()
	if err != nil {
		t.Fatalf("%v %s", err, out)
	}
	got, _, err := receipt.FilesRoot(dir)
	if err != nil || got != strings.TrimSpace(string(out)) {
		t.Fatalf("go %s, python %s", got, out)
	}
}

func contains(list []string, s string) bool {
	for _, x := range list {
		if x == s {
			return true
		}
	}
	return false
}
