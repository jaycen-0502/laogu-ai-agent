package offlinelicense

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type remoteCheckRequest struct {
	ActivationCode   string `json:"activation_code"`
	DeviceID         string `json:"device_id"`
	InstallPublicKey string `json:"install_public_key"`
	AppVersion       string `json:"app_version"`
}

type remoteCheckResponse struct {
	OK               bool     `json:"ok"`
	State            string   `json:"state"`
	Reason           string   `json:"reason"`
	LicenseID        string   `json:"license_id"`
	Customer         string   `json:"customer"`
	IssuedAt         string   `json:"issued_at"`
	ExpiresAt        string   `json:"expires_at"`
	Features         []string `json:"features"`
	OfflineGraceDays int      `json:"offline_grace_days"`
	ServerTime       string   `json:"server_time"`
}

// RemoteCheck performs one optional online authorization check.  It never
// persists the activation code; only the last successful check timestamp is
// stored in the existing protected local license state.
func (m *Manager) RemoteCheck(serverURL string) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	serverURL, serverURLError := normalizeRemoteServerURL(serverURL)
	if serverURL == "" {
		m.status = m.evaluate(time.Now())
		return m.status, fmt.Errorf("remote license server URL is empty")
	}
	if serverURLError != nil {
		return m.offlineStatus(time.Now(), serverURLError)
	}
	if strings.TrimSpace(m.state.ActivationCode) == "" {
		m.status = m.evaluate(time.Now())
		return m.status, fmt.Errorf("license is not activated")
	}
	payload, err := VerifyActivation(m.state.ActivationCode, m.issuerPublicKey)
	if err != nil {
		m.status = m.evaluate(time.Now())
		return m.status, err
	}
	body, err := json.Marshal(remoteCheckRequest{
		ActivationCode:   m.state.ActivationCode,
		DeviceID:         m.state.DeviceID,
		InstallPublicKey: m.state.InstallPublicKey,
		AppVersion:       m.appVersion,
	})
	if err != nil {
		return m.status, err
	}
	request, err := http.NewRequest(http.MethodPost, serverURL+"/api/license/check", bytes.NewReader(body))
	if err != nil {
		return m.status, err
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	response, err := (&http.Client{Timeout: 10 * time.Second}).Do(request)
	if err != nil {
		return m.offlineStatus(time.Now(), err)
	}
	defer response.Body.Close()
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return m.offlineStatus(time.Now(), err)
	}
	var result remoteCheckResponse
	decodeErr := json.Unmarshal(responseBody, &result)
	if state, reason, denied := remoteDenialForHTTP(response.StatusCode); denied {
		return m.remoteDenied(time.Now(), state, reason), nil
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return m.offlineStatus(time.Now(), fmt.Errorf("remote license check returned HTTP %d", response.StatusCode))
	}
	if decodeErr != nil {
		return m.offlineStatus(time.Now(), decodeErr)
	}
	if result.LicenseID != "" && result.LicenseID != payload.LicenseID {
		return m.remoteDenied(time.Now(), "IDENTITY_MISMATCH", "license_identity_mismatch"), nil
	}
	if result.OK && strings.EqualFold(result.State, "VALID") {
		m.state.LastOnlineCheckAt = time.Now().UTC().Format(time.RFC3339)
		if result.OfflineGraceDays >= 3 && result.OfflineGraceDays <= 30 {
			m.state.OfflineGraceDays = result.OfflineGraceDays
		}
		if err := m.save(); err != nil {
			return m.status, err
		}
		m.status = m.evaluate(time.Now())
		m.status.RemoteState = "VALID"
		m.status.LastOnlineCheckAt = m.state.LastOnlineCheckAt
		m.status.OfflineGraceDays = m.state.OfflineGraceDays
		m.remoteOverride = nil
		return m.status, nil
	}
	// A signed response from the server is authoritative for revocation and
	// expiry; do not apply the offline grace period to it.
	state := strings.TrimSpace(result.State)
	if state == "" {
		state = "DENIED"
	}
	reason := strings.TrimSpace(result.Reason)
	if reason == "" {
		reason = "denied"
	}
	return m.remoteDenied(time.Now(), state, reason), nil
}

