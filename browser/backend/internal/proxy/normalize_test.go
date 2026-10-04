package proxy

import (
	"ant-chrome/backend/internal/config"
	"testing"
)

func TestNormalizeBrowserProxiesTrimsAndKeepsUserList(t *testing.T) {
	proxies := NormalizeBrowserProxies([]config.BrowserProxy{
		{ProxyName: "  main  ", ProxyConfig: " http://127.0.0.1:8080 ", DnsServers: " 1.1.1.1 ", GroupName: " group-a "},
		{ProxyName: "missing config"},
	}, func() string { return "generated-id" })

	if len(proxies) != 1 {
		t.Fatalf("len = %d, want 1", len(proxies))
	}
	if proxies[0].ProxyId != "generated-id" {
		t.Fatalf("generated proxy id = %q", proxies[0].ProxyId)
	}
	if proxies[0].ProxyName != "main" || proxies[0].ProxyConfig != "http://127.0.0.1:8080" {
		t.Fatalf("proxy was not trimmed: %#v", proxies[0])
	}
	if proxies[0].DnsServers != "1.1.1.1" || proxies[0].GroupName != "group-a" {
		t.Fatalf("metadata was not trimmed: %#v", proxies[0])
	}
}

func TestNormalizeBrowserProxiesSourceRefreshRules(t *testing.T) {
	proxies := NormalizeBrowserProxies([]config.BrowserProxy{
		{
			ProxyId:                "p1",
			ProxyName:              "source",
			ProxyConfig:            "http://127.0.0.1:8080",
			SourceID:               " source-id ",
			SourceURL:              " https://example.com/proxies.txt ",
			SourceNamePrefix:       " prefix ",
			SourceAutoRefresh:      true,
			SourceRefreshIntervalM: -1,
			SourceLastRefreshAt:    " now ",
		},
		{
			ProxyId:                "p2",
			ProxyName:              "without-source",
			ProxyConfig:            "http://127.0.0.1:8081",
			SourceID:               "source-id",
			SourceNamePrefix:       "prefix",
			SourceAutoRefresh:      true,
			SourceRefreshIntervalM: 9999,
			SourceLastRefreshAt:    "now",
		},
	}, nil)

	if proxies[0].SourceRefreshIntervalM != defaultSourceRefreshIntervalM {
		t.Fatalf("source refresh interval = %d", proxies[0].SourceRefreshIntervalM)
	}
	if !proxies[0].SourceAutoRefresh {
		t.Fatalf("source auto refresh should stay enabled")
	}
	if proxies[1].SourceID != "" || proxies[1].SourceAutoRefresh || proxies[1].SourceRefreshIntervalM != 0 {
		t.Fatalf("source fields should be cleared without source url: %#v", proxies[1])
	}
}

func TestNormalizeBrowserProxiesKeepsExplicitDirect(t *testing.T) {
	proxies := NormalizeBrowserProxies([]config.BrowserProxy{
		{ProxyId: "__direct__", ProxyName: "direct", ProxyConfig: "direct://"},
	}, nil)

	if len(proxies) != 1 {
		t.Fatalf("len = %d, want 1", len(proxies))
	}
}

func TestNormalizeBrowserProxiesKeepsPreferredKernel(t *testing.T) {
	proxies := NormalizeBrowserProxies([]config.BrowserProxy{
		{ProxyId: "p1", ProxyName: "main", ProxyConfig: "http://127.0.0.1:8080", PreferredKernel: " singbox "},
	}, nil)

	if proxies[0].PreferredKernel != ProxyKernelSingBox {
		t.Fatalf("preferred kernel = %q, want sing-box", proxies[0].PreferredKernel)
	}
}
