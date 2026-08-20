package backend

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"ant-chrome/backend/internal/browser"
	"ant-chrome/backend/internal/config"
	"ant-chrome/backend/internal/logger"
)

var startBrowserWindowProcess = func(chromeBinaryPath string, args []string) (*exec.Cmd, error) {
	cmd := exec.Command(chromeBinaryPath, args...)
	cmd.Dir = filepath.Dir(chromeBinaryPath)
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return cmd, nil
}

func tryCloseBrowserViaCDP(debugPort int, timeout time.Duration) bool {
	if debugPort <= 0 || !canConnectDebugPort(debugPort, 250*time.Millisecond) {
		return false
	}

	_ = cdpBrowserCall(debugPort, "Browser.close", nil)

	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	ticker := time.NewTicker(150 * time.Millisecond)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return false
		case <-ticker.C:
			if !canConnectDebugPort(debugPort, 250*time.Millisecond) {
				return true
			}
		}
	}
}

func normalizeNonEmptyStrings(items []string) []string {
	if len(items) == 0 {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		value := strings.TrimSpace(item)
		if value != "" {
			out = append(out, value)
		}
	}
	return out
}

func ensureNewWindowLaunchArg(args []string) []string {
	for _, arg := range args {
		if strings.EqualFold(strings.TrimSpace(arg), "--new-window") {
			return args
		}
	}
	return append(args, "--new-window")
}

func browserDefaultStartURLs(cfg *config.Config) []string {
	if cfg != nil && cfg.Browser.DefaultStartURLs != nil {
		return normalizeNonEmptyStrings(cfg.Browser.DefaultStartURLs)
	}
	return config.DefaultBrowserStartURLs()
}

func (a *App) browserDefaultStartURLs() []string {
	return mergeStartURLs(browserDefaultStartURLs(a.config), bookmarkStartURLs(a.BookmarkList()))
}

func bookmarkStartURLs(bookmarks []BrowserBookmark) []string {
	if len(bookmarks) == 0 {
		return nil
	}
	urls := make([]string, 0, len(bookmarks))
	for _, bookmark := range bookmarks {
		if bookmark.OpenOnStart {
			urls = append(urls, bookmark.URL)
		}
	}
	return normalizeNonEmptyStrings(urls)
}

func mergeStartURLs(groups ...[]string) []string {
	seen := make(map[string]struct{})
	out := []string{}
	for _, group := range groups {
		for _, item := range normalizeNonEmptyStrings(group) {
			key := strings.ToLower(item)
			if _, ok := seen[key]; ok {
				continue
			}
			seen[key] = struct{}{}
			out = append(out, item)
		}
	}
	return out
}

func browserRestoreLastSession(cfg *config.Config) bool {
	if cfg == nil {
		return false
	}
	return cfg.Browser.RestoreLastSession
}

func appendLaunchTargets(args []string, startURLs []string, defaultStartURLs []string, skipDefaultStartURLs bool, restoreLastSession bool) []string {
	launchTargets, _ := buildBrowserLaunchTargets(startURLs, defaultStartURLs, skipDefaultStartURLs, restoreLastSession, false)
	return browser.BuildLaunchArgs(args, launchTargets)
}

func (a *App) markProfileStoppedLocked(profileId string, profile *BrowserProfile) {
	if profile == nil {
		return
	}
	profile.Running = false
	profile.DebugReady = false
	profile.Pid = 0
	profile.DebugPort = 0
	profile.RuntimeWarning = ""
	profile.LastStopAt = time.Now().Format(time.RFC3339)
	delete(a.browserMgr.BrowserProcesses, profileId)
	a.clearDeferredStartTargets(profileId)
	a.releaseProfileProxyBridge(profileId)
	if a.launchServer != nil {
		a.launchServer.ClearActiveProfile(profileId)
	}
}

