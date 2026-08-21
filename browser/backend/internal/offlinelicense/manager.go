package offlinelicense

import (
	"crypto/ed25519"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const rollbackTolerance = 10 * time.Minute

type Manager struct {
	mu              sync.Mutex
	statePath       string
	appVersion      string
	issuerPublicKey ed25519.PublicKey
	state           stateFile
	status          Status
	remoteOverride  *Status
}

func NewManager(stateRoot, appVersion, issuerPublicKey string) (*Manager, error) {
	publicKey, err := DecodeIssuerPublicKey(issuerPublicKey)
	if err != nil {
		return nil, err
	}
	m := &Manager{
		statePath:       filepath.Join(stateRoot, "data", "license-state.bin"),
		appVersion:      strings.TrimSpace(appVersion),
		issuerPublicKey: publicKey,
	}
	if err := m.loadOrCreate(); err != nil {
		return nil, err
	}
	m.status = m.evaluate(time.Now())
	return m, nil
}

func (m *Manager) Status() Status {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.remoteOverride != nil {
		return *m.remoteOverride
	}
	m.status = m.evaluate(time.Now())
	return m.status
}

func (m *Manager) RequestCode(regenerate bool) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if regenerate || strings.TrimSpace(m.state.PendingRequest) == "" {
		if err := m.regenerateRequest(time.Now()); err != nil {
			return Status{}, err
		}
	}
	m.status = m.evaluate(time.Now())
	return m.status, nil
}

func (m *Manager) Activate(code string) (Status, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	payload, err := VerifyActivation(code, m.issuerPublicKey)
	if err != nil {
		return m.status, err
	}
	if payload.DeviceID != m.state.DeviceID || payload.InstallPublicKey != m.state.InstallPublicKey {
		return m.status, fmt.Errorf("激活码不属于当前电脑")
	}
	if payload.RequestNonce != m.state.PendingNonce {
		return m.status, fmt.Errorf("激活码不匹配当前请求码，请使用最新请求码重新签发")
	}
	issuedAt, _ := time.Parse(time.RFC3339, payload.IssuedAt)
	if issuedAt.After(time.Now().UTC().Add(10 * time.Minute)) {
		return m.status, fmt.Errorf("激活码签发时间异常，请校准系统时间")
	}
	expiresAt, _ := time.Parse(time.RFC3339, payload.ExpiresAt)
	if !expiresAt.After(time.Now().UTC()) {
		return m.status, fmt.Errorf("激活码已经过期")
	}
	m.state.ActivationCode = strings.TrimSpace(code)
	m.remoteOverride = nil
	m.state.LastSeenAt = time.Now().UTC().Format(time.RFC3339)
	if err := m.regenerateRequest(time.Now()); err != nil {
		return m.status, err
	}
	m.status = m.evaluate(time.Now())
	return m.status, nil
}

func (m *Manager) loadOrCreate() error {
	raw, err := os.ReadFile(m.statePath)
	if err == nil {
		plain, decryptErr := unprotectData(raw)
		if decryptErr != nil {
			return fmt.Errorf("读取本机授权状态失败: %w", decryptErr)
		}
		if err := json.Unmarshal(plain, &m.state); err != nil {
			return fmt.Errorf("解析本机授权状态失败: %w", err)
		}
		if err := m.validateInstallIdentity(); err != nil {
			return err
		}
		return nil
	}
	if !os.IsNotExist(err) {
		return err
	}
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return err
	}
	fingerprint, err := machineFingerprint()
	if err != nil {
		return err
	}
	hash := sha256.Sum256([]byte(fingerprint + "|" + base64.RawStdEncoding.EncodeToString(publicKey)))
	m.state = stateFile{
		InstallPrivateKey: base64.RawStdEncoding.EncodeToString(privateKey),
		InstallPublicKey:  base64.RawStdEncoding.EncodeToString(publicKey),
		DeviceID:          hex.EncodeToString(hash[:]),
	}
	if err := m.regenerateRequest(time.Now()); err != nil {
		return err
	}
	return nil
}

