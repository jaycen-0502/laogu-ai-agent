package proxy

import (
	"ant-chrome/backend/internal/logger"
	"errors"
	"fmt"
	"time"
)

var errXrayBridgeRestartNotNeeded = errors.New("xray 桥接已无须恢复")

const xrayHealthInterval = 15 * time.Second
const xrayHealthFailureThreshold = 3

func cloneInterfaceSlice(items []interface{}) []interface{} {
	if len(items) == 0 {
		return nil
	}
	cloned := make([]interface{}, len(items))
	copy(cloned, items)
	return cloned
}

func xrayRecoveryDelay(attempt int) time.Duration {
	if attempt <= 0 {
		return 0
	}
	if attempt > 6 {
		attempt = 6
	}
	delay := time.Second * time.Duration(1<<(attempt-1))
	if delay > 30*time.Second {
		return 30 * time.Second
	}
	return delay
}

// Each monitor owns one process generation. Recovery publishes a new
// generation on the same port and starts its monitor before returning it.
func (m *XrayManager) watchBridge(bridge *XrayBridge, key string) {
	if bridge == nil || bridge.Cmd == nil {
		return
	}
	ticker := time.NewTicker(xrayHealthInterval)
	defer ticker.Stop()
	failures := 0
	var cause error
monitor:
	for {
		select {
		case <-m.stopCh:
			return
		case <-bridge.ExitDone:
			cause = fmt.Errorf("xray 桥接进程退出: %v", bridge.exitErr())
			break monitor
		case <-ticker.C:
			m.mu.Lock()
			current := m.Bridges[key] == bridge && !bridge.Stopping
			pinned := bridge.RefCount > 0
			m.mu.Unlock()
			if !current {
				return
			}
			if !pinned {
				continue
			}
			// Only consecutive LOCAL protocol failures trigger recovery; failure
			// of an upstream site must not tear down other working pages.
			if err := checkSocks5Handshake(fmt.Sprintf("127.0.0.1:%d", bridge.Port), time.Second); err == nil {
				failures = 0
			} else {
				failures++
				if failures >= xrayHealthFailureThreshold {
					cause = fmt.Errorf("xray 本地 SOCKS 握手连续失败: %w", err)
					break monitor
				}
			}
		}
	}
	m.mu.Lock()
	if m.Bridges[key] != bridge || bridge.Stopping || m.isStopped() {
		m.mu.Unlock()
		return
	}
	bridge.Running = false
	if bridge.RefCount <= 0 {
		bridge.Stopping = true
		delete(m.Bridges, key)
		m.mu.Unlock()
		m.stopBridgeProcess(bridge)
		return
	}
	bridge.Restarting = true
	bridge.LastError = cause.Error()
	if time.Since(bridge.StartedAt) >= time.Minute {
		bridge.RestartCount = 0
	}
	attempt := bridge.RestartCount
	m.mu.Unlock()
	log := logger.New("Xray")
	log.Warn("xray 桥接异常，保留原端口恢复", logger.F("key", key), logger.F("port", bridge.Port), logger.F("error", cause.Error()))
	if m.OnBridgeDied != nil {
		m.OnBridgeDied(key, cause)
	}
	for {
		if delay := xrayRecoveryDelay(attempt); delay > 0 {
			timer := time.NewTimer(delay)
			select {
			case <-m.stopCh:
				timer.Stop()
				return
			case <-timer.C:
			}
		}
		err := m.restartPinnedBridge(log, key, bridge, 0)
		if err == nil {
			if m.OnBridgeRecovered != nil {
				m.OnBridgeRecovered(key)
			}
			return
		}
		if errors.Is(err, errXrayBridgeRestartNotNeeded) {
			return
		}
		m.mu.Lock()
		if m.Bridges[key] == bridge {
			bridge.LastError = err.Error()
		}
		m.mu.Unlock()
		// Keep the placeholder, port and live reference count even during long
		// failures, so probes cannot switch already-running browsers to a new port.
		log.Warn("xray 原端口恢复未成功，稍后重试", logger.F("key", key), logger.F("port", bridge.Port), logger.F("error", err.Error()))
		attempt++
	}
}

func (m *XrayManager) restartPinnedBridge(log *logger.Logger, key string, bridge *XrayBridge, _ int) error {
	if bridge == nil {
		return fmt.Errorf("xray 桥接不存在")
	}
	unlockLaunch := m.lockLaunchForKey(key)
	defer unlockLaunch()
	m.mu.Lock()
	if m.Bridges[key] != bridge || bridge.Stopping || m.isStopped() {
		m.mu.Unlock()
		return errXrayBridgeRestartNotNeeded
	}
	if bridge.RefCount <= 0 {
		bridge.Stopping = true
		delete(m.Bridges, key)
		m.mu.Unlock()
		m.stopBridgeProcess(bridge)
		return errXrayBridgeRestartNotNeeded
	}
	m.mu.Unlock()
	if len(bridge.Outbounds) == 0 || len(bridge.Routes) == 0 {
		return fmt.Errorf("xray 桥接缺少重启上下文")
	}
	// Wait until the old process has released its listener, including a hung
	// process which had to be killed after repeated local handshake failures.
	m.stopBridgeProcess(bridge)
	select {
	case <-bridge.ExitDone:
	case <-m.stopCh:
		return errXrayBridgeRestartNotNeeded
	case <-time.After(3 * time.Second):
		return fmt.Errorf("等待旧 xray 进程退出超时")
	}
	binaryPath, err := m.resolveBinary()
	if err != nil {
		return err
	}
	url, restarted, err := m.launchBridgeAttempt(log, key, binaryPath,
		cloneInterfaceSlice(bridge.Outbounds), cloneInterfaceSlice(bridge.Routes),
		bridge.Port, bridge.DNSServers, false, 1)
	if err != nil {
		return err
	}
	if restarted == nil {
		return fmt.Errorf("xray 桥接恢复异常: 未返回新进程")
	}
	log.Info("xray 桥接已同端口恢复", logger.F("key", key), logger.F("port", restarted.Port), logger.F("pid", restarted.Pid), logger.F("socks_url", url))
	return nil
}
