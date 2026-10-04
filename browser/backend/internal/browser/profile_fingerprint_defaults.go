package browser

import "strings"

// effectiveRuntimeFingerprintArgs 定义了默认运行参数。噪声项使用显式 0，
// 允许用户在 Profile 参数中明确选择开启时覆盖默认值。
var effectiveRuntimeFingerprintArgs = []string{
	"--disable-non-proxied-udp",                          // 彻底防止 WebRTC 泄露真实局域网/公网 IP
	"--webrtc-ip-handling-policy=disable_non_proxied_udp", // 现代 Chromium WebRTC 策略（双重防护）
	"--disable-quic",                                     // 禁用 HTTP/3 (QUIC) UDP 协议，强制所有流量走 TCP 代理隧道，杜绝 UDP 旁路泄露公网真实 IP
	"--proxy-bypass-list=<-loopback>",                    // 强制所有外部公网流量走代理，仅放行本地回环
	"--fingerprinting-canvas-image-data-noise=0",         // 默认关闭 Canvas 噪声
	"--fingerprinting-client-rects-noise=0",              // 默认关闭 ClientRects 噪声
	"--window-size=1280,800",                             // 默认最佳桌面拓客视口，保证推特完整三栏与悬浮名片正常工作
}

// EnsureRuntimeFingerprintArgs 补齐默认参数，但尊重用户对噪声开关的明确选择。
func EnsureRuntimeFingerprintArgs(args []string) []string {
	out := append([]string{}, args...)

	// 强制补充所有的核心指纹噪音与防泄露参数
	for _, defaultArg := range effectiveRuntimeFingerprintArgs {
		if !fingerprintArgPresent(out, defaultArg) {
			out = append(out, defaultArg)
		}
	}
	return out
}

func upgradeLegacyMinimalFingerprintArgs(args []string) []string {
	if !isLegacyMinimalFingerprintArgs(args) {
		return append([]string{}, args...)
	}
	return EnsureRuntimeFingerprintArgs(args)
}

func isLegacyMinimalFingerprintArgs(args []string) bool {
	if len(args) != 2 {
		return false
	}
	hasBrand := false
	hasPlatform := false
	for _, arg := range args {
		trimmed := strings.TrimSpace(arg)
		if strings.HasPrefix(trimmed, "--fingerprint-brand=") {
			hasBrand = true
		}
		if strings.HasPrefix(trimmed, "--fingerprint-platform=") {
			hasPlatform = true
		}
	}
	return hasBrand && hasPlatform
}

func fingerprintArgContains(args []string, expected string) bool {
	for _, arg := range args {
		if strings.TrimSpace(arg) == expected {
			return true
		}
	}
	return false
}

func fingerprintArgPresent(args []string, expected string) bool {
	key := strings.SplitN(strings.TrimSpace(expected), "=", 2)[0]
	for _, arg := range args {
		trimmed := strings.TrimSpace(arg)
		if trimmed == key || strings.HasPrefix(trimmed, key+"=") {
			return true
		}
	}
	return false
}
