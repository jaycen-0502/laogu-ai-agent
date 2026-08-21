package offlinelicense

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func newActivatedRemoteManager(t *testing.T) *Manager {
	t.Helper()
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	manager, err := NewManager(t.TempDir(), "1.4.0", base64.RawStdEncoding.EncodeToString(publicKey))
	if err != nil {
		t.Fatal(err)
	}
	code, _, err := IssueActivation(manager.Status().RequestCode, privateKey, IssueOptions{LicenseID: "lic-remote", Days: 30}, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Activate(code); err != nil {
		t.Fatal(err)
	}
	return manager
}

func TestRemoteCheckRecordsOnlineTimestampAndHonorsDenial(t *testing.T) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	manager, err := NewManager(t.TempDir(), "1.4.0", base64.RawStdEncoding.EncodeToString(publicKey))
	if err != nil {
		t.Fatal(err)
	}
	initial := manager.Status()
	code, _, err := IssueActivation(initial.RequestCode, privateKey, IssueOptions{LicenseID: "lic-remote", Days: 30}, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := manager.Activate(code); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		var request remoteCheckRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil || request.ActivationCode == "" || request.DeviceID == "" {
			t.Errorf("unexpected remote request: %#v", request)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"state":"VALID","license_id":"lic-remote","offline_grace_days":3}`))
	}))
	defer server.Close()
	status, err := manager.RemoteCheck(server.URL)
	if err != nil || !status.Licensed || status.RemoteState != "VALID" || status.OfflineGraceDays != 3 || status.LastOnlineCheckAt == "" {
		t.Fatalf("unexpected remote status: %#v, err=%v", status, err)
	}
	server.Config.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":false,"state":"REVOKED","reason":"revoked","license_id":"lic-remote"}`))
	})
	denied, err := manager.RemoteCheck(server.URL)
	if err != nil || denied.Licensed || denied.State != "revoked" {
		t.Fatalf("remote denial was not applied: %#v, err=%v", denied, err)
	}
}

func TestRemoteCheckTreatsClientErrorsAsAuthoritativeDenials(t *testing.T) {
	tests := []struct {
		statusCode int
		wantState  string
	}{
		{http.StatusForbidden, "device_mismatch"},
		{http.StatusNotFound, "not_registered"},
		{http.StatusUnprocessableEntity, "invalid"},
	}
	for _, test := range tests {
		t.Run(http.StatusText(test.statusCode), func(t *testing.T) {
			manager := newActivatedRemoteManager(t)
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(test.statusCode)
				_, _ = w.Write([]byte(`{"detail":"denied"}`))
			}))
			defer server.Close()
			status, err := manager.RemoteCheck(server.URL)
			if err != nil || status.Licensed || status.State != test.wantState {
				t.Fatalf("unexpected denial status: %#v, err=%v", status, err)
			}
		})
	}
}

func TestRemoteCheckUsesGraceForTemporaryFailures(t *testing.T) {
	manager := newActivatedRemoteManager(t)
	statusCode := http.StatusOK
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(statusCode)
		if statusCode == http.StatusOK {
			_, _ = w.Write([]byte(`{"ok":true,"state":"VALID","license_id":"lic-remote","offline_grace_days":7}`))
			return
		}
		_, _ = w.Write([]byte(`{"detail":"temporarily unavailable"}`))
	}))
	defer server.Close()
	if status, err := manager.RemoteCheck(server.URL); err != nil || !status.Licensed {
		t.Fatalf("initial remote check failed: %#v, err=%v", status, err)
	}
	for _, temporaryStatus := range []int{http.StatusTooManyRequests, http.StatusInternalServerError} {
		statusCode = temporaryStatus
		status, err := manager.RemoteCheck(server.URL)
		if err == nil || !status.Licensed || status.State != "licensed_offline" || status.RemoteState != "UNAVAILABLE" {
			t.Fatalf("HTTP %d did not use offline grace: %#v, err=%v", temporaryStatus, status, err)
		}
	}
}

func TestRemoteCheckRequiresFirstSuccessAndExpiresGrace(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"detail":"temporarily unavailable"}`))
	}))
	defer server.Close()

	manager := newActivatedRemoteManager(t)
	status, err := manager.RemoteCheck(server.URL)
	if err == nil || status.Licensed || status.State != "remote_unverified" {
		t.Fatalf("first online check should be required: %#v, err=%v", status, err)
	}

	manager = newActivatedRemoteManager(t)
	manager.state.LastOnlineCheckAt = time.Now().UTC().Add(-8 * 24 * time.Hour).Format(time.RFC3339)
	manager.state.OfflineGraceDays = 7
	if err := manager.save(); err != nil {
		t.Fatal(err)
	}
	status, err = manager.RemoteCheck(server.URL)
	if err == nil || status.Licensed || status.State != "remote_grace_expired" {
		t.Fatalf("expired grace should deny access: %#v, err=%v", status, err)
	}
}

func TestRemoteCheckRejectsLicenseIdentityMismatch(t *testing.T) {
	manager := newActivatedRemoteManager(t)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"ok":true,"state":"VALID","license_id":"different-license"}`))
	}))
	defer server.Close()
	status, err := manager.RemoteCheck(server.URL)
	if err != nil || status.Licensed || status.State != "identity_mismatch" {
		t.Fatalf("identity mismatch was not denied: %#v, err=%v", status, err)
	}
}

func TestRemoteCheckRequiresHTTPSOutsideLoopback(t *testing.T) {
	manager := newActivatedRemoteManager(t)
	status, err := manager.RemoteCheck("http://example.com")
	if err == nil || status.Licensed || status.State != "remote_unverified" {
		t.Fatalf("insecure remote URL was not rejected: %#v, err=%v", status, err)
	}
	if _, err := normalizeRemoteServerURL("http://127.0.0.1:8000"); err != nil {
		t.Fatalf("loopback development URL should remain available: %v", err)
	}
	if normalized, err := normalizeRemoteServerURL("https://api.example.com/"); err != nil || normalized != "https://api.example.com" {
		t.Fatalf("HTTPS URL was not normalized: %q, err=%v", normalized, err)
	}
}
