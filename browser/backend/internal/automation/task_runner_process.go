package automation

import (
	"fmt"
	"os/exec"
	goruntime "runtime"
	"sort"
	"strings"

	"github.com/google/uuid"
)

// ActiveTasks returns a stable snapshot of all currently running workers.
func (m *Manager) ActiveTasks() []ActiveTaskInfo {
	m.mu.RLock()
	tasks := make([]ActiveTaskInfo, 0, len(m.activeTasks))
	for _, task := range m.activeTasks {
		if task == nil {
			continue
		}
		tasks = append(tasks, ActiveTaskInfo{
			TaskID:    task.taskID,
			ProfileID: task.profileID,
		})
	}
	m.mu.RUnlock()

	sort.Slice(tasks, func(i, j int) bool {
		if tasks[i].ProfileID == tasks[j].ProfileID {
			return tasks[i].TaskID < tasks[j].TaskID
		}
		return tasks[i].ProfileID < tasks[j].ProfileID
	})
	return tasks
}

// StopTasksByProfile stops only the Playwright workers attached to profileID.
// Browser processes are deliberately left running so other profiles are not affected.
func (m *Manager) StopTasksByProfile(profileID string) int {
	profileID = strings.TrimSpace(profileID)
	if profileID == "" {
		return 0
	}

	m.mu.RLock()
	taskIDs := make([]string, 0)
	for taskID, task := range m.activeTasks {
		if task != nil && task.profileID == profileID {
			taskIDs = append(taskIDs, taskID)
		}
	}
	m.mu.RUnlock()

	stopped := 0
	for _, taskID := range taskIDs {
		if err := m.StopTask(taskID); err == nil {
			stopped++
		}
	}
	return stopped
}

// StopAllTasks 停止所有正在运行的自动化任务
func (m *Manager) StopAllTasks() {
	m.mu.Lock()
	tasks := make([]*activeTask, 0, len(m.activeTasks))
	for _, task := range m.activeTasks {
		if task != nil {
			tasks = append(tasks, task)
		}
	}
	m.activeTasks = make(map[string]*activeTask)
	m.profileTaskPool = make(map[string]map[string]bool) // 清空 profile 任务池
	m.mu.Unlock()

	for _, task := range tasks {
		if task == nil {
			continue
		}
		if task.cmd != nil && task.cmd.Process != nil {
			_ = stopTaskProcess(task.cmd)
		}
		// 通知前端状态已停止
		m.emitTaskEvent(TaskEvent{
			TaskID:    task.taskID,
			ProfileID: task.profileID,
			Phase:     "failed",
			Message:   "所有自动化任务已由用户手动停止",
		})
	}
}

// StopTask 停止指定的单个自动化任务
func (m *Manager) StopTask(taskID string) error {
	taskID = strings.TrimSpace(taskID)
	if taskID == "" {
		return fmt.Errorf("taskID 不能为字串为空")
	}

	m.mu.Lock()
	task, ok := m.activeTasks[taskID]
	m.mu.Unlock()

	if !ok || task == nil {
		return nil
	}

	// 执行强杀逻辑
	if task.cmd != nil && task.cmd.Process != nil {
		_ = stopTaskProcess(task.cmd)
	}

	// 立即清理注册信息，释放资源锁
	m.unregisterTask(taskID)

	m.emitTaskEvent(TaskEvent{
		TaskID:    task.taskID,
		ProfileID: task.profileID,
		Phase:     "failed",
		Message:   "自动化任务已被用户手动停止",
	})

	return nil
}

