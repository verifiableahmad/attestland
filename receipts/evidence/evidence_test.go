package evidence

import (
	"context"
	"slices"
	"strings"
	"testing"
)

// AzureMAA must pass the MAA URL and nonce to AttestationClient and return its token.
func TestAzureMAAArgs(t *testing.T) {
	nonce := strings.Repeat("ab", 32)
	var got []string
	a := &AzureMAA{MAAURL: "https://sharedeus2.eus2.attest.azure.net", Run: func(_ context.Context, name string, args ...string) ([]byte, error) {
		got = append([]string{name}, args...)
		return []byte("h.p.s\n"), nil
	}}
	c, err := a.AttestCPU(context.Background(), nonce)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"AttestationClient", "-a", "https://sharedeus2.eus2.attest.azure.net", "-n", nonce, "-o", "token"}
	if !slices.Equal(got, want) {
		t.Fatalf("args %v", got)
	}
	if c.Type != "sev-snp" || c.VerifierToken != "h.p.s" {
		t.Fatalf("got %+v", c)
	}
}

func TestAzureMAARejectsNonJWT(t *testing.T) {
	a := &AzureMAA{MAAURL: "https://maa.example", Run: func(context.Context, string, ...string) ([]byte, error) {
		return []byte("error: attestation failed"), nil
	}}
	if _, err := a.AttestCPU(context.Background(), strings.Repeat("00", 32)); err == nil {
		t.Fatal("non-JWT output accepted")
	}
}

func TestAzureMAARequiresURL(t *testing.T) {
	a := &AzureMAA{Run: func(context.Context, string, ...string) ([]byte, error) { return []byte("h.p.s"), nil }}
	if _, err := a.AttestCPU(context.Background(), strings.Repeat("00", 32)); err == nil {
		t.Fatal("empty MAA URL accepted")
	}
}
