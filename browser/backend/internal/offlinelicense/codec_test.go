package offlinelicense

import (
	"crypto/ed25519"
	"crypto/rand"
	"strings"
	"testing"
	"time"
)

func TestIssueAndVerifyActivation(t *testing.T) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Date(2026, 8, 14, 10, 0, 0, 0, time.UTC)
	requestCode, err := EncodeRequest(RequestPayload{
		Version:          1,
		DeviceID:         "device-a",
		InstallPublicKey: "install-public-key",
		Nonce:            "nonce-a",
		AppVersion:       "1.4.0",
		RequestedAt:      now.Format(time.RFC3339),
	})
	if err != nil {
		t.Fatal(err)
	}
	activationCode, issued, err := IssueActivation(requestCode, privateKey, IssueOptions{
		Customer: "customer-a",
		Days:     7,
	}, now)
	if err != nil {
		t.Fatal(err)
	}
	verified, err := VerifyActivation(activationCode, publicKey)
	if err != nil {
		t.Fatal(err)
	}
	if verified.DeviceID != "device-a" || verified.RequestNonce != "nonce-a" {
		t.Fatalf("verified payload mismatch: %#v", verified)
	}
	if verified.ExpiresAt != now.Add(7*24*time.Hour).Format(time.RFC3339) {
		t.Fatalf("expiresAt = %s", verified.ExpiresAt)
	}
	if issued.LicenseID == "" {
		t.Fatal("license id is empty")
	}
}

func TestActivationTamperingIsRejected(t *testing.T) {
	publicKey, privateKey, _ := ed25519.GenerateKey(rand.Reader)
	now := time.Now().UTC()
	requestCode, _ := EncodeRequest(RequestPayload{
		Version: 1, DeviceID: "device-a", InstallPublicKey: "key-a", Nonce: "nonce-a",
		RequestedAt: now.Format(time.RFC3339),
	})
	activationCode, _, err := IssueActivation(requestCode, privateKey, IssueOptions{Days: 7}, now)
	if err != nil {
		t.Fatal(err)
	}
	tampered := strings.Replace(activationCode, "LGACT1.", "LGACT1.A", 1)
	if _, err := VerifyActivation(tampered, publicKey); err == nil {
		t.Fatal("tampered activation code was accepted")
	}
}
