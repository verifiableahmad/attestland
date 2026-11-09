// Package receipt defines the v1 job receipt, its canonical form (RFC 8785),
// the nonce that binds attestations to the job and key, and signing.
package receipt

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/gowebpki/jcs"
)

const Version = "attestland/receipt/v1"

type Job struct {
	ID         string `json:"id"`
	Kind       string `json:"kind"`
	Scheduler  string `json:"scheduler"`
	StartedAt  string `json:"started_at"`
	FinishedAt string `json:"finished_at"`
}

type CPUTEE struct {
	Type          string `json:"type"`
	Evidence      string `json:"evidence"`       // base64 raw report / quote (may be empty when only a token exists)
	VerifierToken string `json:"verifier_token"` // Google Cloud Attestation JWT
	Tool          string `json:"tool,omitempty"` // tool + version that produced the evidence
}

type GPUTEE struct {
	Type           string `json:"type"`
	Evidence       string `json:"evidence"`        // base64 raw GPU attestation report + certificate chain
	VerifierResult string `json:"verifier_result"` // NRAS EAT (JSON) or local verifier result
	Verifier       string `json:"verifier,omitempty"`
	DriverVersion  string `json:"driver_version"`
	VBIOSVersion   string `json:"vbios_version"`
	Tool           string `json:"tool,omitempty"`
}

type Platform struct {
	Cloud  string `json:"cloud"`
	VMSize string `json:"vm_size"`
	CPU    CPUTEE `json:"cpu_tee"`
	GPU    GPUTEE `json:"gpu_tee"`
}

type Software struct {
	Image        string `json:"image"`         // registry/repo@sha256:<digest>
	CosignBundle string `json:"cosign_bundle"` // base64 Sigstore bundle of the image's SLSA provenance
}

type Model struct {
	Name            string `json:"name"`
	FilesRootSHA256 string `json:"files_root_sha256"`
}

type Content struct {
	Count      int    `json:"count"`
	RootSHA256 string `json:"root_sha256"`
	Format     string `json:"format,omitempty"`
}

type SigningKey struct {
	Alg       string `json:"alg"`
	PublicKey string `json:"public_key"` // base64 DER SubjectPublicKeyInfo
}

// Receipt is the v1 job receipt (spec 7.5).
type Receipt struct {
	Version    string     `json:"version"`
	Job        Job        `json:"job"`
	Platform   Platform   `json:"platform"`
	Software   Software   `json:"software"`
	Model      Model      `json:"model"`
	Inputs     Content    `json:"inputs"`
	Outputs    Content    `json:"outputs"`
	SigningKey SigningKey `json:"signing_key"`
	Nonce      string     `json:"nonce"`
	Signature  string     `json:"signature"`
}

// ImageDigest returns the hex digest from Software.Image.
func (r *Receipt) ImageDigest() (string, error) {
	i := strings.LastIndex(r.Software.Image, "@sha256:")
	if i < 0 {
		return "", errors.New("image is not pinned by @sha256: digest")
	}
	d := r.Software.Image[i+len("@sha256:"):]
	if len(d) != 64 {
		return "", errors.New("bad image digest length")
	}
	return d, nil
}

// ComputeNonce binds the job content, the image and the signing key:
//
//	SHA-256( model.files_root || inputs.root || outputs.root || image digest || SPKI(signing key) )
//
// with the four hashes as raw 32-byte values and the key as DER
// SubjectPublicKeyInfo. Both attestations carry this nonce, so they are fresh
// (the key is new per job) and vouch for this job and this key.
func ComputeNonce(modelRoot, inputsRoot, outputsRoot, imageDigest string, spki []byte) (string, error) {
	h := sha256.New()
	for _, x := range []string{modelRoot, inputsRoot, outputsRoot, imageDigest} {
		b, err := hex.DecodeString(x)
		if err != nil || len(b) != 32 {
			return "", fmt.Errorf("nonce input %q is not a sha256 hex digest", x)
		}
		h.Write(b)
	}
	h.Write(spki)
	return hex.EncodeToString(h.Sum(nil)), nil
}

// Nonce recomputes the nonce from the receipt's own fields.
func (r *Receipt) ExpectedNonce() (string, error) {
	d, err := r.ImageDigest()
	if err != nil {
		return "", err
	}
	spki, err := base64.StdEncoding.DecodeString(r.SigningKey.PublicKey)
	if err != nil {
		return "", fmt.Errorf("signing key: %w", err)
	}
	return ComputeNonce(r.Model.FilesRootSHA256, r.Inputs.RootSHA256, r.Outputs.RootSHA256, d, spki)
}

// Body is the canonical (JCS) JSON of the receipt without its signature.
func (r *Receipt) Body() ([]byte, error) {
	c := *r
	c.Signature = ""
	raw, err := json.Marshal(c)
	if err != nil {
		return nil, err
	}
	var m map[string]any
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, err
	}
	delete(m, "signature")
	raw, err = json.Marshal(m)
	if err != nil {
		return nil, err
	}
	return jcs.Transform(raw)
}

// BodyFromRaw canonicalises a receipt exactly as received (unknown fields
// included), so an edit after signing always breaks the signature.
func BodyFromRaw(raw []byte) ([]byte, error) {
	var m map[string]any
	dec := json.NewDecoder(strings.NewReader(string(raw)))
	dec.UseNumber()
	if err := dec.Decode(&m); err != nil {
		return nil, err
	}
	delete(m, "signature")
	b, err := json.Marshal(m)
	if err != nil {
		return nil, err
	}
	return jcs.Transform(b)
}

// NewKey creates the per-job ephemeral key. It never leaves the VM.
func NewKey() (*ecdsa.PrivateKey, error) {
	return ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
}

// PublicKeyB64 is the base64 DER SPKI of the key, as stored in the receipt.
func PublicKeyB64(pub *ecdsa.PublicKey) (string, []byte, error) {
	der, err := x509.MarshalPKIXPublicKey(pub)
	if err != nil {
		return "", nil, err
	}
	return base64.StdEncoding.EncodeToString(der), der, nil
}

// Sign signs SHA-256(JCS body) with ASN.1 ECDSA and stores it base64.
func (r *Receipt) Sign(key *ecdsa.PrivateKey) error {
	body, err := r.Body()
	if err != nil {
		return err
	}
	d := sha256.Sum256(body)
	sig, err := ecdsa.SignASN1(rand.Reader, key, d[:])
	if err != nil {
		return err
	}
	r.Signature = base64.StdEncoding.EncodeToString(sig)
	return nil
}

// VerifySignature checks the signature over the canonical body of raw.
func VerifySignature(raw []byte, publicKeyB64, signatureB64 string) error {
	der, err := base64.StdEncoding.DecodeString(publicKeyB64)
	if err != nil {
		return fmt.Errorf("public key: %w", err)
	}
	pk, err := x509.ParsePKIXPublicKey(der)
	if err != nil {
		return fmt.Errorf("public key: %w", err)
	}
	ec, ok := pk.(*ecdsa.PublicKey)
	if !ok || ec.Curve != elliptic.P256() {
		return errors.New("signing key is not ECDSA P-256")
	}
	sig, err := base64.StdEncoding.DecodeString(signatureB64)
	if err != nil {
		return fmt.Errorf("signature: %w", err)
	}
	body, err := BodyFromRaw(raw)
	if err != nil {
		return err
	}
	d := sha256.Sum256(body)
	if !ecdsa.VerifyASN1(ec, d[:], sig) {
		return errors.New("signature does not verify over the canonical body")
	}
	return nil
}
