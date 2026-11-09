package evidence

import (
	"crypto/ecdsa"
	"crypto/rsa"
	"crypto/x509"
)

func marshalRSA(k *rsa.PrivateKey) []byte { return x509.MarshalPKCS1PrivateKey(k) }

func parseRSA(b []byte) (*rsa.PrivateKey, error) { return x509.ParsePKCS1PrivateKey(b) }

func marshalEC(k *ecdsa.PrivateKey) ([]byte, error) { return x509.MarshalECPrivateKey(k) }

func parseEC(b []byte) (*ecdsa.PrivateKey, error) { return x509.ParseECPrivateKey(b) }
