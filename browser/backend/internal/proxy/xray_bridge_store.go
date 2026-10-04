package proxy

import (
	"fmt"
	"time"
)

func (m *XrayManager) isStopped() bool {
	select {
	case <-m.stopCh:
		return true
	default:
		return false
	}
}

// Called with m.mu held. Cmd.Wait writes ProcessState; ExitDone is the
// synchronization point used to observe process exit without a data race.
func xrayBridgeAlive(bridge *XrayBridge) bool {
	if bridge == nil || !bridge.Running || bridge.Stopping || bridge.Restarting || bridge.Cmd == nil || bridge.Cmd.Process == nil {
		return false
	}
	select {
	case <-bridge.ExitDone:
		return false
	default:
		return true
	}
}

// Caller owns the per-node launch lock. Never hold the manager lock during
// network I/O, and never discard a listener with browser references.
func (m *XrayManager) tryReuseBridge(key string, pin bool) (string, bool) {
	m.mu.Lock()
	bridge := m.Bridges[key]
	alive := xrayBridgeAlive(bridge)
	m.mu.Unlock()
	if bridge == nil {
		return "", false
	}
	ready := alive && waitSocks5Ready("127.0.0.1", bridge.Port, 800*time.Millisecond) == nil
	m.mu.Lock()
	if m.Bridges[key] != bridge || m.isStopped() {
		m.mu.Unlock()
		return "", false
	}
	if ready && xrayBridgeAlive(bridge) {
		if pin {
			bridge.RefCount++
		}
		bridge.LastUsedAt = time.Now()
		m.mu.Unlock()
		return fmt.Sprintf("socks5://127.0.0.1:%d", bridge.Port), true
	}
	if bridge.RefCount > 0 {
		m.mu.Unlock()
		return "", false
	}
	bridge.Stopping = true
	delete(m.Bridges, key)
	m.mu.Unlock()
	m.stopBridgeProcess(bridge)
	return "", false
}

// Serialized by the per-node launch lock. Transfer references at publication
// so releases during process startup are not lost.
func (m *XrayManager) registerBridge(key string, bridge *XrayBridge, pin bool) (string, bool, error) {
	m.mu.Lock()
	if m.isStopped() {
		m.mu.Unlock()
		return "", false, fmt.Errorf("xray 管理器已停止")
	}
	existing := m.Bridges[key]
	if existing == bridge {
		m.mu.Unlock()
		return "", false, nil
	}
	if xrayBridgeAlive(existing) {
		if pin {
			existing.RefCount++
		}
		existing.LastUsedAt = time.Now()
		url := fmt.Sprintf("socks5://127.0.0.1:%d", existing.Port)
		m.mu.Unlock()
		m.stopBridgeProcess(bridge)
		return url, true, nil
	}
	if existing != nil {
		if existing.RefCount > 0 && existing.Port != bridge.Port {
			m.mu.Unlock()
			return "", false, fmt.Errorf("禁止更换活动浏览器的 xray 代理端口")
		}
		bridge.RefCount = existing.RefCount
		bridge.RestartCount = existing.RestartCount
		if existing.Restarting {
			bridge.RestartCount++
		}
		existing.Stopping = true
	}
	if pin {
		bridge.RefCount++
	}
	bridge.LastUsedAt = time.Now()
	m.Bridges[key] = bridge
	m.mu.Unlock()
	if existing != nil {
		m.stopBridgeProcess(existing)
	}
	return "", false, nil
}
