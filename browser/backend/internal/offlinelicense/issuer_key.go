package offlinelicense

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func GenerateIssuerKeyFile(path string) (string, error) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", fmt.Errorf("generate issuer key: %w", err)
	}
	protected, err := protectData(privateKey)
	if err != nil {
		return "", fmt.Errorf("protect issuer key: %w", err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return "", err
	}
	if err := os.WriteFile(path, protected, 0o600); err != nil {
		return "", fmt.Errorf("write issuer key: %w", err)
	}
	return base64.RawStdEncoding.EncodeToString(publicKey), nil
}

func LoadIssuerPrivateKey(path string) (ed25519.PrivateKey, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read issuer key: %w", err)
	}
	unprotected, err := unprotectData(raw)
	if err != nil {
		return nil, fmt.Errorf("unprotect issuer key: %w", err)
	}
	if len(unprotected) != ed25519.PrivateKeySize {
		return nil, fmt.Errorf("issuer key file is invalid")
	}
	return ed25519.PrivateKey(append([]byte(nil), unprotected...)), nil
}

func DecodeIssuerPublicKey(value string) (ed25519.PublicKey, error) {
	raw, err := base64.RawStdEncoding.DecodeString(strings.TrimSpace(value))
	if err != nil || len(raw) != ed25519.PublicKeySize {
		return nil, fmt.Errorf("issuer public key is invalid")
	}
	return ed25519.PublicKey(raw), nil
}