func normalizeRemoteServerURL(raw string) (string, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return "", nil
	}
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Hostname() == "" {
		return raw, fmt.Errorf("remote license server URL is invalid")
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return raw, fmt.Errorf("remote license server URL must not contain credentials, query parameters, or fragments")
	}
	if !strings.EqualFold(parsed.Scheme, "https") {
		ip := net.ParseIP(parsed.Hostname())
		isLoopback := strings.EqualFold(parsed.Hostname(), "localhost") || (ip != nil && ip.IsLoopback())
		if !strings.EqualFold(parsed.Scheme, "http") || !isLoopback {
			return raw, fmt.Errorf("remote license server URL must use HTTPS")
		}
	}
	parsed.Path = strings.TrimRight(parsed.Path, "/")
	return strings.TrimRight(parsed.String(), "/"), nil
}

func remoteDenialForHTTP(statusCode int) (string, string, bool) {
	switch statusCode {
	case http.StatusBadRequest:
		return "INVALID", "invalid_request", true
	case http.StatusUnauthorized:
		return "UNAUTHORIZED", "unauthorized", true
	case http.StatusForbidden:
		return "DEVICE_MISMATCH", "device_mismatch", true
	case http.StatusNotFound:
		return "NOT_REGISTERED", "not_registered", true
	case http.StatusConflict:
		return "CONFLICT", "conflict", true
	case http.StatusUnprocessableEntity:
		return "INVALID", "invalid_activation", true
	default:
		return "", "", false
	}
}

func (m *Manager) remoteDenied(current time.Time, state string, reason string) Status {
	normalizedState := strings.ToUpper(strings.TrimSpace(state))
	m.status = m.evaluate(current)
	m.status.Licensed = false
	m.status.State = strings.ToLower(normalizedState)
	m.status.RemoteState = normalizedState
	m.status.Message = remoteDenialMessage(normalizedState, reason)
	override := m.status
	m.remoteOverride = &override
	return m.status
}

func remoteDenialMessage(state string, reason string) string {
	switch state {
	case "REVOKED":
		return "远程授权已撤销"
	case "EXPIRED":
		return "远程授权已过期"
	case "DEVICE_MISMATCH":
		return "远程授权与当前设备不匹配"
	case "NOT_REGISTERED":
		return "远程授权尚未在服务器登记"
	case "INVALID":
		return "远程授权无效"
	case "IDENTITY_MISMATCH":
		return "远程授权编号校验失败"
	case "UNAUTHORIZED":
		return "授权服务器拒绝了检查请求"
	default:
		return "远程授权检查未通过: " + strings.TrimSpace(reason)
	}
}

func (m *Manager) offlineStatus(current time.Time, remoteErr error) (Status, error) {
	m.status = m.evaluate(current)
	m.status.RemoteState = "UNAVAILABLE"
	m.status.LastOnlineCheckAt = m.state.LastOnlineCheckAt
	if !m.status.Licensed {
		override := m.status
		m.remoteOverride = &override
		return m.status, remoteErr
	}
	graceDays := m.status.OfflineGraceDays
	if graceDays < 3 || graceDays > 30 {
		graceDays = 7
	}
	last, parseErr := time.Parse(time.RFC3339, m.state.LastOnlineCheckAt)
	if parseErr != nil || last.IsZero() {
		m.status.Licensed = false
		m.status.State = "remote_unverified"
		m.status.Message = "Remote license has not completed its first online check"
		override := m.status
		m.remoteOverride = &override
		return m.status, remoteErr
	}
	if current.UTC().After(last.Add(time.Duration(graceDays) * 24 * time.Hour)) {
		m.status.Licensed = false
		m.status.State = "remote_grace_expired"
		m.status.Message = "Remote license offline grace period expired"
		override := m.status
		m.remoteOverride = &override
		return m.status, remoteErr
	}
	m.status.State = "licensed_offline"
	m.status.Licensed = true
	m.status.Message = "Licensed with remote check temporarily unavailable"
	override := m.status
	m.remoteOverride = &override
	return m.status, remoteErr
}
