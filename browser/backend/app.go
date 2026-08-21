package backend

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	"ant-chrome/backend/internal/automation"
	"ant-chrome/backend/internal/automationstate"
	"ant-chrome/backend/internal/browser"
	"ant-chrome/backend/internal/config"
	"ant-chrome/backend/internal/database"
	"ant-chrome/backend/internal/launchcode"
	"ant-chrome/backend/internal/logger"
	"ant-chrome/backend/internal/offlinelicense"
	"ant-chrome/backend/internal/proxy"

	wailsRuntime "github.com/wailsapp/wails/v2/pkg/runtime"
)

type quitMode uint8

const (
	quitModeFull quitMode = iota
	quitModeAppOnly
)

// App 应用结构体
type App struct {
	ctx                  context.Context
	config               *config.Config
	db                   *database.DB
	interceptor          *logger.MethodInterceptor
	browserMgr           *browser.Manager
	xrayMgr              *proxy.XrayManager
	clashMgr             *proxy.ClashManager
	singboxMgr           *proxy.SingBoxManager
	launchCodeSvc        *launchcode.LaunchCodeService
	launchServer         *launchcode.LaunchServer
	automationMgr        *automation.Manager
	automationStateStore *automationstate.Store
	speedScheduler       *browser.ProxySpeedScheduler
	appRoot              string
	version              string
	licenseMgr           *offlinelicense.Manager
	licenseMu            sync.Mutex
	licenseMonitorOnce   sync.Once
	startupMu            sync.Mutex
	runtimeStarted       bool

	forceQuit              bool
	quitMode               quitMode
	maintenanceMu          sync.Mutex
	bridgeMu               sync.Mutex
	profileBridgeRefs      map[string]profileProxyBridgeRef
	deferredStartTargetsMu sync.Mutex
	deferredStartTargets   map[string][]string
	automationTargetMu     sync.Mutex
	automationTargetCursor map[string]string
	stopServicesOnce       sync.Once
	finalizeOnce           sync.Once

	// 自动化运行中的 Canceller 句柄映射，用于精准杀死指定实例的程序
	runningTaskMu     sync.Mutex
	runningTaskCancel map[string]context.CancelFunc
}

// NewApp 创建新的应用实例
func NewApp(appRoot string, appVersion ...string) *App {
	version := ""
	if len(appVersion) > 0 {
		version = strings.TrimSpace(appVersion[0])
	}
	return &App{
		appRoot:                strings.TrimSpace(appRoot),
		version:                version,
		profileBridgeRefs:      make(map[string]profileProxyBridgeRef),
		deferredStartTargets:   make(map[string][]string),
		automationTargetCursor: make(map[string]string),
		runningTaskCancel:      make(map[string]context.CancelFunc),
	}
}

// BindContext 保存 Wails 上下文
func (a *App) BindContext(ctx context.Context) {
	a.ctx = ctx
}

// VerifyAdminPassword 校验本机管理员密码。配置支持明文兼容值和 sha256:<hex>。
func (a *App) VerifyAdminPassword(password string) bool {
	if a.config == nil {
		return false
	}
	configured := strings.TrimSpace(a.config.App.AdminPassword)
	if configured == "" {
		return false
	}
	provided := strings.TrimSpace(password)
	if strings.HasPrefix(strings.ToLower(configured), "sha256:") {
		expected, err := hex.DecodeString(strings.TrimSpace(configured[len("sha256:"):]))
		if err != nil || len(expected) != sha256.Size {
			return false
		}
		actual := sha256.Sum256([]byte(provided))
		return subtle.ConstantTimeCompare(actual[:], expected) == 1
	}
	return subtle.ConstantTimeCompare([]byte(provided), []byte(configured)) == 1
}

// VerifyAndRegisterAutomationTask 保留旧调用兼容性，并恢复密码校验。
func (a *App) VerifyAndRegisterAutomationTask(password string, targetCode string, runTaskFunc func(ctx context.Context) error) error {
	if !a.VerifyAdminPassword(password) {
		return fmt.Errorf("管理员密码错误")
	}
	return a.RegisterAutomationTask(targetCode, runTaskFunc)
}

// ------------------- 自动化程序与进程生命周期管理 -------------------

