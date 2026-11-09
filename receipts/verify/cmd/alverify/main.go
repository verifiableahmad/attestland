// alverify checks a job receipt (spec 7.6). Exit 0 only if every required check passes.
//
//	alverify receipt.json [--model-dir DIR] [--inputs FILE] [--outputs FILE] [--policy policy.yaml] [--json]
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"attestland/receipts/verify"
)

func main() {
	fs := flag.NewFlagSet("alverify", flag.ExitOnError)
	modelDir := fs.String("model-dir", "", "model directory to re-hash")
	inputs := fs.String("inputs", "", "inputs JSONL to re-hash")
	outputs := fs.String("outputs", "", "outputs JSONL to re-hash")
	policy := fs.String("policy", "policy.yaml", "verification policy")
	asJSON := fs.Bool("json", false, "machine-readable output")
	// Accept the receipt path before or after the flags.
	args := os.Args[1:]
	var path string
	if len(args) > 0 && args[0] != "" && args[0][0] != '-' {
		path, args = args[0], args[1:]
	}
	_ = fs.Parse(args)
	if path == "" {
		path = fs.Arg(0)
	}
	if path == "" {
		fmt.Fprintln(os.Stderr, "usage: alverify receipt.json [--model-dir DIR] [--inputs FILE] [--outputs FILE] [--policy policy.yaml] [--json]")
		os.Exit(2)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		fmt.Fprintln(os.Stderr, "alverify:", err)
		os.Exit(2)
	}
	p, err := verify.LoadPolicy(*policy)
	if err != nil {
		fmt.Fprintln(os.Stderr, "alverify: policy:", err)
		os.Exit(2)
	}
	v := &verify.Verifier{Policy: p, Image: verify.SigstoreImageVerifier}
	rep := v.Verify(raw, verify.Inputs{ModelDir: *modelDir, InputsFile: *inputs, OutputsFile: *outputs})
	if *asJSON {
		_ = json.NewEncoder(os.Stdout).Encode(rep)
	} else {
		for _, c := range rep.Checks {
			mark := "PASS"
			if !c.Pass {
				mark = "FAIL"
			}
			fmt.Printf("%s  %-16s %s\n", mark, c.Name, c.Detail)
		}
		if rep.OK {
			fmt.Println("receipt OK")
		} else {
			fmt.Println("receipt REJECTED")
		}
	}
	if !rep.OK {
		os.Exit(1)
	}
}
