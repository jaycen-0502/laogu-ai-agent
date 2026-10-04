package backend

import (
	"ant-chrome/backend/internal/config"
	"ant-chrome/backend/internal/logger"
	"ant-chrome/backend/internal/proxy"
	"fmt"
	"strings"
)

func (a *App) SaveBrowserProxies(proxies []BrowserProxy) error {
	log := logger.New("Browser")
	normalized := proxy.NormalizeBrowserProxies(proxies, generateUUID)
	for index := range normalized {
		if strings.TrimSpace(normalized[index].DnsServers) == "" {
			continue
		}
		canonical, _, err := proxy.NormalizeXrayDNSInput(normalized[index].DnsServers)
		if err != nil {
			name := strings.TrimSpace(normalized[index].ProxyName)
			if name == "" {
				name = normalized[index].ProxyId
			}
			return fmt.Errorf("代理 %q 的 DNS 配置无效: %w", name, err)
		}
		normalized[index].DnsServers = canonical
	}

	a.config.Browser.Proxies = normalized

	if a.browserMgr.ProxyDAO != nil {
		if err := a.browserMgr.ProxyDAO.DeleteAll(); err != nil {
			log.Error("清空代理表失败", logger.F("error", err))
			return err
		}
		for _, item := range normalized {
			if err := a.browserMgr.ProxyDAO.Upsert(item); err != nil {
				log.Error("代理保存失败", logger.F("proxy_id", item.ProxyId), logger.F("error", err))
				return err
			}
		}
		log.Info("代理列表已保存到数据库", logger.F("count", len(normalized)))
		a.reconcileProfileProxyBindings()
		return nil
	}

	if err := config.SaveProxies(a.resolveAppPath("proxies.yaml"), normalized); err != nil {
		log.Error("代理列表保存失败", logger.F("error", err))
		return err
	}
	a.reconcileProfileProxyBindings()
	return nil
}
