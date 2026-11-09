// alissue builds and signs a job receipt inside the confidential VM (spec 7.5).
//
//	alissue --job-id bench-001 --model-dir /models/current --model-name al-gpt-124m \
//	  --inputs requests.jsonl --outputs responses.jsonl \
//	  --image ghcr.io/org/vllm-receipts@sha256:... --bundle provenance.sigstore.json \
//	  --platform gcp --out ./receipt-001
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"attestland/receipts/evidence"
	"attestland/receipts/issuer"
)

func main() {
	var (
		jobID     = flag.String("job-id", "", "job id")
		kind      = flag.String("kind", "inference", "inference|training")
		sched     = flag.String("scheduler", "k8s", "k8s|slurm|none")
		started   = flag.String("started-at", "", "RFC3339 (default: now)")
		finished  = flag.String("finished-at", "", "RFC3339 (default: now)")
		modelDir  = flag.String("model-dir", "", "model directory (hashed)")
		modelName = flag.String("model-name", "", "model name")
		inputs    = flag.String("inputs", "", "requests JSONL")
		outputs   = flag.String("outputs", "", "responses JSONL")
		image     = flag.String("image", "", "image reference pinned by @sha256:")
		bundle    = flag.String("bundle", "", "Sigstore bundle of the image provenance")
		platform  = flag.String("platform", "gcp", "gcp|test")
		vmSize    = flag.String("vm-size", "", "VM size (default: the platform's confidential VM)")
		gotpmBin  = flag.String("gotpm", "gotpm", "go-tpm-tools gotpm CLI (gcp)")
		gcaAud    = flag.String("gca-audience", "attestland-receipts", "aud claim of the GCA token (gcp)")
		tdxQuote  = flag.Bool("tdx-quote", true, "also embed a raw TDX quote over the nonce (gcp)")
		nvatBin   = flag.String("nvattest", "nvattest", "NVIDIA nvattest CLI")
		nvatVer   = flag.String("nvat-version", os.Getenv("NVAT_VERSION"), "recorded in the receipt")
		gpuVerif  = flag.String("gpu-verifier", "remote", "remote (NRAS) | local")
		testKeys  = flag.String("test-keys", "", "test platform key dir (writes JWKS for the test policy)")
		out       = flag.String("out", ".", "output directory")
	)
	flag.Parse()
	ts := func(s string) time.Time {
		if s == "" {
			return time.Now()
		}
		t, err := time.Parse(time.RFC3339, s)
		if err != nil {
			fail(err)
		}
		return t
	}
	b, err := os.ReadFile(*bundle)
	if err != nil {
		fail(fmt.Errorf("bundle: %w", err))
	}
	is := &issuer.Issuer{}
	switch *platform {
	case "gcp":
		g := &evidence.GoogleCA{Binary: *gotpmBin, Audience: *gcaAud}
		if *tdxQuote {
			g.Quote = &evidence.TDXConfigFS{}
		}
		is.CPU = g
		is.GPU = &evidence.NVAT{Binary: *nvatBin, Verifier: *gpuVerif, Version: *nvatVer}
		defaultSize(vmSize, "a3-highgpu-1g")
	case "test":
		tp, err := evidence.NewTestPlatform(*testKeys)
		if err != nil {
			fail(err)
		}
		is.CPU, is.GPU = tp, tp
		*vmSize = "test"
	default:
		fail(fmt.Errorf("unknown platform %q", *platform))
	}
	r, err := is.Issue(context.Background(), issuer.Request{
		JobID: *jobID, Kind: *kind, Scheduler: *sched, StartedAt: ts(*started), FinishedAt: ts(*finished),
		Cloud: map[string]string{"gcp": "gcp", "test": "test"}[*platform], VMSize: *vmSize,
		Image: *image, Bundle: b, ModelName: *modelName, ModelDir: *modelDir,
		Inputs: *inputs, Outputs: *outputs, OutDir: *out,
	})
	if err != nil {
		fail(err)
	}
	raw, _ := json.MarshalIndent(r, "", "  ")
	path := filepath.Join(*out, "receipt.json")
	if err := os.WriteFile(path, append(raw, '\n'), 0o644); err != nil {
		fail(err)
	}
	fmt.Printf("wrote %s (nonce %s)\n", path, r.Nonce)
}

func defaultSize(s *string, v string) {
	if *s == "" {
		*s = v
	}
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "alissue:", err)
	os.Exit(1)
}
