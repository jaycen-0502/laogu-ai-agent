package backend

import (
	"testing"

	"ant-chrome/backend/internal/config"
)

func TestVerifyAdminPasswordSupportsSHA256AndLegacyPlaintext(t *testing.T) {
	app := NewApp(t.TempDir())
	app.config = &config.Config{}

	app.config.App.AdminPassword = "sha256:06bae143bee6b008c30daf52180ebec48cf53586cc062b3aee015761d155ee03"
	if !app.VerifyAdminPassword("laogu88888888") {
		t.Fatal("expected SHA-256 password to verify")
	}
	if app.VerifyAdminPassword("wrong") {
		t.Fatal("wrong SHA-256 password must not verify")
	}

	app.config.App.AdminPassword = "legacy-password"
	if !app.VerifyAdminPassword("legacy-password") {
		t.Fatal("expected legacy plaintext password to verify")
	}
	if app.VerifyAdminPassword("") {
		t.Fatal("empty password must not verify")
	}
}