// RunAndMonitorTaskCmd 启动并异步监控 cmd 进程生命周期（关键：解决关闭浏览器后 UI 卡住转圈的问题）
func (m *Manager) RunAndMonitorTaskCmd(profileID string, cmd *exec.Cmd) (string, error) {
	// 1. 注册 TaskID（支持多实例并发）
	taskID, err := m.registerTask(profileID)
	if err != nil {
		return "", err
	}

	// 2. 绑定 cmd
	m.attachTaskCommand(taskID, cmd)

	// 3. 启动进程
	if err := cmd.Start(); err != nil {
		m.unregisterTask(taskID)
		return "", fmt.Errorf("启动自动化进程失败: %w", err)
	}

	// 4. 广播前端：任务已开始运行
	m.emitTaskEvent(TaskEvent{
		TaskID:    taskID,
		ProfileID: profileID,
		Phase:     "running",
		Message:   "自动化任务已启动执行...",
	})

	// 5. 开启异步 Goroutine 监听进程退出状态
	go func() {
		defer func() {
			// 5.1 进程退出（无论正常结束、报错还是手关浏览器），解绑并释放当前 taskID
			m.detachTaskCommand(taskID)
			m.unregisterTask(taskID)
		}()

		// 阻塞等待 Node/Playwright 进程完全退出（手关浏览器时 Node 进程随之结束，这里秒级感应）
		waitErr := cmd.Wait()

		m.mu.Lock()
		// 检查任务是否已经被 StopTask 手动清理掉了
		_, stillActive := m.activeTasks[taskID]
		m.mu.Unlock()

		// 如果任务尚未被手动 Stop 清理，说明是自然的正常结束或异常退出，通知前端停止转圈
		if stillActive {
			if waitErr != nil {
				m.emitTaskEvent(TaskEvent{
					TaskID:    taskID,
					ProfileID: profileID,
					Phase:     "failed",
					Message:   fmt.Sprintf("脚本执行结束或连接断开: %v", waitErr),
				})
			} else {
				m.emitTaskEvent(TaskEvent{
					TaskID:    taskID,
					ProfileID: profileID,
					Phase:     "done",
					Message:   "自动化任务执行完成",
				})
			}
		}
	}()

	return taskID, nil
}

// registerTask 允许同一个 Profile 实例同时注册并运行多个程序（解除独占死锁）
func (m *Manager) registerTask(profileID string) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()

	// 初始化 activeTasks (如果不存在)
	if m.activeTasks == nil {
		m.activeTasks = make(map[string]*activeTask)
	}

	// 初始化 profileTaskPool (如果不存在)
	if m.profileTaskPool == nil {
		m.profileTaskPool = make(map[string]map[string]bool)
	}
	if _, ok := m.profileTaskPool[profileID]; !ok {
		m.profileTaskPool[profileID] = make(map[string]bool)
	}

	// 生成新的任务 ID
	taskID := uuid.NewString()

	// 将新任务加入到该 profile 的并发任务集合中
	m.profileTaskPool[profileID][taskID] = true

	// 注册到全局活动任务列表
	m.activeTasks[taskID] = &activeTask{
		taskID:    taskID,
		profileID: profileID,
	}

	return taskID, nil
}

func (m *Manager) attachTaskCommand(taskID string, cmd *exec.Cmd) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if task, ok := m.activeTasks[taskID]; ok && task != nil {
		task.cmd = cmd
	}
}

// detachTaskCommand 安全解绑 cmd 引用，防止内存泄漏或无效 Kill 操作
func (m *Manager) detachTaskCommand(taskID string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if task, ok := m.activeTasks[taskID]; ok && task != nil {
		task.cmd = nil
	}
}

// unregisterTask 只精细化释放当前 taskID 的状态，不影响同一个 Profile 下的其他并发程序
func (m *Manager) unregisterTask(taskID string) {
	m.mu.Lock()
	defer m.mu.Unlock()

	task, ok := m.activeTasks[taskID]
	if !ok || task == nil {
		return
	}

	// 1. 从全局活动任务集中移除当前 task
	delete(m.activeTasks, taskID)

	// 2. 从 profileTaskPool 引用池中移除当前 taskID
	if pool, ok := m.profileTaskPool[task.profileID]; ok {
		delete(pool, taskID)
		// 如果该 profile 下已经没有任何运行中的任务，清理外层 map
		if len(pool) == 0 {
			delete(m.profileTaskPool, task.profileID)
		}
	}
}

func (m *Manager) emitTaskEvent(event TaskEvent) {
	if m.emit == nil {
		return
	}
	m.emit(TaskEventName, event)
}

func stopTaskProcess(cmd *exec.Cmd) error {
	if cmd == nil || cmd.Process == nil {
		return nil
	}
	if goruntime.GOOS == "windows" {
		// 在 Windows 下使用 taskkill /F /T 杀死进程树（包括 Node 及其启动的 Chrome）
		killCmd := exec.Command("taskkill", "/F", "/T", "/PID", fmt.Sprintf("%d", cmd.Process.Pid))
		hideWindow(killCmd)
		if err := killCmd.Run(); err == nil {
			return nil
		}
	} else if cmd.Process.Pid > 0 {
		if err := killProcessGroup(cmd.Process.Pid); err == nil {
			return nil
		}
	}
	err := cmd.Process.Kill()
	if err == nil {
		return nil
	}
	if strings.Contains(strings.ToLower(err.Error()), "already finished") {
		return nil
	}
	return err
}
