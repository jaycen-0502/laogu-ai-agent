package proxy

import (
	"fmt"
	"net"
	"net/url"
	"strconv"
	"strings"
	"unicode"

	"gopkg.in/yaml.v3"
)

type DnsDiagnosticSummary struct {
	HasConfig       bool     `json:"hasConfig"`
	SourceFormat    string   `json:"sourceFormat"`
	EnhancedMode    string   `json:"enhancedMode"`
	NameserverCount int      `json:"nameserverCount"`
	FallbackCount   int      `json:"fallbackCount"`
	XrayServerCount int      `json:"xrayServerCount"`
	Unsupported     []string `json:"unsupported"`
}

// parseDnsConfig 解析 DNS 配置，支持两种格式：
// 1. Clash dns: YAML 块（含 nameserver/fallback 等字段）
// 2. 逗号分隔的 IP 列表（兼容旧格式）
// 返回 xray dns 配置 map，若无有效配置则返回 nil
//
// 注意：xray dns.servers 只支持纯 IP 或 DoH（https://）地址，
// 不支持 Clash 的 tls:// 格式（DoT），会被自动过滤。
func parseDnsConfig(raw string) map[string]interface{} {
	_, addresses, err := NormalizeXrayDNSInput(raw)
	if err != nil || len(addresses) == 0 {
		return nil
	}
	hasLocalhost := false
	for _, a := range addresses {
		if strings.EqualFold(a, "localhost") {
			hasLocalhost = true
			break
		}
	}
	servers := make([]interface{}, len(addresses))
	for i, s := range addresses {
		servers[i] = s
	}
	if !hasLocalhost {
		servers = append(servers, "localhost")
	}
	return withXrayDNSRefreshPolicy(map[string]interface{}{"servers": servers})
}

// NormalizeXrayDNSInput accepts either a Clash dns: YAML block or a simple
// address list separated by lines, spaces, commas (including Chinese commas),
// or semicolons. It validates eagerly so a typo cannot become a delayed Xray
// startup/network failure.
func NormalizeXrayDNSInput(raw string) (string, []string, error) {
	original := strings.TrimSpace(raw)
	if original == "" {
		return "", nil, nil
	}

	type clashDNS struct {
		Nameserver []string `yaml:"nameserver"`
		Fallback   []string `yaml:"fallback"`
	}
	type clashDNSWrapper struct {
		DNS *clashDNS `yaml:"dns"`
	}
	var wrapper clashDNSWrapper
	if err := yaml.Unmarshal([]byte(original), &wrapper); err == nil && wrapper.DNS != nil {
		addresses := append(append([]string{}, wrapper.DNS.Nameserver...), wrapper.DNS.Fallback...)
		validated, err := validateAndDedupeXrayDNSAddresses(addresses, original)
		if err != nil {
			return "", nil, err
		}
		if len(validated) == 0 {
			return "", nil, fmt.Errorf("DNS YAML 未包含 nameserver 或 fallback 地址")
		}
		return original, validated, nil
	}

	addresses := splitSimpleDNSInput(original)
	validated, err := validateAndDedupeXrayDNSAddresses(addresses, original)
	if err != nil {
		return "", nil, err
	}
	if len(validated) == 0 {
		return "", nil, fmt.Errorf("未识别到有效 DNS 地址")
	}
	return strings.Join(validated, "\n"), validated, nil
}

func splitSimpleDNSInput(raw string) []string {
	return strings.FieldsFunc(raw, func(r rune) bool {
		return unicode.IsSpace(r) || r == ',' || r == '，' || r == ';' || r == '；'
	})
}

func validateAndDedupeXrayDNSAddresses(addresses []string, original string) ([]string, error) {
	result := make([]string, 0, len(addresses))
	seen := make(map[string]struct{}, len(addresses))
	for _, address := range addresses {
		address = strings.TrimSpace(address)
		if address == "" {
			continue
		}
		if !isXrayDnsAddr(address) {
			return nil, fmt.Errorf("DNS 地址 %q 格式无效或 Xray 不支持（输入: %q）", address, original)
		}
		key := strings.ToLower(address)
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		result = append(result, address)
	}
	return result, nil
}

func withXrayDNSRefreshPolicy(config map[string]interface{}) map[string]interface{} {
	if config == nil {
		return nil
	}
	config["queryStrategy"] = "UseIP"
	config["disableCache"] = false
	config["serveStale"] = false
	return config
}

// isXrayDnsAddr 判断 DNS 地址是否为 xray 支持的格式。
// xray 支持：纯 IP（如 8.8.8.8）、IP:port（如 8.8.8.8:53）、
// DoH（https://...）、localhost。
// 不支持：Clash 的 tls:// 格式（DoT）。
func isXrayDnsAddr(s string) bool {
	s = strings.TrimSpace(s)
	if s == "" {
		return false
	}
	if strings.EqualFold(s, "localhost") {
		return true
	}
	if net.ParseIP(s) != nil {
		return true
	}
	lower := strings.ToLower(s)
	if strings.HasPrefix(lower, "https://") {
		parsed, err := url.Parse(s)
		return err == nil && parsed.Scheme == "https" && parsed.Hostname() != "" && parsed.Path != ""
	}
	if strings.Contains(s, ":") {
		host, portText, err := net.SplitHostPort(s)
		if err != nil {
			return false
		}
		port, err := strconv.Atoi(portText)
		return err == nil && port >= 1 && port <= 65535 && (net.ParseIP(host) != nil || strings.EqualFold(host, "localhost"))
	}
	return false
}

func buildDnsDiagnosticSummary(raw string) DnsDiagnosticSummary {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return DnsDiagnosticSummary{}
	}
	summary := DnsDiagnosticSummary{HasConfig: true, SourceFormat: "list"}
	type clashDns struct {
		Enable       bool     `yaml:"enable"`
		EnhancedMode string   `yaml:"enhanced-mode"`
		Nameserver   []string `yaml:"nameserver"`
		Fallback     []string `yaml:"fallback"`
	}
	type clashDnsWrapper struct {
		Dns clashDns `yaml:"dns"`
	}
	var wrapper clashDnsWrapper
	if err := yaml.Unmarshal([]byte(raw), &wrapper); err == nil && (len(wrapper.Dns.Nameserver) > 0 || len(wrapper.Dns.Fallback) > 0 || wrapper.Dns.EnhancedMode != "") {
		summary.SourceFormat = "clash"
		summary.EnhancedMode = strings.TrimSpace(wrapper.Dns.EnhancedMode)
		summary.NameserverCount = len(wrapper.Dns.Nameserver)
		summary.FallbackCount = len(wrapper.Dns.Fallback)
		for _, server := range append(append([]string{}, wrapper.Dns.Nameserver...), wrapper.Dns.Fallback...) {
			server = strings.TrimSpace(server)
			if server == "" {
				continue
			}
			if isXrayDnsAddr(server) {
				summary.XrayServerCount++
			} else {
				summary.Unsupported = append(summary.Unsupported, server)
			}
		}
		return summary
	}
	for _, server := range splitSimpleDNSInput(raw) {
		server = strings.TrimSpace(server)
		if server == "" {
			continue
		}
		summary.NameserverCount++
		if isXrayDnsAddr(server) {
			summary.XrayServerCount++
		} else {
			summary.Unsupported = append(summary.Unsupported, server)
		}
	}
	return summary
}
