package automation

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"time"
)

const TaskEventName = "automation:task:state"

const (
	taskTypeScript = "script"
)

func (m *Manager) RunScriptTask(ctx context.Context, req ScriptTaskRequest) (ScriptTaskResult, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	timeoutLimit := req.Timeout
	if req.Timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, req.Timeout)
		defer cancel()
	} else if deadline, ok := ctx.Deadline(); ok {
		timeoutLimit = time.Until(deadline)
	}

	state := m.CurrentState()
	if !state.Ready {
		return ScriptTaskResult{}, fmt.Errorf("自动化运行时尚未就绪")
	}

	req.TaskKey = strings.TrimSpace(req.TaskKey)
	if req.TaskKey == "" {
		return ScriptTaskResult{}, fmt.Errorf("taskKey is required")
	}
	req.ScriptPath = strings.TrimSpace(req.ScriptPath)
	if req.ScriptPath == "" {
		return ScriptTaskResult{}, fmt.Errorf("scriptPath is required")
	}
	req.LaunchBaseURL = strings.TrimSpace(req.LaunchBaseURL)
	if req.LaunchBaseURL == "" {
		return ScriptTaskResult{}, fmt.Errorf("launchBaseUrl is required")
	}

	// 💡【核心修复】防空与默认实例参数自动补全：
	// 确保 req.Params 绝对不为 nil，避免 JSON 序列化为 null 导致 Node 端解析失败
	if req.Params == nil {
		req.Params = make(map[string]any)
	}

	// 当使用“默认实例”时，如果 Params 里没有 profileId / taskKey，自动用 req.TaskKey 填充
	if val, ok := req.Params["profileId"]; !ok || strings.TrimSpace(fmt.Sprint(val)) == "" {
		req.Params["profileId"] = req.TaskKey
	}
	if _, ok := req.Params["taskKey"]; !ok {
		req.Params["taskKey"] = req.TaskKey
	}

	payload := taskRunnerPayload{
		TaskType:         taskTypeScript,
		ScriptID:         strings.TrimSpace(req.ScriptID),
		RuntimeDir:       state.RuntimeDir,
		ScriptPath:       req.ScriptPath,
		Selector:         req.Selector,
		Params:           req.Params,
		LaunchBaseURL:    req.LaunchBaseURL,
		LaunchAuthHeader: strings.TrimSpace(req.LaunchAuthHeader),
		LaunchAuthValue:  strings.TrimSpace(req.LaunchAuthValue),
		ArtifactDir:      strings.TrimSpace(req.ArtifactDir),
	}

	taskID, runnerResp, rawOutput, durationMs, err := m.executeTask(
		ctx,
		req.TaskKey,
		payload,
		"自动化 script task 已启动",
		"自动化 script task 已完成",
		timeoutLimit,
	)
	if err != nil {
		return ScriptTaskResult{}, err
	}

	result := ScriptTaskResult{
		TaskID:            taskID,
		TaskKey:           req.TaskKey,
		OK:                runnerResp.OK,
		Summary:           strings.TrimSpace(runnerResp.Summary),
		Error:             strings.TrimSpace(runnerResp.Error),
		ResultText:        rawOutput,
		LogText:           formatTaskRunnerLogs(runnerResp.Logs),
		DurationMs:        durationMs,
		StartedAt:         runnerResp.StartedAt,
		FinishedAt:        runnerResp.FinishedAt,
		RuntimeVersion:    state.RuntimeVersion,
		NodeVersion:       state.NodeVersion,
		PlaywrightVersion: state.PlaywrightVersion,
	}
	if result.Summary == "" {
		if result.OK {
			result.Summary = "脚本执行完成"
		} else {
			result.Summary = "脚本执行失败"
		}
	}
	return result, nil
}

func formatTaskRunnerLogs(logs []taskRunnerLogEntry) string {
	if len(logs) == 0 {
		return ""
	}
	lines := make([]string, 0, len(logs))
	for _, entry := range logs {
		valueText := formatTaskRunnerLogValues(entry.Values)
		if valueText == "" {
			continue
		}
		timeText := strings.TrimSpace(entry.Time)
		if timeText == "" {
			lines = append(lines, valueText)
			continue
		}
		lines = append(lines, fmt.Sprintf("%s %s", timeText, valueText))
	}
	return strings.Join(lines, "\n")
}

func formatTaskRunnerLogValues(values []any) string {
	parts := make([]string, 0, len(values))
	for _, value := range values {
		parts = append(parts, formatTaskRunnerLogValue(value))
	}
	return strings.TrimSpace(strings.Join(parts, " "))
}

func formatTaskRunnerLogValue(value any) string {
	if value == nil {
		return "null"
	}
	if text, ok := value.(string); ok {
		return strings.TrimSpace(text)
	}
	if reflect.TypeOf(value).Kind() == reflect.Map || reflect.TypeOf(value).Kind() == reflect.Slice {
		if data, err := json.Marshal(value); err == nil {
			return string(data)
		}
	}
	return strings.TrimSpace(fmt.Sprint(value))
}

