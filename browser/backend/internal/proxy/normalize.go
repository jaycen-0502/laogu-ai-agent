package proxy

import (
	"ant-chrome/backend/internal/config"
	"strings"
)

const defaultSourceRefreshIntervalM = 60
const maxSourceRefreshIntervalM = 24 * 60

func NormalizeBrowserProxies(proxies []config.BrowserProxy, generateID func() string) []config.BrowserProxy {
	normalized := make([]config.BrowserProxy, 0, len(proxies))
	for i, item := range proxies {
		proxyName := strings.TrimSpace(item.ProxyName)
		proxyConfig := strings.TrimSpace(item.ProxyConfig)
		if proxyName == "" || proxyConfig == "" {
			continue
		}

		proxyID := strings.TrimSpace(item.ProxyId)
		if proxyID == "" && generateID != nil {
			proxyID = generateID()
		}

		sourceURL := strings.TrimSpace(item.SourceURL)
		sourceID := strings.TrimSpace(item.SourceID)
		sourceNamePrefix := strings.TrimSpace(item.SourceNamePrefix)
		sourceLastRefreshAt := strings.TrimSpace(item.SourceLastRefreshAt)
		sourceRefreshIntervalM := item.SourceRefreshIntervalM
		if sourceRefreshIntervalM < 0 {
			sourceRefreshIntervalM = 0
		}
		if sourceRefreshIntervalM > maxSourceRefreshIntervalM {
			sourceRefreshIntervalM = maxSourceRefreshIntervalM
		}

		sourceAutoRefresh := item.SourceAutoRefresh && sourceURL != ""
		if sourceAutoRefresh && sourceRefreshIntervalM <= 0 {
			sourceRefreshIntervalM = defaultSourceRefreshIntervalM
		}
		if !sourceAutoRefresh {
			sourceRefreshIntervalM = 0
		}
		if sourceURL == "" {
			sourceID = ""
			sourceNamePrefix = ""
			sourceLastRefreshAt = ""
			sourceAutoRefresh = false
			sourceRefreshIntervalM = 0
		}

		normalized = append(normalized, config.BrowserProxy{
			ProxyId:                proxyID,
			ProxyName:              proxyName,
			ProxyConfig:            proxyConfig,
			PreferredKernel:        NormalizePreferredKernel(item.PreferredKernel),
			DnsServers:             strings.TrimSpace(item.DnsServers),
			GroupName:              strings.TrimSpace(item.GroupName),
			SourceID:               sourceID,
			SourceURL:              sourceURL,
			SourceNamePrefix:       sourceNamePrefix,
			SourceAutoRefresh:      sourceAutoRefresh,
			SourceRefreshIntervalM: sourceRefreshIntervalM,
			SourceLastRefreshAt:    sourceLastRefreshAt,
			SortOrder:              i,
		})
	}

	// 直连现在是可管理的普通代理项：允许用户删除，也允许通过代理导入面板再次添加。
	// 不在规范化阶段强制补回 __direct__，否则删除操作无法持久化。
	return normalized
}
