// Package issuer builds and signs a receipt inside the confidential VM (spec 7.5).
//
// Order matters: hash the job's content first, generate a fresh key, derive
// the nonce from both, then collect CPU and GPU attestation over that nonce,
// and sign last. The private key lives only in this process.
package issuer

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"attestland/receipts/evidence"
	"attestland/receipts/receipt"
)

// Request describes one job (one benchmark run or time window of requests).
type Request struct {
	JobID      string
	Kind       string // inference | training
	Scheduler  string // k8s | slurm | none
	StartedAt  time.Time
	FinishedAt time.Time
	Cloud      string
	VMSize     string
	Image      string // repo@sha256:<digest>
	Bundle     []byte // Sigstore bundle of the image's provenance attestation
	ModelName  string
	ModelDir   string
	Inputs     string // JSONL; rewritten canonically next to the receipt
	Outputs    string
	OutDir     string
}

// Issuer holds the attesters for this platform.
type Issuer struct {
	CPU evidence.CPUAttester
	GPU evidence.GPUAttester
	// VerifyImage, when set, refuses to issue for images whose signature does
	// not match the expected workflow identity (spec 7.4).
	VerifyImage func(bundle []byte, digestHex string) error
}

// Issue writes receipt.json, inputs.jsonl and outputs.jsonl into req.OutDir.
func (is *Issuer) Issue(ctx context.Context, req Request) (*receipt.Receipt, error) {
	if err := os.MkdirAll(req.OutDir, 0o755); err != nil {
		return nil, err
	}
	r := &receipt.Receipt{
		Version: receipt.Version,
		Job: receipt.Job{ID: req.JobID, Kind: req.Kind, Scheduler: req.Scheduler,
			StartedAt: req.StartedAt.UTC().Format(time.RFC3339Nano), FinishedAt: req.FinishedAt.UTC().Format(time.RFC3339Nano)},
		Platform: receipt.Platform{Cloud: req.Cloud, VMSize: req.VMSize},
		Software: receipt.Software{Image: req.Image, CosignBundle: evidence.B64(req.Bundle)},
	}
	digest, err := r.ImageDigest()
	if err != nil {
		return nil, err
	}
	if is.VerifyImage != nil {
		if err := is.VerifyImage(req.Bundle, digest); err != nil {
			return nil, fmt.Errorf("refusing to issue: image signature: %w", err)
		}
	}

	root, _, err := receipt.FilesRoot(req.ModelDir)
	if err != nil {
		return nil, fmt.Errorf("model: %w", err)
	}
	r.Model = receipt.Model{Name: req.ModelName, FilesRootSHA256: root}
	for _, x := range []struct {
		src, dst string
		c        *receipt.Content
	}{{req.Inputs, "inputs.jsonl", &r.Inputs}, {req.Outputs, "outputs.jsonl", &r.Outputs}} {
		if err := canonicalCopy(x.src, filepath.Join(req.OutDir, x.dst)); err != nil {
			return nil, err
		}
		n, h, err := receipt.JSONLRoot(filepath.Join(req.OutDir, x.dst))
		if err != nil {
			return nil, err
		}
		*x.c = receipt.Content{Count: n, RootSHA256: h, Format: "jcs-jsonl"}
	}

	key, err := receipt.NewKey()
	if err != nil {
		return nil, err
	}
	pub, spki, err := receipt.PublicKeyB64(&key.PublicKey)
	if err != nil {
		return nil, err
	}
	r.SigningKey = receipt.SigningKey{Alg: "ecdsa-p256", PublicKey: pub}
	if r.Nonce, err = receipt.ComputeNonce(root, r.Inputs.RootSHA256, r.Outputs.RootSHA256, digest, spki); err != nil {
		return nil, err
	}

	cpu, err := is.CPU.AttestCPU(ctx, r.Nonce)
	if err != nil {
		return nil, fmt.Errorf("cpu attestation: %w", err)
	}
	r.Platform.CPU = receipt.CPUTEE{Type: cpu.Type, Evidence: evidence.B64(cpu.Evidence),
		VerifierToken: cpu.VerifierToken, Tool: cpu.Tool}
	gpu, err := is.GPU.AttestGPU(ctx, r.Nonce)
	if err != nil {
		return nil, fmt.Errorf("gpu attestation: %w", err)
	}
	r.Platform.GPU = receipt.GPUTEE{Type: "nvidia-cc", Evidence: evidence.B64(gpu.Evidence),
		VerifierResult: gpu.VerifierResult, Verifier: gpu.Verifier,
		DriverVersion: gpu.DriverVersion, VBIOSVersion: gpu.VBIOSVersion, Tool: gpu.Tool}
	if req.Cloud == "test" {
		r.Platform.CPU.Type, r.Platform.GPU.Type = "test", "test"
	}

	if err := r.Sign(key); err != nil {
		return nil, err
	}
	return r, nil
}

func canonicalCopy(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	if _, err := receipt.CanonicalJSONL(in, out); err != nil {
		out.Close()
		return err
	}
	return out.Close()
}