// RegisterAutomationTask 注册并开始执行自动化程序（无密码保护，直接启动）
func (a *App) RegisterAutomationTask(targetCode string, runTaskFunc func(ctx context.Context) error) error {
	a.runningTaskMu.Lock()
	if _, exists := a.runningTaskCancel[targetCode]; exists {
		a.runningTaskMu.Unlock()
		return fmt.Errorf("实例 %s 已有自动化程序在运行中", targetCode)
	}

	// 继承 a.ctx 创建独立可取消的 Task Context
	parentCtx := a.ctx
	if parentCtx == nil {
		parentCtx = context.Background()
	}
	taskCtx, cancel := context.WithCancel(parentCtx)

	a.runningTaskCancel[targetCode] = cancel
	a.runningTaskMu.Unlock()

	// 实时推送状态给前端：运行中
	a.emitAutomationStatus(targetCode, "RUNNING", "程序启动中...")

	go func() {
		hasEmittedError := false

		defer func() {
			// 1. 防崩溃保护：捕获协程内部未处理的 panic，防止应用闪退
			if r := recover(); r != nil {
				log.Printf("[ERROR] 自动化程序 [%s] 发生 Panic 崩溃: %v", targetCode, r)
				a.emitAutomationStatus(targetCode, "ERROR", fmt.Sprintf("程序运行崩溃: %v", r))
				hasEmittedError = true
			}

			a.runningTaskMu.Lock()
			_, existed := a.runningTaskCancel[targetCode]
			delete(a.runningTaskCancel, targetCode)
			a.runningTaskMu.Unlock()

			// 2. 精准推送停止状态：仅在程序正常完结（且未触发ERROR或手动Cancel）时推送 STOPPED
			if existed && !hasEmittedError {
				a.emitAutomationStatus(targetCode, "STOPPED", "程序已运行完成")
			}
		}()

		// 执行自动化程序
		err := runTaskFunc(taskCtx)

		// 3. 过滤正常取消的 Context 信号
		if err != nil {
			if taskCtx.Err() == context.Canceled {
				log.Printf("[INFO] 自动化程序 [%s] 已手动取消/正常停止", targetCode)
				return
			}

			// 真正发生报错
			hasEmittedError = true
			log.Printf("[ERROR] 自动化程序 [%s] 异常退出: %v", targetCode, err)
			a.emitAutomationStatus(targetCode, "ERROR", fmt.Sprintf("程序运行报错: %v", err))
		}
	}()

	return nil
}

// StopAutomationTask 手动强行停止某个指定实例的自动化程序（无死锁优化）
func (a *App) StopAutomationTask(targetCode string) {
	a.runningTaskMu.Lock()
	cancel, exists := a.runningTaskCancel[targetCode]
	if exists {
		delete(a.runningTaskCancel, targetCode)
	}
	a.runningTaskMu.Unlock()

	if exists && cancel != nil {
		cancel() // 在解锁后安全触发 Context 取消信号，避免死锁
		a.emitAutomationStatus(targetCode, "STOPPED", "程序已手动停止")
	}
}

// StopAllAutomationTasks 彻底杀死所有正在运行的自动化程序（软件退出或窗口关闭时调用，无死锁优化）
func (a *App) StopAllAutomationTasks() {
	a.runningTaskMu.Lock()
	cancels := make(map[string]context.CancelFunc, len(a.runningTaskCancel))
	for code, cancel := range a.runningTaskCancel {
		cancels[code] = cancel
	}
	a.runningTaskCancel = make(map[string]context.CancelFunc) // 清空 map
	a.runningTaskMu.Unlock()

	// 批量解锁触发取消
	for targetCode, cancel := range cancels {
		if cancel != nil {
			cancel()
			a.emitAutomationStatus(targetCode, "STOPPED", "应用关闭，强行停止程序")
		}
	}
}

// emitAutomationStatus 辅助方法：零延迟向前端推送最新自动化程序状态
func (a *App) emitAutomationStatus(targetCode string, status string, message string) {
	if a.ctx != nil {
		wailsRuntime.EventsEmit(a.ctx, "automation-status-change", map[string]interface{}{
			"code":       targetCode,
			"targetCode": targetCode,
			"status":     status,
			"message":    message,
			"timestamp":  time.Now().UnixMilli(),
		})
	}
}

func (a *App) appName() string {
	if a.config != nil {
		if name := strings.TrimSpace(a.config.App.Name); name != "" {
			return name
		}
	}
	return "Laogu Browser"
}

func (a *App) appVersion() string {
	version := strings.TrimSpace(a.version)
	if version == "" {
		return "unknown"
	}
	return version
}

// ResetAutomationNotifications 重置并清空所有自动化程序相关的异常通知与缓存状态
func (a *App) ResetAutomationNotifications() map[string]interface{} {
	a.runningTaskMu.Lock()
	// 1. 停止并清空当前所有正在运行的程序句柄
	for targetCode, cancel := range a.runningTaskCancel {
		if cancel != nil {
			cancel()
		}
		delete(a.runningTaskCancel, targetCode)
	}
	a.runningTaskMu.Unlock()

	// 2. 向前端发送全局重置与清空通知事件
	if a.ctx != nil {
		wailsRuntime.EventsEmit(a.ctx, "automation-notifications-reset", map[string]interface{}{
			"reset":     true,
			"timestamp": time.Now().UnixMilli(),
		})

		// 广播全量状态清除，告知前端重置所有实例状态为 IDLE
		wailsRuntime.EventsEmit(a.ctx, "automation-status-change", map[string]interface{}{
			"code":       "*",
			"targetCode": "*",
			"status":     "IDLE",
			"message":    "系统已重置，历史通知已清空",
			"timestamp":  time.Now().UnixMilli(),
		})
	}

	return map[string]interface{}{
		"success": true,
		"message": "已成功重置所有自动化程序状态与异常通知",
	}
}
