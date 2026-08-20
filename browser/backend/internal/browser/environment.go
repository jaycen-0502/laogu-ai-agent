package browser

import (
	"path/filepath"
	"strings"

	"ant-chrome/backend/internal/logger"
)

// GetProxyConfigById 根据代理 ID 获取代理配置
func (m *Manager) GetProxyConfigById(proxyId string) (string, bool) {
	if proxy, ok := m.GetProxyByID(proxyId); ok {
		return strings.TrimSpace(proxy.ProxyConfig), true
	}
	return "", false
}

// ResolveUserDataDir 解析用户数据目录（强化防空、彻底隔离版）
func (m *Manager) ResolveUserDataDir(profile *Profile) string {
	if profile == nil {
		return filepath.Join(m.getResolvedUserDataRoot(), "profile_fallback_nil")
	}

	userDataDir := strings.TrimSpace(profile.UserDataDir)
	if userDataDir == "" {
		userDataDir = strings.TrimSpace(profile.ProfileId)
	}

	// 核心加固：如果 UserDataDir 和 ProfileId 均为空，强制生成独立目录名，防止直接退化到 root
	if userDataDir == "" {
		userDataDir = "profile_unknown_default"
	}

	// 绝对路径直接使用
	if filepath.IsAbs(userDataDir) {
		return userDataDir
	}

	// 相对路径拼接在 UserDataRoot 下
	return filepath.Join(m.getResolvedUserDataRoot(), userDataDir)
}

func (m *Manager) getResolvedUserDataRoot() string {
	root := strings.TrimSpace(m.Config.Browser.UserDataRoot)
	if root == "" {
		root = "data"
	}
	return m.ResolveRelativePath(root)
}

// MigrateConfig 迁移旧配置到新格式
func (m *Manager) MigrateConfig() bool {
	log := logger.New("Browser")

	if len(m.Config.Browser.Environments) > 0 && len(m.Config.Browser.Cores) == 0 {
		log.Info("检测到旧配置格式，开始迁移")

		for _, env := range m.Config.Browser.Environments {
			m.Config.Browser.Cores = append(m.Config.Browser.Cores, Core{
				CoreId:    env.CoreId,
				CoreName:  env.CoreName,
				CorePath:  env.CorePath,
				IsDefault: env.IsDefault,
			})
		}

		m.Config.Browser.Environments = nil
		m.Config.Browser.ChromeBinaryPath = ""
		m.Config.Browser.CoreRoot = ""
		m.Config.Browser.DefaultCoreId = ""
		m.Config.Browser.DefaultConnectorType = ""

		if err := m.Config.Save(m.ResolveRelativePath("config.yaml")); err != nil {
			log.Error("配置迁移保存失败", logger.F("error", err.Error()))
			return false
		}

		log.Info("配置迁移完成", logger.F("cores_count", len(m.Config.Browser.Cores)))
		return true
	}

	return false
}
