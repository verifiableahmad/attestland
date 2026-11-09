package verify

import (
	"encoding/hex"
	"fmt"

	"github.com/sigstore/sigstore-go/pkg/bundle"
	"github.com/sigstore/sigstore-go/pkg/root"
	"github.com/sigstore/sigstore-go/pkg/verify"
)

// SigstoreImageVerifier verifies the receipt's cosign bundle: the image's SLSA
// provenance attestation, signed keyless in GitHub Actions (Fulcio cert from
// the workflow's OIDC identity, entry in Rekor). The in-toto subject must be
// the image digest, and the certificate must name the expected workflow.
// Offline: the Sigstore trusted root is pinned in the policy directory.
func SigstoreImageVerifier(bundleJSON []byte, digestHex string, p ImagePolicy) error {
	if p.TrustedRoot == "" {
		return fmt.Errorf("policy image.trusted_root not set")
	}
	tr, err := root.NewTrustedRootFromPath(p.TrustedRoot)
	if err != nil {
		return fmt.Errorf("trusted root: %w", err)
	}
	var b bundle.Bundle
	if err := b.UnmarshalJSON(bundleJSON); err != nil {
		return fmt.Errorf("bundle: %w", err)
	}
	v, err := verify.NewVerifier(tr, verify.WithSignedCertificateTimestamps(1),
		verify.WithTransparencyLog(1), verify.WithObserverTimestamps(1))
	if err != nil {
		return err
	}
	return VerifyEntity(v, &b, digestHex, p)
}

// VerifyEntity applies the identity and digest policy to any signed entity
// (a parsed bundle in production, a virtual-Sigstore entity in tests).
func VerifyEntity(v *verify.Verifier, e verify.SignedEntity, digestHex string, p ImagePolicy) error {
	digest, err := hex.DecodeString(digestHex)
	if err != nil {
		return err
	}
	id, err := verify.NewShortCertificateIdentity(p.CertificateOIDCIssuer, "", p.CertificateIdentity, "")
	if err != nil {
		return err
	}
	_, err = v.Verify(e, verify.NewPolicy(verify.WithArtifactDigest("sha256", digest), verify.WithCertificateIdentity(id)))
	return err
}