func (m *Manager) executeTask(ctx context.Context, taskKey string, payload taskRunnerPayload, startMessage string, completeMessage string, timeoutLimit time.Duration) (string, taskRunnerResponse, string, int64, error) {
	taskID, err := m.registerTask(taskKey)
	if err != nil {
		return "", taskRunnerResponse{}, "", 0, err
	}
	// 确保退出时立即解绑 Task 锁
	defer m.unregisterTask(taskID)

	payloadPath, err := m.writeTaskPayload(payload)
	if err != nil {
		return "", taskRunnerResponse{}, "", 0, err
	}
	defer os.Remove(payloadPath)

	state := m.CurrentState()
	cmd := exec.CommandContext(ctx, state.NodePath, state.RunnerPath, payloadPath)
	cmd.Dir = state.RuntimeDir
	prepareTaskCommand(cmd)
	cmd.Cancel = func() error {
		return stopTaskProcess(cmd)
	}
	cmd.WaitDelay = 3 * time.Second

	// stdout 必须保持为 runner 的 JSON 协议；stderr 单独收集，避免提示文本污染 JSON。
	var outBuf bytes.Buffer
	var errBuf bytes.Buffer
	cmd.Stdout = &outBuf
	cmd.Stderr = &errBuf

	startedAt := time.Now()
	m.attachTaskCommand(taskID, cmd)
	// 无论如何退出，确保最后从 activeTasks 中移除 command 引用
	defer m.detachTaskCommand(taskID)

	// 标记事件是否已发送，防止 defer 重复发送
	eventEmitted := false
	defer func() {
		// 兜底逻辑：如果函数因任何报错退出且尚未广播终态事件，强行广播 failed 事件解除前端 UI 转圈
		if !eventEmitted {
			m.emitTaskEvent(TaskEvent{
				TaskID:     taskID,
				ProfileID:  taskKey,
				Phase:      "failed",
				Message:    "任务执行已中断或链接断开",
				StartedAt:  startedAt.Format(time.RFC3339),
				FinishedAt: time.Now().Format(time.RFC3339),
				DurationMs: time.Since(startedAt).Milliseconds(),
			})
		}
	}()

	m.emitTaskEvent(TaskEvent{
		TaskID:    taskID,
		ProfileID: taskKey,
		Phase:     "started",
		Message:   startMessage,
		StartedAt: startedAt.Format(time.RFC3339),
	})

	// 启动子进程
	if err := cmd.Start(); err != nil {
		eventEmitted = true
		m.emitTaskEvent(TaskEvent{
			TaskID:     taskID,
			ProfileID:  taskKey,
			Phase:      "failed",
			Message:    "进程启动失败: " + err.Error(),
			StartedAt:  startedAt.Format(time.RFC3339),
			FinishedAt: time.Now().Format(time.RFC3339),
		})
		return "", taskRunnerResponse{}, "", 0, fmt.Errorf("启动自动化进程失败: %w", err)
	}

	// 监听 Wait 结果的 Channel
	waitDone := make(chan error, 1)
	go func() {
		waitDone <- cmd.Wait()
	}()

	var runErr error
	select {
	case <-ctx.Done():
		// Context 超时或取消（例如用户点击停止）
		_ = stopTaskProcess(cmd)
		// 等待进程完全释放，最长等 2 秒
		select {
		case <-waitDone:
		case <-time.After(2 * time.Second):
		}

		durationMs := time.Since(startedAt).Milliseconds()
		ctxErr := ctx.Err()
		message := taskContextErrorMessage(ctxErr, timeoutLimit)
		eventEmitted = true
		m.emitTaskEvent(TaskEvent{
			TaskID:     taskID,
			ProfileID:  taskKey,
			Phase:      "failed",
			Message:    message,
			StartedAt:  startedAt.Format(time.RFC3339),
			FinishedAt: time.Now().Format(time.RFC3339),
			DurationMs: durationMs,
		})
		return "", taskRunnerResponse{}, "", durationMs, fmt.Errorf("%s", message)

	case runErr = <-waitDone:
		// 进程自然结束或被打断
	}

	output := outBuf.Bytes()
	durationMs := time.Since(startedAt).Milliseconds()

	if runErr != nil {
		message := strings.TrimSpace(errBuf.String())
		if message == "" {
			message = strings.TrimSpace(string(output))
		}
		if message == "" {
			message = runErr.Error()
		}
		eventEmitted = true
		m.emitTaskEvent(TaskEvent{
			TaskID:     taskID,
			ProfileID:  taskKey,
			Phase:      "failed",
			Message:    message,
			StartedAt:  startedAt.Format(time.RFC3339),
			FinishedAt: time.Now().Format(time.RFC3339),
			DurationMs: durationMs,
		})
		return "", taskRunnerResponse{}, "", durationMs, fmt.Errorf("自动化任务执行失败: %s", message)
	}

	runnerResp, normalizedOutput, parseErr := parseTaskRunnerResponse(output)
	if parseErr != nil {
		eventEmitted = true
		m.emitTaskEvent(TaskEvent{
			TaskID:     taskID,
			ProfileID:  taskKey,
			Phase:      "failed",
			Message:    "解析脚本执行结果失败或浏览器连接断开",
			StartedAt:  startedAt.Format(time.RFC3339),
			FinishedAt: time.Now().Format(time.RFC3339),
			DurationMs: durationMs,
		})
		stderrPreview := taskOutputPreview(errBuf.Bytes())
		if stderrPreview != "" {
			return "", taskRunnerResponse{}, string(output), durationMs, fmt.Errorf(
				"解析自动化任务结果失败: %w；stderr: %s",
				parseErr,
				stderrPreview,
			)
		}
		return "", taskRunnerResponse{}, string(output), durationMs, fmt.Errorf("解析自动化任务结果失败: %w", parseErr)
	}
	if stderrText := taskOutputPreview(errBuf.Bytes()); stderrText != "" {
		runnerResp.Logs = append(runnerResp.Logs, taskRunnerLogEntry{
			Time:   time.Now().Format(time.RFC3339),
			Values: []any{"stderr", stderrText},
		})
	}

	eventEmitted = true
	m.emitTaskEvent(TaskEvent{
		TaskID:     taskID,
		ProfileID:  taskKey,
		Phase:      "completed",
		Message:    completeMessage,
		StartedAt:  runnerResp.StartedAt,
		FinishedAt: runnerResp.FinishedAt,
		DurationMs: durationMs,
	})

	return taskID, runnerResp, string(normalizedOutput), durationMs, nil
}

