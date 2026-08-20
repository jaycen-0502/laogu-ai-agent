package offlinelicense

import (
	"crypto/ed25519"
	"encoding/base64"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestPythonIssuerInteroperability(t *testing.T) {
	if os.Getenv("LAOGU_TEST_PYTHON_INTEROP") != "1" {
		t.Skip("set LAOGU_TEST_PYTHON_INTEROP=1 to run the Python interoperability test")
	}

	python, err := exec.LookPath("python")
	if err != nil {
		t.Fatal(err)
	}
	root, err := filepath.Abs(filepath.Join("..", "..", ".."))
	if err != nil {
		t.Fatal(err)
	}
	script := filepath.Join(root, "tools", "license_server", "license_admin.py")
	temp := t.TempDir()
	keyPath := filepath.Join(temp, "issuer.pem")
	passwordPath := filepath.Join(temp, "password.txt")
	ledgerPath := filepath.Join(temp, "ledger.json")

	initOutput, err := exec.Command(
		python, script,
		"--key", keyPath,
		"--password-file", passwordPath,
		"init",
	).CombinedOutput()
	if err != nil {
		t.Fatalf("python init failed: %v\n%s", err, initOutput)
	}
	var publicText string
	for _, line := range strings.Split(string(initOutput), "\n") {
		if value, found := strings.CutPrefix(strings.TrimSpace(line), "客户端公钥："); found {
			publicText = value
			break
		}
	}
	publicKey, err := base64.RawStdEncoding.DecodeString(publicText)
	if err != nil || len(publicKey) == 0 {
		t.Fatalf("cannot read Python public key from output: %q", initOutput)
	}

	requestCode, err := EncodeRequest(RequestPayload{
		Version:          1,
		DeviceID:         "interop-device",
		InstallPublicKey: "interop-install-public-key",
		Nonce:            "interop-nonce",
		AppVersion:       "test",
		RequestedAt:      time.Now().UTC().Format(time.RFC3339),
	})
	if err != nil {
		t.Fatal(err)
	}
	issueOutput, err := exec.Command(
		python, script,
		"--key", keyPath,
		"--password-file", passwordPath,
		"--ledger", ledgerPath,
		"issue",
		"--request", requestCode,
		"--days", "7",
		"--customer", "互操作测试",
		"--license", "INTEROP-001",
	).CombinedOutput()
	if err != nil {
		t.Fatalf("python issue failed: %v\n%s", err, issueOutput)
	}
	var activationCode string
	for _, line := range strings.Split(string(issueOutput), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, ActivationPrefix) {
			activationCode = line
			break
		}
	}
	payload, err := VerifyActivation(activationCode, ed25519.PublicKey(publicKey))
	if err != nil {
		t.Fatalf("Go failed to verify Python activation: %v\n%s", err, issueOutput)
	}
	if payload.DeviceID != "interop-device" || payload.RequestNonce != "interop-nonce" {
		t.Fatalf("unexpected activation payload: %+v", payload)
	}
}
