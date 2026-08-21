package offlinelicense

import (
	"crypto/ed25519"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/google/uuid"
)

var rawBase64 = base64.RawURLEncoding

func EncodeRequest(payload RequestPayload) (string, error) {
	if err := validateRequest(payload); err != nil {
		return "", err
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return "", fmt.Errorf("encode request payload: %w", err)
	}
	return RequestPrefix + rawBase64.EncodeToString(raw), nil
}

func DecodeRequest(code string) (RequestPayload, error) {
	code = strings.TrimSpace(code)
	if !strings.HasPrefix(code, RequestPrefix) {
		return RequestPayload{}, fmt.Errorf("invalid request code prefix")
	}
	raw, err := rawBase64.DecodeString(strings.TrimPrefix(code, RequestPrefix))
	if err != nil {
		return RequestPayload{}, fmt.Errorf("decode request code: %w", err)
	}
	var payload RequestPayload
	if err := json.Unmarshal(raw, &payload); err != nil {
		return RequestPayload{}, fmt.Errorf("parse request code: %w", err)
	}
	if err := validateRequest(payload); err != nil {
		return RequestPayload{}, err
	}
	return payload, nil
}

func IssueActivation(requestCode string, privateKey ed25519.PrivateKey, options IssueOptions, now time.Time) (string, ActivationPayload, error) {
	request, err := DecodeRequest(requestCode)
	if err != nil {
		return "", ActivationPayload{}, err
	}
	if len(privateKey) != ed25519.PrivateKeySize {
		return "", ActivationPayload{}, fmt.Errorf("invalid issuer private key")
	}
	days := options.Days
	if days <= 0 || days > 3650 {
		return "", ActivationPayload{}, fmt.Errorf("days must be between 1 and 3650")
	}
	licenseID := strings.TrimSpace(options.LicenseID)
	if licenseID == "" {
		licenseID = "lic_" + strings.ReplaceAll(uuid.NewString(), "-", "")[:16]
	}
	payload := ActivationPayload{
		Version:          1,
		LicenseID:        licenseID,
		Customer:         strings.TrimSpace(options.Customer),
		DeviceID:         request.DeviceID,
		InstallPublicKey: request.InstallPublicKey,
		RequestNonce:     request.Nonce,
		IssuedAt:         now.UTC().Format(time.RFC3339),
		ExpiresAt:        now.UTC().Add(time.Duration(days) * 24 * time.Hour).Format(time.RFC3339),
		Features:         normalizeFeatures(options.Features),
	}
	raw, err := json.Marshal(payload)
	if err != nil {
		return "", ActivationPayload{}, fmt.Errorf("encode activation payload: %w", err)
	}
	signature := ed25519.Sign(privateKey, raw)
	code := ActivationPrefix + rawBase64.EncodeToString(raw) + "." + rawBase64.EncodeToString(signature)
	return code, payload, nil
}

func VerifyActivation(code string, publicKey ed25519.PublicKey) (ActivationPayload, error) {
	code = strings.TrimSpace(code)
	if !strings.HasPrefix(code, ActivationPrefix) {
		return ActivationPayload{}, fmt.Errorf("invalid activation code prefix")
	}
	parts := strings.Split(strings.TrimPrefix(code, ActivationPrefix), ".")
	if len(parts) != 2 {
		return ActivationPayload{}, fmt.Errorf("invalid activation code format")
	}
	raw, err := rawBase64.DecodeString(parts[0])
	if err != nil {
		return ActivationPayload{}, fmt.Errorf("decode activation payload: %w", err)
	}
	signature, err := rawBase64.DecodeString(parts[1])
	if err != nil {
		return ActivationPayload{}, fmt.Errorf("decode activation signature: %w", err)
	}
	if len(publicKey) != ed25519.PublicKeySize || !ed25519.Verify(publicKey, raw, signature) {
		return ActivationPayload{}, fmt.Errorf("activation signature is invalid")
	}
	var payload ActivationPayload
	if err := json.Unmarshal(raw, &payload); err != nil {
		return ActivationPayload{}, fmt.Errorf("parse activation payload: %w", err)
	}
	if err := validateActivation(payload); err != nil {
		return ActivationPayload{}, err
	}
	return payload, nil
}

func validateRequest(payload RequestPayload) error {
	if payload.Version != 1 || strings.TrimSpace(payload.DeviceID) == "" || strings.TrimSpace(payload.InstallPublicKey) == "" || strings.TrimSpace(payload.Nonce) == "" {
		return fmt.Errorf("request code is incomplete")
	}
	if _, err := time.Parse(time.RFC3339, payload.RequestedAt); err != nil {
		return fmt.Errorf("request time is invalid")
	}
	return nil
}

func validateActivation(payload ActivationPayload) error {
	if payload.Version != 1 || strings.TrimSpace(payload.LicenseID) == "" || strings.TrimSpace(payload.DeviceID) == "" || strings.TrimSpace(payload.InstallPublicKey) == "" || strings.TrimSpace(payload.RequestNonce) == "" {
		return fmt.Errorf("activation payload is incomplete")
	}
	issuedAt, err := time.Parse(time.RFC3339, payload.IssuedAt)
	if err != nil {
		return fmt.Errorf("activation issue time is invalid")
	}
	expiresAt, err := time.Parse(time.RFC3339, payload.ExpiresAt)
	if err != nil || !expiresAt.After(issuedAt) {
		return fmt.Errorf("activation expiry time is invalid")
	}
	return nil
}

func normalizeFeatures(values []string) []string {
	if len(values) == 0 {
		return []string{"browser", "playwright", "external_api"}
	}
	seen := map[string]struct{}{}
	result := make([]string, 0, len(values))
	for _, value := range values {
		value = strings.ToLower(strings.TrimSpace(value))
		if value == "" {
			continue
		}
		if _, exists := seen[value]; exists {
			continue
		}
		seen[value] = struct{}{}
		result = append(result, value)
	}
	return result
}