func parseTaskRunnerResponse(output []byte) (taskRunnerResponse, []byte, error) {
	normalized := bytes.TrimSpace(bytes.TrimPrefix(output, []byte{0xEF, 0xBB, 0xBF}))
	var response taskRunnerResponse
	if err := json.Unmarshal(normalized, &response); err == nil {
		return response, normalized, nil
	}

	lines := bytes.Split(normalized, []byte{'\n'})
	for index := len(lines) - 1; index >= 0; index-- {
		candidate := bytes.TrimSpace(bytes.TrimPrefix(lines[index], []byte{0xEF, 0xBB, 0xBF}))
		if len(candidate) == 0 || candidate[0] != '{' {
			continue
		}
		response = taskRunnerResponse{}
		if err := json.Unmarshal(candidate, &response); err == nil {
			return response, candidate, nil
		}
	}

	return taskRunnerResponse{}, normalized, fmt.Errorf("stdout 中未找到有效 JSON（%s）", taskOutputPreview(normalized))
}

func taskOutputPreview(output []byte) string {
	const maxPreviewBytes = 600
	normalized := strings.TrimSpace(string(output))
	if normalized == "" {
		return ""
	}
	normalized = strings.Join(strings.Fields(normalized), " ")
	if len(normalized) > maxPreviewBytes {
		return normalized[:maxPreviewBytes] + "..."
	}
	return normalized
}

func taskContextErrorMessage(err error, timeoutLimit time.Duration) string {
	if err == context.DeadlineExceeded {
		if timeoutText := formatTaskTimeout(timeoutLimit); timeoutText != "" {
			return fmt.Sprintf("自动化任务超时，已终止（上限 %s）", timeoutText)
		}
		return "自动化任务超时，已终止"
	}
	if err == context.Canceled {
		return "自动化任务已取消"
	}
	return err.Error()
}

func formatTaskTimeout(timeout time.Duration) string {
	if timeout <= 0 {
		return ""
	}
	if timeout >= time.Minute && timeout%time.Minute == 0 {
		return fmt.Sprintf("%d 分钟", int64(timeout/time.Minute))
	}
	if timeout >= time.Second && timeout%time.Second == 0 {
		return fmt.Sprintf("%d 秒", int64(timeout/time.Second))
	}
	return fmt.Sprintf("%d 毫秒", timeout.Milliseconds())
}

func (m *Manager) writeTaskPayload(payload taskRunnerPayload) (string, error) {
	tempDir := filepath.Join(m.runtimeRoot(), "tmp")
	if err := os.MkdirAll(tempDir, 0o755); err != nil {
		return "", fmt.Errorf("创建自动化任务临时目录失败: %w", err)
	}
	file, err := os.CreateTemp(tempDir, "task-*.json")
	if err != nil {
		return "", fmt.Errorf("创建自动化任务临时文件失败: %w", err)
	}
	defer file.Close()
	if err := json.NewEncoder(file).Encode(payload); err != nil {
		return "", fmt.Errorf("写入自动化任务 payload 失败: %w", err)
	}
	return file.Name(), nil
}
