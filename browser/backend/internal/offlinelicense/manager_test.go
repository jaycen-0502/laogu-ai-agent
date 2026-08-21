package offlinelicense

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"testing"
	"time"
)

func TestManagerActivationPersistsAndRequestIsOneTime(t *testing.T) {
	publicKey, privateKey, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	publicText := base64.RawStdEncoding.EncodeToString(publicKey)
	root := t.TempDir()
	manager, err := NewManager(root, "1.4.0", publicText)
	if err != nil {
		t.Fatal(err)
	}
	initial := manager.Status()
	if initial.Licensed || initial.RequestCode == "" {
		t.Fatalf("unexpected initial status: %#v", initial)
	}
	code, _, err := IssueActivation(initial.RequestCode, privateKey, IssueOptions{Days: 7}, time.Now().UTC())
	if err != nil {
		t.Fatal(err)
	}
	activated, err := manager.Activate(code)
	if err != nil {
		t.Fatal(err)
	}
	if !activated.Licensed || activated.RemainingDays < 6 {
		t.Fatalf("unexpected activated status: %#v", activated)
	}
	if _, err := manager.Activate(code); err == nil {
		t.Fatal("the same activation response was accepted twice")
	}

	reloaded, err := NewManager(root, "1.4.0", publicText)
	if err != nil {
		t.Fatal(err)
	}
	if status := reloaded.Status(); !status.Licensed {
		t.Fatalf("persisted activation is not licensed: %#v", status)
	}
}
