package backend

import (
	"ant-chrome/backend/internal/browser"
	"ant-chrome/backend/internal/logger"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type browserStartInput struct {
	ProfileID            string
	ExtraLaunchArgs      []string
	StartURLs            []string
	SkipDefaultStartURLs bool
	PreferVisibleWindow  bool
	ForceDirectProxy     bool
	TemporaryProxyID     string
	TemporaryProxyConfig string
}

type browserStartPlan struct {
	profile              *BrowserProfile
	chromeBinaryPath     string
	userDataDir          string
	args                 []string
	extensionDirs        []string
	deferredStartTargets []string
	effectiveProxy       string
	acquiredProxyBridge  profileProxyBridgeRef
	releaseProxyBridge   bool
	assignedDebugPort    int
	startReadyTimeout    time.Duration
	startStableWindow    time.Duration
	maxStartAttempts     int
	totalReadyTimeout    time.Duration
}

var clearBrowserSessionRestoreData = browser.ClearSessionRestoreData

func newBrowserStartInput(profileID string, extraLaunchArgs []string, startURLs []string, skipDefaultStartURLs bool, preferVisibleWindow bool, forceDirectProxy bool, proxyID string, proxyConfig string) browserStartInput {
	normalizedExtraLaunchArgs := normalizeNonEmptyStrings(extraLaunchArgs)

	return browserStartInput{
		ProfileID:            profileID,
		ExtraLaunchArgs:      normalizedExtraLaunchArgs,
		StartURLs:            normalizeNonEmptyStrings(startURLs),
		SkipDefaultStartURLs: skipDefaultStartURLs,
		PreferVisibleWindow:  preferVisibleWindow,
		ForceDirectProxy:     forceDirectProxy,
		TemporaryProxyID:     strings.TrimSpace(proxyID),
		TemporaryProxyConfig: strings.TrimSpace(proxyConfig),
	}
}

func (input browserStartInput) hasTemporaryProxy() bool {
	return strings.TrimSpace(input.TemporaryProxyID) != "" || strings.TrimSpace(input.TemporaryProxyConfig) != ""
}

func (plan *browserStartPlan) releaseBridgeIfNeeded(a *App) {
	if plan == nil || a == nil {
		return
	}
	if plan.releaseProxyBridge {
		a.releaseProxyBridgeRef(plan.acquiredProxyBridge)
	}
}

func (a *App) resolveBrowserStartProfile(input browserStartInput) (*BrowserProfile, bool, error) {
	log := logger.New("Browser")

	profile, exists := a.browserMgr.Profiles[input.ProfileID]
	if !exists {
		err := fmt.Errorf("实例启动失败：未找到实例配置（ID=%s）。请刷新列表后重试。", input.ProfileID)
		log.Error("实例不存在", logger.F("profile_id", input.ProfileID), logger.F("reason", err.Error()))
		return nil, false, err
	}
	a.ensureProfileLaunchCode(profile)

	if !profile.Running {
		return profile, false, nil
	}

	if !isBrowserProfileLive(profile, a.browserMgr.BrowserProcesses[input.ProfileID]) {
		log.Info("检测到实例运行状态已失效，准备重新启动",
			logger.F("profile_id", input.ProfileID),
			logger.F("pid", profile.Pid),
			logger.F("debug_port", profile.DebugPort),
		)
		a.markProfileStoppedLocked(input.ProfileID, profile)
		return profile, false, nil
	}

	if len(normalizeNonEmptyStrings(input.StartURLs)) == 0 && len(normalizeNonEmptyStrings(input.ExtraLaunchArgs)) == 0 {
		if a.launchServer != nil && profile.DebugReady {
			a.launchServer.SetActiveProfile(profile)
		}
		a.emitBrowserInstanceStarted(profile, true)
		return profile, true, nil
	}

	fingerprintExpectedArgs := a.fingerprintCheckExpectedArgsForRunningProfile(profile, input.ExtraLaunchArgs)
	resolvedStartURLs := a.resolveFingerprintCheckStartURLsForExpectedArgsAndProfile(profile.ProfileId, fingerprintExpectedArgs, profile, input.StartURLs)
	if err := a.openBrowserTabForRunningProfile(profile, input.ExtraLaunchArgs, resolvedStartURLs); err != nil {
		startErr := fmt.Errorf("实例已在运行，但新标签打开失败：%w", err)
		log.Error("运行中实例新标签打开失败",
			logger.F("profile_id", input.ProfileID),
			logger.F("debug_port", profile.DebugPort),
			logger.F("error", err.Error()),
			logger.F("reason", startErr.Error()),
		)
		profile.LastError = startErr.Error()
		return profile, true, startErr
	}

	if a.launchServer != nil && profile.DebugReady {
		a.launchServer.SetActiveProfile(profile)
	}
	a.emitBrowserInstanceStarted(profile, true)
	return profile, true, nil
}

func (a *App) prepareBrowserStartPlan(input browserStartInput, profile *BrowserProfile) (*browserStartPlan, error) {
	bookmarks := a.BookmarkList()
	sanitizedProfileLaunchArgs, sanitizedExtraLaunchArgs, fingerprintLaunchArgs, chromeBinaryPath, userDataDir, err := a.prepareBrowserLaunchContext(input, profile, bookmarks)
	if err != nil {
		return nil, err
	}

	effectiveProxy, acquiredProxyBridge, releaseProxyBridge, err := a.resolveBrowserStartProxy(input, profile)
	if err != nil {
		return nil, err
	}

	startReadyTimeout, startStableWindow := a.browserStartTimingSettings()
	maxStartAttempts := browserStartAttemptCount()
	totalReadyTimeout := time.Duration(maxStartAttempts) * startReadyTimeout
	restoreLastSession := browserRestoreLastSession(a.config)
	extensionDirs := a.browserMgr.EnabledExtensionDirsForProfile(input.ProfileID)
	fingerprintExpectedArgs := combineFingerprintExpectedArgs(fingerprintLaunchArgs, sanitizedProfileLaunchArgs, sanitizedExtraLaunchArgs)
	defaultStartURLs := a.resolveFingerprintCheckStartURLsForExpectedArgsAndProfile(profile.ProfileId, fingerprintExpectedArgs, profile, mergeStartURLs(browserDefaultStartURLs(a.config), bookmarkStartURLs(bookmarks)))
	startURLs := a.resolveFingerprintCheckStartURLsForExpectedArgsAndProfile(profile.ProfileId, fingerprintExpectedArgs, profile, input.StartURLs)
	launchTargets, deferredStartTargets := buildBrowserLaunchTargets(
		startURLs,
		defaultStartURLs,
		input.SkipDefaultStartURLs,
		restoreLastSession,
		browserLightStartEnabled(a.config),
	)

	assignedDebugPort, err := nextAvailablePort()
	if err != nil {
		startErr := fmt.Errorf("实例启动失败：本地调试端口分配失败。原因：%v。请关闭占用端口的程序后重试。", err)
		logger.New("Browser").Error("调试端口分配失败",
			logger.F("profile_id", input.ProfileID),
			logger.F("error", err.Error()),
			logger.F("reason", startErr.Error()),
		)
		profile.LastError = startErr.Error()
		return nil, startErr
	}

	return &browserStartPlan{
		profile:              profile,
		chromeBinaryPath:     chromeBinaryPath,
		userDataDir:          userDataDir,
		extensionDirs:        extensionDirs,
		args:                 buildBrowserLaunchArgs(userDataDir, assignedDebugPort, effectiveProxy, extensionDirs, fingerprintLaunchArgs, sanitizedProfileLaunchArgs, sanitizedExtraLaunchArgs, launchTargets),
		deferredStartTargets: deferredStartTargets,
		effectiveProxy:       effectiveProxy,
		acquiredProxyBridge:  acquiredProxyBridge,
		releaseProxyBridge:   releaseProxyBridge,
		assignedDebugPort:    assignedDebugPort,
		startReadyTimeout:    startReadyTimeout,
		startStableWindow:    startStableWindow,
		maxStartAttempts:     maxStartAttempts,
		totalReadyTimeout:    totalReadyTimeout,
	}, nil
}

func (a *App) fingerprintCheckExpectedArgsForRunningProfile(profile *BrowserProfile, _ []string) []string {
	return a.fingerprintCheckExpectedArgsFromLockedProfile(profile)
}

func (a *App) prepareBrowserLaunchContext(input browserStartInput, profile *BrowserProfile, bookmarks []BrowserBookmark) ([]string, []string, []string, string, string, error) {
	log := logger.New("Browser")

	sanitizedProfileLaunchArgs, managedProfileArgs := sanitizeManagedLaunchArgs(profile.LaunchArgs)
	sanitizedExtraLaunchArgs, managedExtraArgs := sanitizeManagedLaunchArgs(input.ExtraLaunchArgs)
	logManagedLaunchArgOverrides(log, input.ProfileID, "profile.launchArgs", managedProfileArgs)
	logManagedLaunchArgOverrides(log, input.ProfileID, "start.extraLaunchArgs", managedExtraArgs)

	proxyChanged := a.browserMgr.ApplyDefaults(profile)
	if proxyChanged {
		_ = a.browserMgr.SaveProfiles()
	}

	chromeBinaryPath, err := a.browserMgr.ResolveChromeBinary(profile)
	if err != nil {
		startErr := fmt.Errorf("实例启动失败：%w", err)
		log.Error("内核路径解析失败",
			logger.F("profile_id", input.ProfileID),
			logger.F("error", err.Error()),
			logger.F("reason", startErr.Error()),
		)
		profile.LastError = startErr.Error()
		return nil, nil, nil, "", "", startErr
	}

	userDataDir := a.browserMgr.ResolveUserDataDir(profile)
	if err := os.MkdirAll(userDataDir, 0o755); err != nil {
		startErr := fmt.Errorf("实例启动失败：无法创建用户数据目录 %s。原因：%w。请检查目录权限或路径配置。", userDataDir, err)
		log.Error("用户数据目录创建失败",
			logger.F("profile_id", input.ProfileID),
			logger.F("dir", userDataDir),
			logger.F("error", err.Error()),
			logger.F("reason", startErr.Error()),
		)
		profile.LastError = startErr.Error()
		return nil, nil, nil, "", "", startErr
	}

	fingerprintLaunchArgs := a.buildBrowserFingerprintCapabilityReport(input.ProfileID, profile.CoreId, profile.FingerprintArgs).LaunchArgs
	fingerprintExpectedArgs := combineFingerprintExpectedArgs(fingerprintLaunchArgs, sanitizedProfileLaunchArgs, sanitizedExtraLaunchArgs)

	// 🛡️ 防风控时区与语言自动对齐：若未显式配置，根据代理归属国家自动补齐，消灭地域冲突指纹
	hasTimezone := browserArgValue(fingerprintExpectedArgs, "--timezone") != "" || browserArgValue(fingerprintExpectedArgs, "--tz") != ""
	hasLang := browserArgValue(fingerprintExpectedArgs, browserLangArg) != "" || browserArgValue(fingerprintExpectedArgs, browserAcceptLangArg) != ""
	if !hasTimezone || !hasLang {
		inferredTz, inferredLang := a.resolveProxyLocaleForProfile(profile, input)
		if !hasTimezone && inferredTz != "" {
			fingerprintLaunchArgs = append(fingerprintLaunchArgs, fmt.Sprintf("--timezone=%s", inferredTz))
			log.Info("根据代理归属国家自动对齐浏览器时区", logger.F("profile_id", input.ProfileID), logger.F("timezone", inferredTz))
		}
		if !hasLang && inferredLang != "" {
			fingerprintLaunchArgs = append(fingerprintLaunchArgs, fmt.Sprintf("--lang=%s", inferredLang), fmt.Sprintf("--accept-lang=%s", buildAcceptLanguage(inferredLang)))
			log.Info("根据代理归属国家自动对齐浏览器语言", logger.F("profile_id", input.ProfileID), logger.F("lang", inferredLang))
		}
		fingerprintExpectedArgs = combineFingerprintExpectedArgs(fingerprintLaunchArgs, sanitizedProfileLaunchArgs, sanitizedExtraLaunchArgs)
	}

	runtimeBookmarks, fingerprintBookmarkURL, bookmarkErr := a.runtimeBookmarksForProfileExpectedArgsAndProfile(profile.ProfileId, fingerprintExpectedArgs, profile, bookmarks)
	if bookmarkErr != nil {
		log.Error("指纹检测书签生成失败", logger.F("profile_id", input.ProfileID), logger.F("error", bookmarkErr.Error()))
		runtimeBookmarks = bookmarks
	}
	if fingerprintBookmarkURL != "" {
		if _, err := browser.ReplaceBookmarkURL(userDataDir, fingerprintCheckBookmarkURL, fingerprintBookmarkURL); err != nil {
			log.Warn("旧指纹检测书签更新失败", logger.F("profile_id", input.ProfileID), logger.F("error", err.Error()))
		}
	}
	if err := browser.EnsureDefaultBookmarks(userDataDir, runtimeBookmarks); err != nil {
		log.Error("默认书签写入失败", logger.F("error", err.Error()))
	}
	if err := writeBrowserLanguagePreferences(userDataDir, fingerprintLaunchArgs); err != nil {
		log.Error("浏览器语言偏好写入失败", logger.F("profile_id", input.ProfileID), logger.F("error", err.Error()))
	}

	if detection, ok := detectBrowserRuntimeByActivePort(userDataDir); ok && detection.DebugReady {
		a.markProfileLastLaunchArgsLocked(profile, nil)
		a.markProfileRunningLocked(input.ProfileID, profile, nil, detection.PID, detection.DebugPort, true, "")
		log.Warn("检测到同一用户数据目录已有浏览器运行，已接管为当前实例状态",
			logger.F("profile_id", input.ProfileID),
			logger.F("user_data_dir", userDataDir),
			logger.F("pid", detection.PID),
			logger.F("debug_port", detection.DebugPort),
		)
		if len(normalizeNonEmptyStrings(input.StartURLs)) == 0 && len(normalizeNonEmptyStrings(input.ExtraLaunchArgs)) == 0 {
			return nil, nil, nil, "", "", errBrowserStartHandledByRecoveredRuntime
		}
		fingerprintExpectedArgs := a.fingerprintCheckExpectedArgsForRunningProfile(profile, input.ExtraLaunchArgs)
		resolvedStartURLs := a.resolveFingerprintCheckStartURLsForExpectedArgsAndProfile(profile.ProfileId, fingerprintExpectedArgs, profile, input.StartURLs)
		if err := a.openBrowserTabForRunningProfile(profile, input.ExtraLaunchArgs, resolvedStartURLs); err != nil {
			startErr := fmt.Errorf("实例已在运行，但新标签打开失败：%w", err)
			profile.LastError = startErr.Error()
			return nil, nil, nil, "", "", startErr
		}
		return nil, nil, nil, "", "", errBrowserStartHandledByRecoveredRuntime
	}

	if !browserRestoreLastSession(a.config) {
		if err := clearBrowserSessionRestoreData(userDataDir); err != nil {
			if terminated, terminateErr := terminateBrowserProcessesByUserDataDir(userDataDir, 5*time.Second); terminateErr == nil && terminated {
				log.Warn("会话缓存被旧浏览器进程占用，已结束占用进程并重试清理",
					logger.F("profile_id", input.ProfileID),
					logger.F("user_data_dir", userDataDir),
				)
				if retryErr := clearBrowserSessionRestoreData(userDataDir); retryErr == nil {
					return sanitizedProfileLaunchArgs, sanitizedExtraLaunchArgs, fingerprintLaunchArgs, chromeBinaryPath, userDataDir, nil
				} else {
					err = retryErr
				}
			} else if terminateErr != nil {
				log.Warn("会话缓存清理失败后尝试结束占用进程失败",
					logger.F("profile_id", input.ProfileID),
					logger.F("user_data_dir", userDataDir),
					logger.F("error", terminateErr.Error()),
				)
			}
			sessionDir := filepath.Join(userDataDir, "Default", "Sessions")
			startErr := fmt.Errorf("实例启动失败：无法清理上次会话缓存 %s。原因：%w。请关闭占用该目录的浏览器进程后重试。", sessionDir, err)
			log.Error("会话恢复缓存清理失败",
				logger.F("profile_id", input.ProfileID),
				logger.F("dir", sessionDir),
				logger.F("error", err.Error()),
				logger.F("reason", startErr.Error()),
			)
			profile.LastError = startErr.Error()
			return nil, nil, nil, "", "", startErr
		}
	}

	return sanitizedProfileLaunchArgs, sanitizedExtraLaunchArgs, fingerprintLaunchArgs, chromeBinaryPath, userDataDir, nil
}

func buildBrowserLaunchArgs(userDataDir string, debugPort int, effectiveProxy string, extensionDirs []string, fingerprintLaunchArgs []string, sanitizedProfileLaunchArgs []string, sanitizedExtraLaunchArgs []string, launchTargets []string) []string {
	fingerprintLaunchArgs = browser.EnsureRuntimeFingerprintArgs(fingerprintLaunchArgs)
	args := []string{
		fmt.Sprintf("--user-data-dir=%s", userDataDir),
		fmt.Sprintf("--remote-debugging-port=%d", debugPort),
		"--disable-session-crashed-bubble",

		// === 核心抗自动化侦测与指纹伪装 ===
		"--excludeSwitches=enable-automation",           // 隐藏“受自动测试软件控制”警告条
		"--disable-infobars",                           // 禁用通知信息栏
		"--no-first-run",                               // 跳过首次启动引导页
		"--no-default-browser-check",                   // 跳过默认浏览器提示
		"--password-store=basic",                       // 跨系统独立凭据存储
		"--use-mock-keychain",                          // 避免钥匙串弹窗
		"--hide-crash-restore-bubble",                  // 禁用非正常关闭后的崩溃恢复黄条

		// === 屏蔽后台遥测与非预期直连网络请求 ===
		"--disable-background-networking",
		"--disable-component-update",
		"--disable-domain-reliability",
		"--disable-sync",
		"--disable-features=Translate,OptimizationHints,MediaRouter",
		"--metrics-recording-only",

		// === 内存稳定性与防 OOM 崩溃加固 (Anti-OOM & Memory Stability) ===
		"--js-flags=--max-old-space-size=4096",        // 扩展 V8 JS 堆内存从默认约 1.4GB 扩充至 4GB，预防 SPA 复杂页面堆内存溢出
		"--disable-dev-shm-usage",                     // 避免共享内存 (/dev/shm) 不足引发崩溃
		"--disable-gpu-memory-buffer-video-frames",    // 防止媒体流/视频帧缓冲持续占用渲染进程显存
	}

	args = appendEffectiveProxyLaunchArg(args, effectiveProxy)

	if extensionArg := strings.Join(normalizeNonEmptyStrings(extensionDirs), ","); extensionArg != "" {
		args = append(args, fmt.Sprintf("--disable-extensions-except=%s", extensionArg))
		args = append(args, fmt.Sprintf("--load-extension=%s", extensionArg))
	}

	args = append(args, normalizeNonEmptyStrings(fingerprintLaunchArgs)...)
	args = append(args, sanitizedProfileLaunchArgs...)
	args = append(args, sanitizedExtraLaunchArgs...)
	return browser.BuildLaunchArgs(args, launchTargets)
}

// appendEffectiveProxyLaunchArg keeps every Chromium launch path consistent.
// In particular, a running profile can need to fall back from CDP tab creation
// to a native new-window launch. That window must use the same proxy decision
// as a normal profile start instead of silently falling back to direct traffic.
func appendEffectiveProxyLaunchArg(args []string, effectiveProxy string) []string {
	if effectiveProxy == "direct://" {
		return append(args, "--no-proxy-server")
	}
	if effectiveProxy != "" {
		return append(args, fmt.Sprintf("--proxy-server=%s", effectiveProxy))
	}
	return args
}