// AddProfileWithRegionPreset 提供给前端调用的“一键添加美国/日本独立环境”接口
// region 参数可传 "US" 或 "JP"
func (a *App) AddProfileWithRegionPreset(profileName string, region string) (*browser.Profile, error) {
	regionUpper := strings.ToUpper(strings.TrimSpace(region))
	if regionUpper != "US" && regionUpper != "JP" {
		return nil, fmt.Errorf("不支持的地区类型，仅支持 'US' 或 'JP'")
	}

	// 1. 调用指纹模块生成独一无二的随机物理指纹与环境 Flag
	presetArgs, err := browser.GeneratePresetLaunchArgs(regionUpper)
	if err != nil {
		return nil, fmt.Errorf("生成地区指纹参数失败: %w", err)
	}

	// 2. 补全默认 ProfileName
	if strings.TrimSpace(profileName) == "" {
		profileName = fmt.Sprintf("%s 隔离环境-%d", regionUpper, time.Now().Unix()%1000)
	}

	// 3. 构造全新的 Profile 实例
	profileID := fmt.Sprintf("profile_%d", time.Now().UnixMilli())
	newProfile := &browser.Profile{
		ProfileId:   profileID,
		ProfileName: profileName,
		UserDataDir: profileID,
		LaunchArgs:  presetArgs,
		Running:     false,
		CreatedAt:   time.Now().Format("2006-01-02 15:04:05"),
		UpdatedAt:   time.Now().Format("2006-01-02 15:04:05"),
	}

	// 4. 安全存入 Manager 的内存 Map 与 DAO 持久化
	if a.browserMgr != nil {
		a.browserMgr.Mutex.Lock()
		if a.browserMgr.Profiles == nil {
			a.browserMgr.Profiles = make(map[string]*browser.Profile)
		}
		a.browserMgr.Profiles[profileID] = newProfile
		a.browserMgr.Mutex.Unlock()

		// 如果项目配置了 SQLite DAO 层，进行持久化落地
		if a.browserMgr.ProfileDAO != nil {
			_ = a.browserMgr.ProfileDAO.Upsert(newProfile)
		}
	}

	return newProfile, nil
}

func (a *App) openBrowserWindowForRunningProfile(profile *BrowserProfile, extraLaunchArgs []string, startURLs []string) error {
	if profile == nil {
		return errors.New("profile 不能为 nil")
	}

	chromeBinaryPath, err := a.browserMgr.ResolveChromeBinary(profile)
	if err != nil {
		return fmt.Errorf("无法解析 Chrome 二进制路径：%w", err)
	}

	userDataDir := a.browserMgr.ResolveUserDataDir(profile)
	if err := os.MkdirAll(userDataDir, 0755); err != nil {
		return fmt.Errorf("无法创建用户数据目录 %s：%w", userDataDir, err)
	}

	args := []string{
		fmt.Sprintf("--user-data-dir=%s", userDataDir),
		// === 关键抗封锁 Flag 汇总 ===
		"--disable-blink-features=AutomationControlled", // 隐藏 Chromium 原生的 navigator.webdriver = true 标记
		"--excludeSwitches=enable-automation",           // 隐藏顶部的“正受自动测试软件控制”警告条
		"--disable-infobars",                            // 禁用信息栏通知
		"--no-first-run",                                // 跳过 Chrome 首次运行引导页
		"--no-default-browser-check",                    // 跳过默认浏览器提示
		"--password-store=basic",                        // 避免不同环境凭据冲突
	}

	// 注入 Profile 本身保存的 LaunchArgs（如一键生成时写入的语言、时区、随机 CPU/内存与 GPS 坐标等）
	if len(profile.LaunchArgs) > 0 {
		args = append(args, profile.LaunchArgs...)
	}

	sanitizedExtraLaunchArgs, managedExtraArgs := sanitizeManagedLaunchArgs(extraLaunchArgs)
	logManagedLaunchArgOverrides(logger.New("Browser"), profile.ProfileId, "running-window.extraLaunchArgs", managedExtraArgs)
	args = append(args, sanitizedExtraLaunchArgs...)

	// === 保底机制：确保 Canvas Noise / ClientRects Noise / WebRTC UDP 屏蔽 100% 被注入 ===
	args = browser.EnsureRuntimeFingerprintArgs(args)

	if len(startURLs) > 0 {
		args = append(args, startURLs...)
	} else {
		args = append(args, "about:blank")
	}

	cmd, err := startBrowserWindowProcess(chromeBinaryPath, args)
	if err != nil {
		return fmt.Errorf("启动 Chrome 进程失败：%s", describeChromeProcessStartError(chromeBinaryPath, err))
	}

	if cmd != nil {
		go func() {
			_ = cmd.Wait()
		}()
	}
	return nil
}

func (a *App) openBrowserTabForRunningProfile(profile *BrowserProfile, extraLaunchArgs []string, startURLs []string) error {
	explicitTargets := normalizeNonEmptyStrings(startURLs)
	targets := explicitTargets
	if len(targets) == 0 {
		targets = []string{"about:blank"}
	}

	if profile != nil && profile.DebugReady && profile.DebugPort > 0 {
		var lastCDPErr error
		for _, target := range targets {
			if err := createBrowserStartTarget(profile.DebugPort, target); err != nil {
				lastCDPErr = err
			}
		}

		if lastCDPErr == nil {
			return nil
		}

		if len(explicitTargets) == 0 && len(normalizeNonEmptyStrings(extraLaunchArgs)) == 0 {
			return nil
		}
	}

	err := a.openBrowserWindowForRunningProfile(profile, extraLaunchArgs, targets)
	if err != nil && len(explicitTargets) == 0 && len(normalizeNonEmptyStrings(extraLaunchArgs)) == 0 {
		return nil
	}
	return err
}