func (m *Manager) validateInstallIdentity() error {
	privateKey, err := base64.RawStdEncoding.DecodeString(m.state.InstallPrivateKey)
	if err != nil || len(privateKey) != ed25519.PrivateKeySize {
		return fmt.Errorf("本机安装密钥无效")
	}
	publicKey := ed25519.PrivateKey(privateKey).Public().(ed25519.PublicKey)
	if base64.RawStdEncoding.EncodeToString(publicKey) != m.state.InstallPublicKey {
		return fmt.Errorf("本机安装密钥不匹配")
	}
	fingerprint, err := machineFingerprint()
	if err != nil {
		return err
	}
	hash := sha256.Sum256([]byte(fingerprint + "|" + m.state.InstallPublicKey))
	if hex.EncodeToString(hash[:]) != m.state.DeviceID {
		return fmt.Errorf("授权状态已从其他电脑复制，设备指纹不匹配")
	}
	return nil
}

func (m *Manager) regenerateRequest(now time.Time) error {
	nonceBytes := make([]byte, 18)
	if _, err := rand.Read(nonceBytes); err != nil {
		return err
	}
	m.state.PendingNonce = rawBase64.EncodeToString(nonceBytes)
	request, err := EncodeRequest(RequestPayload{
		Version:          1,
		DeviceID:         m.state.DeviceID,
		InstallPublicKey: m.state.InstallPublicKey,
		Nonce:            m.state.PendingNonce,
		AppVersion:       m.appVersion,
		RequestedAt:      now.UTC().Format(time.RFC3339),
	})
	if err != nil {
		return err
	}
	m.state.PendingRequest = request
	return m.save()
}

func (m *Manager) evaluate(now time.Time) Status {
	remoteState := "NOT_CONFIGURED"
	if strings.TrimSpace(m.state.LastOnlineCheckAt) != "" {
		remoteState = "VALID"
	}
	status := Status{
		Licensed:          false,
		State:             "unlicensed",
		Message:           "请输入对应当前电脑请求码生成的激活码",
		DeviceID:          m.state.DeviceID,
		RequestCode:       m.state.PendingRequest,
		Features:          []string{},
		RemoteState:       remoteState,
		LastOnlineCheckAt: m.state.LastOnlineCheckAt,
		OfflineGraceDays:  m.state.OfflineGraceDays,
	}
	if status.OfflineGraceDays < 3 || status.OfflineGraceDays > 30 {
		status.OfflineGraceDays = 7
	}
	lastSeenAt, _ := time.Parse(time.RFC3339, m.state.LastSeenAt)
	if !lastSeenAt.IsZero() && now.UTC().Before(lastSeenAt.Add(-rollbackTolerance)) {
		status.State = "clock_rollback"
		status.Message = "检测到系统时间明显回退，请校准时间后重新打开软件"
		status.ClockRollback = true
		return status
	}
	if strings.TrimSpace(m.state.ActivationCode) == "" {
		return status
	}
	payload, err := VerifyActivation(m.state.ActivationCode, m.issuerPublicKey)
	if err != nil {
		status.State = "invalid"
		status.Message = "本机授权文件无效，请重新申请激活码"
		return status
	}
	status.LicenseID = payload.LicenseID
	status.Customer = payload.Customer
	status.IssuedAt = payload.IssuedAt
	status.ExpiresAt = payload.ExpiresAt
	status.Features = append([]string(nil), payload.Features...)
	if payload.DeviceID != m.state.DeviceID || payload.InstallPublicKey != m.state.InstallPublicKey {
		status.State = "device_mismatch"
		status.Message = "授权不属于当前电脑"
		return status
	}
	expiresAt, err := time.Parse(time.RFC3339, payload.ExpiresAt)
	if err != nil || !expiresAt.After(now.UTC()) {
		status.State = "expired"
		status.Message = "授权已到期，请发送新的请求码续期"
		return status
	}
	status.Licensed = true
	status.State = "licensed"
	status.Message = "授权有效"
	remaining := expiresAt.Sub(now.UTC())
	status.RemainingDays = int((remaining + 24*time.Hour - time.Nanosecond) / (24 * time.Hour))
	m.state.LastSeenAt = now.UTC().Format(time.RFC3339)
	_ = m.save()
	return status
}

func (m *Manager) save() error {
	raw, err := json.Marshal(m.state)
	if err != nil {
		return err
	}
	protected, err := protectData(raw)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(m.statePath), 0o700); err != nil {
		return err
	}
	tempPath := m.statePath + ".tmp"
	if err := os.WriteFile(tempPath, protected, 0o600); err != nil {
		return err
	}
	if err := os.Rename(tempPath, m.statePath); err != nil {
		_ = os.Remove(m.statePath)
		if retryErr := os.Rename(tempPath, m.statePath); retryErr != nil {
			return retryErr
		}
	}
	return nil
}
