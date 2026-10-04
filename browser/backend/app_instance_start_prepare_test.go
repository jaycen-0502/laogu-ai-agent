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
		"--webrtc-ip-handling-policy=disable_non_proxied_udp",
		"--fingerprinting-canvas-image-data-noise=0",
		"--fingerprinting-client-rects-noise=0",
		"--excludeSwitches=enable-automation",
		"--hide-crash-restore-bubble",
		"--disable-background-networking",
		"--js-flags=--max-old-space-size=4096",
		"--disable-dev-shm-usage",
		"--disable-gpu-memory-buffer-video-frames",
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

func TestAppendEffectiveProxyLaunchArg(t *testing.T) {
	tests := []struct {
		name           string
		effectiveProxy string
		want           string
	}{
		{name: "direct", effectiveProxy: "direct://", want: "--no-proxy-server"},
		{name: "bridge", effectiveProxy: "socks5://127.0.0.1:53030", want: "--proxy-server=socks5://127.0.0.1:53030"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			args := appendEffectiveProxyLaunchArg([]string{"--user-data-dir=profile"}, tt.effectiveProxy)
			if len(args) != 2 || args[1] != tt.want {
				t.Fatalf("args = %#v, want proxy argument %q", args, tt.want)
			}
		})
	}
}

func TestResolveProxyLocaleForProfile(t *testing.T) {
	app := NewApp(t.TempDir())
	app.browserMgr = browser.NewManager(nil, app.appRoot)

	// Direct proxy or empty proxy yields empty locale
	directProfile := &browser.Profile{ProfileId: "p1", ProxyId: "__direct__"}
	tz, lang := app.resolveProxyLocaleForProfile(directProfile, newBrowserStartInput("p1", nil, nil, false, false, false, "", ""))
	if tz != "" || lang != "" {
		t.Fatalf("expected empty for direct proxy, got tz=%q lang=%q", tz, lang)
	}

	// Inferred from text
	if code := inferCountryCodeFromText("日本东京高速专线"); code != "JP" {
		t.Fatalf("inferCountryCodeFromText('日本东京高速专线') = %q, want JP", code)
	}
	if code := inferCountryCodeFromText("US-California-Premium"); code != "US" {
		t.Fatalf("inferCountryCodeFromText('US-California-Premium') = %q, want US", code)
	}
	if code := inferCountryCodeFromText("us-clone"); code != "US" {
		t.Fatalf("inferCountryCodeFromText('us-clone') = %q, want US", code)
	}
	if code := inferCountryCodeFromText("香港中继节点-HK"); code != "HK" {
		t.Fatalf("inferCountryCodeFromText('香港中继节点-HK') = %q, want HK", code)
	}
	if code := inferCountryCodeFromText("台湾台北-TW"); code != "TW" {
		t.Fatalf("inferCountryCodeFromText('台湾台北-TW') = %q, want TW", code)
	}

	// 🛡️ 严格验证：含有 network: tcp、client-fingerprint 等关键字绝不可被误判为 TW 或 FR/CA
	if code := inferCountryCodeFromText("network: tcp\nclient-fingerprint: chrome"); code != "" {
		t.Fatalf("inferCountryCodeFromText('network: tcp...') = %q, want empty (no false positive TW)", code)
	}

	jpOpt := resolveProxyLocationOption("JP", "", "")
	if jpOpt.Timezone != "Asia/Tokyo" || jpOpt.Lang != "ja-JP" {
		t.Fatalf("JP option = %#v, want Asia/Tokyo / ja-JP", jpOpt)
	}

	usOpt := resolveProxyLocationOption("US", "", "los angeles")
	if usOpt.Timezone != "America/Los_Angeles" || usOpt.Lang != "en-US" {
		t.Fatalf("US LA option = %#v, want America/Los_Angeles / en-US", usOpt)
	}
}
