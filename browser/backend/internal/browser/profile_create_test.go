package browser

import (
	"ant-chrome/backend/internal/config"
	"testing"
)

func TestCreateAppliesDefaultFingerprintArgsWhenInputIsEmpty(t *testing.T) {
	manager := NewManager(&config.Config{}, t.TempDir())
	manager.Config.Browser.DefaultFingerprintArgs = []string{
		"--fingerprint-brand=Chrome",
		"--fingerprint-platform=windows",
		"--disable-non-proxied-udp",
		"--fingerprinting-canvas-image-data-noise=0",
		"--fingerprinting-client-rects-noise=0",
	}

	profile, err := manager.Create(ProfileInput{ProfileName: "test"})
	if err != nil {
		t.Fatalf("Create returned error: %v", err)
	}

	assertStringSliceContains(t, profile.FingerprintArgs, "--disable-non-proxied-udp")
	assertStringSliceContains(t, profile.FingerprintArgs, "--fingerprinting-canvas-image-data-noise=0")
	assertStringSliceContains(t, profile.FingerprintArgs, "--fingerprinting-client-rects-noise=0")
}

func TestCreateKeepsExplicitFingerprintArgs(t *testing.T) {
	manager := NewManager(&config.Config{}, t.TempDir())
	manager.Config.Browser.DefaultFingerprintArgs = []string{"--fingerprint-brand=Chrome"}

	profile, err := manager.Create(ProfileInput{
		ProfileName:     "test",
		FingerprintArgs: []string{"--fingerprint=123"},
	})
	if err != nil {
		t.Fatalf("Create returned error: %v", err)
	}

	if got, want := len(profile.FingerprintArgs), 1; got != want {
		t.Fatalf("fingerprint args length = %d, want %d: %#v", got, want, profile.FingerprintArgs)
	}
	assertStringSliceContains(t, profile.FingerprintArgs, "--fingerprint=123")
}

func assertStringSliceContains(t *testing.T, values []string, expected string) {
	t.Helper()
	for _, value := range values {
		if value == expected {
			return
		}
	}
	t.Fatalf("values %#v missing %q", values, expected)
}

func TestDifferentiateProfileFingerprintArgsGeneratesDistinctSeedsAndConcurrency(t *testing.T) {
	base := []string{
		"--fingerprint-brand=Chrome",
		"--fingerprint-platform=windows",
	}

	p1 := differentiateProfileFingerprintArgs("profile-alpha-1001", base)
	p2 := differentiateProfileFingerprintArgs("profile-beta-2002", base)

	var seed1, seed2 string
	for _, arg := range p1 {
		if len(arg) > 14 && arg[:14] == "--fingerprint=" {
			seed1 = arg
		}
	}
	for _, arg := range p2 {
		if len(arg) > 14 && arg[:14] == "--fingerprint=" {
			seed2 = arg
		}
	}

	if seed1 == "" || seed2 == "" {
		t.Fatalf("expected both profiles to have --fingerprint, got %q and %q", seed1, seed2)
	}
	if seed1 == seed2 {
		t.Fatalf("expected different seeds for different profile IDs, got %q == %q", seed1, seed2)
	}
}

func TestGeneratePresetLaunchArgsUsesStandardTimezoneAndLanguage(t *testing.T) {
	argsUS, err := GeneratePresetLaunchArgs("US")
	if err != nil {
		t.Fatalf("GeneratePresetLaunchArgs(US) failed: %v", err)
	}

	hasStandardTimezone := false
	hasInvalidTz := false
	hasValidLang := false
	hasAntiAutomation := false

	for _, arg := range argsUS {
		if arg == "--timezone=America/New_York" {
			hasStandardTimezone = true
		}
		if len(arg) >= 5 && arg[:5] == "--tz=" {
			hasInvalidTz = true
		}
		if arg == "--lang=en-US" {
			hasValidLang = true
		}
		if arg == "--excludeSwitches=enable-automation" {
			hasAntiAutomation = true
		}
	}

	if !hasStandardTimezone {
		t.Fatalf("expected --timezone=America/New_York, got args: %#v", argsUS)
	}
	if hasInvalidTz {
		t.Fatalf("found invalid --tz flag in args: %#v", argsUS)
	}
	if !hasValidLang {
		t.Fatalf("expected valid RFC 5646 --lang=en-US, got args: %#v", argsUS)
	}
	if !hasAntiAutomation {
		t.Fatalf("expected --excludeSwitches=enable-automation in args: %#v", argsUS)
	}
}

