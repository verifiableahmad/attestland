package evidence

import (
	"context"
	"os"
	"slices"
	"strings"
	"testing"
)

// GoogleCA must pass the nonce and audience to gotpm and read the token from --output.
func TestGoogleCAArgs(t *testing.T) {
	nonce := strings.Repeat("ab", 32)
	var got []string
	g := &GoogleCA{Audience: "attestland-receipts", Run: func(_ context.Context, name string, args ...string) ([]byte, error) {
		got = append([]string{name}, args...)
		out := args[slices.Index(args, "--output")+1]
		return nil, os.WriteFile(out, []byte("h.p.s\n"), 0o600)
	}}
	c, err := g.AttestCPU(context.Background(), nonce)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"gotpm", "token", "--tee-technology", "tdx", "--custom-nonce", nonce}
	if !slices.Equal(got[:len(want)], want) || !slices.Contains(got, "attestland-receipts") {
		t.Fatalf("args %v", got)
	}
	if c.Type != "tdx" || c.VerifierToken != "h.p.s" {
		t.Fatalf("got %+v", c)
	}
}

func TestGoogleCARejectsNonJWT(t *testing.T) {
	g := &GoogleCA{Run: func(_ context.Context, _ string, args ...string) ([]byte, error) {
		return nil, os.WriteFile(args[slices.Index(args, "--output")+1], []byte("error: quota"), 0o600)
	}}
	if _, err := g.AttestCPU(context.Background(), strings.Repeat("00", 32)); err == nil {
		t.Fatal("non-JWT output accepted")
	}
}
