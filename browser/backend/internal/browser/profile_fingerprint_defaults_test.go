package browser

import (
	"strings"
	"testing"
)

func TestUpgradeLegacyMinimalFingerprintArgsAddsEffectiveRuntimeArgs(t *testing.T) {
	args := upgradeLegacyMinimalFingerprintArgs([]string{"--fingerprint-brand=Chrome", "--fingerprint-platform=windows"})

	assertStringSliceContains(t, args, "--disable-non-proxied-udp")
	assertStringSliceContains(t, args, "--fingerprinting-canvas-image-data-noise=0")
	assertStringSliceContains(t, args, "--fingerprinting-client-rects-noise=0")
}

func TestEnsureRuntimeFingerprintArgsRespectsExplicitNoiseEnablement(t *testing.T) {
	args := EnsureRuntimeFingerprintArgs([]string{
		"--fingerprint-brand=Chrome",
		"--fingerprint-platform=windows",
		"--fingerprinting-canvas-image-data-noise",
		"--fingerprinting-client-rects-noise",
	})
	assertStringSliceContains(t, args, "--fingerprinting-canvas-image-data-noise")
	assertStringSliceContains(t, args, "--fingerprinting-client-rects-noise")
	if got := countFingerprintArgKey(args, "--fingerprinting-canvas-image-data-noise"); got != 1 {
		t.Fatalf("canvas noise flag count = %d, want 1: %#v", got, args)
	}
	if got := countFingerprintArgKey(args, "--fingerprinting-client-rects-noise"); got != 1 {
		t.Fatalf("client rects noise flag count = %d, want 1: %#v", got, args)
	}
}

func countFingerprintArgKey(args []string, key string) int {
	count := 0
	for _, arg := range args {
		if arg == key || strings.HasPrefix(arg, key+"=") {
			count++
		}
	}
	return count
}

func TestUpgradeLegacyMinimalFingerprintArgsKeepsCustomArgs(t *testing.T) {
	args := upgradeLegacyMinimalFingerprintArgs([]string{"--fingerprint=123", "--fingerprint-brand=Chrome"})

	if got, want := len(args), 2; got != want {
		t.Fatalf("fingerprint args length = %d, want %d: %#v", got, want, args)
	}
	assertStringSliceContains(t, args, "--fingerprint=123")
	assertStringSliceContains(t, args, "--fingerprint-brand=Chrome")
}
