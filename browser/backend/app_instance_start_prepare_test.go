package backend

import (
	"ant-chrome/backend/internal/browser"
	"testing"
)

func TestRunningProfileFingerprintExpectedArgsIgnoreExtraLaunchArgs(t *testing.T) {
	app := NewApp(t.TempDir())
	app.browserMgr = browser.NewManager(nil, app.appRoot)
	profile := &browser.Profile{
		ProfileId: "profile-running",
		Running:   true,
		LastLaunchArgs: []string{
			"--fingerprint=123",
			"--lang=zh-CN",
		},
	}

	expectedArgs := app.fingerprintCheckExpectedArgsForRunningProfile(profile, []string{"--timezone=Asia/Tokyo"})
	actual := buildBrowserFingerprintExpected(expectedArgs)
	if actual.Language != "zh-CN" {
		t.Fatalf("language = %q, want zh-CN", actual.Language)
	}
	if actual.Timezone != "" {
		t.Fatalf("timezone = %q, want no extra launch arg in running profile expected args", actual.Timezone)
	}
}

func TestBuildBrowserLaunchArgsAlwaysInjectsRuntimeFingerprintProtection(t *testing.T) {
	args := buildBrowserLaunchArgs(
		"profile-data",
		9222,
		"direct://",
		nil,
		[]string{"--fingerprint=123"},
		nil,
		nil,
		nil,
	)

	for _, expected := range []string{
		"--disable-non-proxied-udp",
		"--fingerprinting-canvas-image-data-noise",
		"--fingerprinting-client-rects-noise",
	} {
		found := false
		for _, arg := range args {
			if arg == expected {
				found = true
				break
			}
		}
		if !found {
			t.Fatalf("launch args missing %q: %#v", expected, args)
		}
	}
}
