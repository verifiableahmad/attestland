package verify

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rsa"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"strings"

	"github.com/golang-jwt/jwt/v5"
)

type jwk struct {
	Kty string   `json:"kty"`
	Kid string   `json:"kid"`
	Crv string   `json:"crv"`
	X   string   `json:"x"`
	Y   string   `json:"y"`
	N   string   `json:"n"`
	E   string   `json:"e"`
	X5c []string `json:"x5c"`
}

func b64u(s string) ([]byte, error) {
	return base64.RawURLEncoding.DecodeString(strings.TrimRight(s, "="))
}

func (k jwk) publicKey() (any, error) {
	if len(k.X5c) > 0 {
		der, err := base64.StdEncoding.DecodeString(k.X5c[0])
		if err != nil {
			return nil, err
		}
		c, err := x509.ParseCertificate(der)
		if err != nil {
			return nil, err
		}
		return c.PublicKey, nil
	}
	switch k.Kty {
	case "RSA":
		n, err := b64u(k.N)
		if err != nil {
			return nil, err
		}
		e, err := b64u(k.E)
		if err != nil {
			return nil, err
		}
		return &rsa.PublicKey{N: new(big.Int).SetBytes(n), E: int(new(big.Int).SetBytes(e).Int64())}, nil
	case "EC":
		var curve elliptic.Curve
		switch k.Crv {
		case "P-256":
			curve = elliptic.P256()
		case "P-384":
			curve = elliptic.P384()
		default:
			return nil, fmt.Errorf("unsupported curve %s", k.Crv)
		}
		x, err := b64u(k.X)
		if err != nil {
			return nil, err
		}
		y, err := b64u(k.Y)
		if err != nil {
			return nil, err
		}
		return &ecdsa.PublicKey{Curve: curve, X: new(big.Int).SetBytes(x), Y: new(big.Int).SetBytes(y)}, nil
	}
	return nil, fmt.Errorf("unsupported key type %s", k.Kty)
}

// verifyJWT checks the signature against the JWKS (kid match) and the issuer.
// Expiry is deliberately not enforced: receipts are verified long after the
// attestation token's lifetime. Freshness comes from the nonce instead.
func verifyJWT(token string, set *JWKS, issuer string) (jwt.MapClaims, error) {
	keys := map[string]any{}
	var any0 any
	for _, raw := range set.Keys {
		var k jwk
		if err := json.Unmarshal(raw, &k); err != nil {
			continue
		}
		pk, err := k.publicKey()
		if err != nil {
			continue
		}
		keys[k.Kid] = pk
		if any0 == nil {
			any0 = pk
		}
	}
	claims := jwt.MapClaims{}
	_, err := jwt.ParseWithClaims(token, claims, func(t *jwt.Token) (any, error) {
		kid, _ := t.Header["kid"].(string)
		if k, ok := keys[kid]; ok {
			return k, nil
		}
		if kid == "" && len(keys) == 1 {
			return any0, nil
		}
		return nil, fmt.Errorf("no key %q in pinned JWKS", kid)
	}, jwt.WithValidMethods([]string{"RS256", "RS384", "ES256", "ES384", "PS256"}), jwt.WithoutClaimsValidation())
	if err != nil {
		return nil, err
	}
	if issuer != "" {
		if iss, _ := claims["iss"].(string); iss != issuer {
			return nil, fmt.Errorf("issuer %q, policy wants %q", iss, issuer)
		}
	}
	return claims, nil
}

// claim reads a dot-separated path from nested claims.
func claim(c map[string]any, path string) (any, bool) {
	var cur any = c
	for _, p := range strings.Split(path, ".") {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, false
		}
		cur, ok = m[p]
		if !ok {
			return nil, false
		}
	}
	return cur, true
}

func claimString(c map[string]any, path string) (string, error) {
	v, ok := claim(c, path)
	if !ok {
		return "", fmt.Errorf("claim %s missing", path)
	}
	switch t := v.(type) {
	case string:
		return t, nil
	case bool:
		return fmt.Sprint(t), nil
	case float64:
		return fmt.Sprint(t), nil
	}
	return "", fmt.Errorf("claim %s is %T", path, v)
}

// claimStrings reads a claim that may be a string or an array of strings
// (EAT nonce claims may be either).
func claimStrings(c map[string]any, path string) ([]string, error) {
	v, ok := claim(c, path)
	if !ok {
		return nil, fmt.Errorf("claim %s missing", path)
	}
	switch t := v.(type) {
	case string:
		return []string{t}, nil
	case []any:
		out := make([]string, 0, len(t))
		for _, x := range t {
			s, ok := x.(string)
			if !ok {
				return nil, fmt.Errorf("claim %s has a %T element", path, x)
			}
			out = append(out, s)
		}
		return out, nil
	}
	return nil, fmt.Errorf("claim %s is %T", path, v)
}

var errNoMatch = errors.New("nonce mismatch")
