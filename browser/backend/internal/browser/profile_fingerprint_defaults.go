package browser

import "strings"

// effectiveRuntimeFingerprintArgs 定义了内核层面的核心物理指纹隔离 Flag
var effectiveRuntimeFingerprintArgs = []string{
	"--disable-non-proxied-udp",                // 彻底防止 WebRTC 泄露真实局域网/公网 IP
	"--fingerprinting-canvas-image-data-noise", // 内核级 Canvas 绘图微小噪音隔离
	"--fingerprinting-client-rects-noise",      // 内核级 字体/元素的 ClientRects 尺寸噪音隔离
}

// EnsureRuntimeFingerprintArgs 确保无论传入什么参数，核心指纹隔离 Flag 都被强制注入
func EnsureRuntimeFingerprintArgs(args []string) []string {
	out := append([]string{}, args...)

	// 强制补充所有的核心指纹噪音与防泄露参数
	for _, defaultArg := range effectiveRuntimeFingerprintArgs {
		if !fingerprintArgContains(out, defaultArg) {
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
