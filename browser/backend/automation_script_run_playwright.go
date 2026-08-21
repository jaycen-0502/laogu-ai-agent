package backend

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"time"

	"ant-chrome/backend/internal/automation"
	"ant-chrome/backend/internal/browser"
)

func automationSelectorProfileID(selector map[string]any) string {
	if selector == nil {
		return ""
	}

	profileID, _ := selector["profileId"].(string)
	return strings.TrimSpace(profileID)
}

func automationSelectorCode(selector map[string]any) string {
	if selector == nil {
		return ""
	}

	code, _ := selector["code"].(string)
	return strings.ToUpper(strings.TrimSpace(code))
}

// 提取多实例 Target Codes 列表 (兼容 targetCodes 数组、codes 数组与单值 code)
func automationSelectorCodes(selector map[string]any) []string {
	if selector == nil {
		return nil
	}

	var codes []string

	// 1. 优先尝试读取前端传来的 targetCodes (支持 []any 与 []string)
	if rawCodes, ok := selector["targetCodes"].([]any); ok {
		for _, item := range rawCodes {
			if str, ok := item.(string); ok {
				if trimmed := strings.ToUpper(strings.TrimSpace(str)); trimmed != "" {
					codes = append(codes, trimmed)
				}
			}
		}
	} else if rawCodesStr, ok := selector["targetCodes"].([]string); ok {
		for _, str := range rawCodesStr {
			if trimmed := strings.ToUpper(strings.TrimSpace(str)); trimmed != "" {
				codes = append(codes, trimmed)
			}
		}
	}

	// 2. 兼容读取 codes 字段 (如果 targetCodes 为空)
	if len(codes) == 0 {
		if rawCodes, ok := selector["codes"].([]any); ok {
			for _, item := range rawCodes {
				if str, ok := item.(string); ok {
					if trimmed := strings.ToUpper(strings.TrimSpace(str)); trimmed != "" {
						codes = append(codes, trimmed)
					}
				}
			}
		} else if rawCodesStr, ok := selector["codes"].([]string); ok {
			for _, str := range rawCodesStr {
				if trimmed := strings.ToUpper(strings.TrimSpace(str)); trimmed != "" {
					codes = append(codes, trimmed)
				}
			}
		}
	}

	// 3. 去重
	uniqueCodes := make([]string, 0, len(codes))
	seen := make(map[string]bool)
	for _, c := range codes {
		if !seen[c] {
			seen[c] = true
			uniqueCodes = append(uniqueCodes, c)
		}
	}
	codes = uniqueCodes

	// 4. 如果没有数组，尝试读取单个 code
	if len(codes) == 0 {
		if singleCode := automationSelectorCode(selector); singleCode != "" {
			codes = append(codes, singleCode)
		}
	}

	return codes
}

func cloneAutomationSelector(selector map[string]any) map[string]any {
	if selector == nil {
		return nil
	}

	cloned := make(map[string]any, len(selector))
	for key, value := range selector {
		cloned[key] = value
	}
	return cloned
}

func cloneAutomationParams(params map[string]any) map[string]any {
	if params == nil {
		return map[string]any{}
	}
	cloned := make(map[string]any, len(params))
	for key, value := range params {
		cloned[key] = value
	}
	return cloned
}

func automationSelectorParamsByCode(selector map[string]any) map[string]map[string]any {
	result := make(map[string]map[string]any)
	if selector == nil {
		return result
	}

	raw, ok := selector["profileParamsByCode"].(map[string]any)
	if !ok {
		return result
	}
	for rawCode, rawParams := range raw {
		code := strings.ToUpper(strings.TrimSpace(rawCode))
		params, ok := rawParams.(map[string]any)
		if code == "" || !ok {
			continue
		}
		result[code] = cloneAutomationParams(params)
	}
	return result
}

func mergeAutomationParams(base map[string]any, override map[string]any) map[string]any {
	merged := cloneAutomationParams(base)
	for key, value := range override {
		merged[key] = value
	}
	return merged
}

func (a *App) ensurePlaywrightTargetReady(selector map[string]any) (map[string]any, string, error) {
	normalized := cloneAutomationSelector(selector)
	profileID := automationSelectorProfileID(normalized)
	code := automationSelectorCode(normalized)
	if profileID == "" && code == "" {
		return normalized, "", nil
	}

	var (
		profile *browser.Profile
		err     error
	)
	switch {
	case profileID != "":
		profile, err = a.BrowserInstanceStart(profileID)
	case code != "":
		profile, err = a.BrowserInstanceStartByCode(code)
	}
	if err != nil {
		return nil, "", fmt.Errorf("预启动脚本目标实例失败: %w", err)
	}
	if profile == nil {
		return nil, "", fmt.Errorf("预启动脚本目标实例失败：未返回实例")
	}

	resolvedProfileID := strings.TrimSpace(profile.ProfileId)
	if resolvedProfileID != "" && normalized != nil {
		normalized["profileId"] = resolvedProfileID
	}

	resolvedCode := strings.ToUpper(strings.TrimSpace(profile.LaunchCode))
	if resolvedCode == "" {
		resolvedCode = code
	}
	if resolvedCode != "" && normalized != nil {
		normalized["code"] = resolvedCode
	}

	return normalized, resolvedProfileID, nil
}

func automationScriptTaskKey(scriptID string, selector map[string]any) string {
	if profileID := automationSelectorProfileID(selector); profileID != "" {
		return profileID
	}
	return "script:" + strings.TrimSpace(scriptID)
}

func (a *App) runPlaywrightScript(ctx context.Context, script automation.ScriptRecord, input automation.ScriptRunRequest) (string, string, string, string) {
	if ctx == nil {
		ctx = context.Background()
	}
	if a.automationMgr == nil {
		return "", "", "脚本执行失败", "automation runtime manager is not initialized"
	}
	if a.config == nil || !a.config.Automation.Enabled {
		return "", "", "脚本执行失败", "自动化支持尚未启用"
	}
	if err := ctx.Err(); err != nil {
		return "", "", "脚本执行失败", automationRunContextErrorMessage(err)
	}
	if err := a.automationMgr.EnsureInstalled(ctx); err != nil {
		return "", "", "脚本执行失败", err.Error()
	}
	if err := ctx.Err(); err != nil {
		return "", "", "脚本执行失败", automationRunContextErrorMessage(err)
	}

	state := a.automationMgr.CurrentState()
	if !state.Ready {
		return "", "", "脚本执行失败", "自动化运行时尚未就绪"
	}

	paramsText := resolveAutomationRunJSONText(input.ParamsText, script.ParamsText, input.UseScriptParams)

	selector, targetSummary, err := a.resolveAutomationEffectiveSelector(script, input, false)
	if err != nil {
		return "", "", "脚本执行失败", err.Error()
	}

	// 解析出所有的 Target Code（优先匹配 targetCodes 数组）
	targetCodes := automationSelectorCodes(selector)
	paramsByCode := automationSelectorParamsByCode(selector)

	// 如果多个实例 Code 存在，进入多窗口并发调度流程
	if len(targetCodes) > 1 {
		params, err := parseAutomationJSONObject(paramsText, false)
		if err != nil {
			return "", "", "脚本执行失败", err.Error()
		}

		baseURL, authHeader, authValue, err := a.automationDemoEndpoint()
		if err != nil {
			return "", "", "脚本执行失败", err.Error()
		}

		scriptPath, artifactDir, cleanup, err := a.preparePlaywrightScriptWorkspace(state.RuntimeDir, script)
		if err != nil {
			return "", "", "脚本执行失败", err.Error()
		}
		defer cleanup()

		var (
			wg         sync.WaitGroup
			mu         sync.Mutex
			results    []string
			logs       []string
			summaries  []string
			errorTexts []string
		)

		for idx, targetCode := range targetCodes {
			wg.Add(1)
			go func(code string, index int) {
				defer wg.Done()

				// 错峰启动，防止同时唤起多个浏览器导致占用卡死 (间隔 2 秒)
				if index > 0 {
					time.Sleep(time.Duration(index*2) * time.Second)
				}

				// 针对单个实例生成单独的 Selector
				instanceSelector := cloneAutomationSelector(selector)
				instanceSelector["code"] = code
				delete(instanceSelector, "targetCodes")
				delete(instanceSelector, "codes")
				delete(instanceSelector, "profileParamsByCode")

				instanceSelector, taskProfileID, err := a.ensurePlaywrightTargetReady(instanceSelector)
				if err != nil {
					mu.Lock()
					errorTexts = append(errorTexts, fmt.Sprintf("[%s] %v", code, err))
					mu.Unlock()
					return
				}

				taskResult, err := a.automationMgr.RunScriptTask(ctx, automation.ScriptTaskRequest{
					TaskKey:          automationScriptTaskKey(script.ID, instanceSelector),
					ScriptID:         script.ID,
					ScriptPath:       scriptPath,
					Selector:         instanceSelector,
					Params:           mergeAutomationParams(params, paramsByCode[code]),
					LaunchBaseURL:    baseURL,
					LaunchAuthHeader: authHeader,
					LaunchAuthValue:  authValue,
					ArtifactDir:      artifactDir,
					Timeout:          automationScriptRunTimeout(input),
				})

				mu.Lock()
				defer mu.Unlock()

				if err != nil {
					errorTexts = append(errorTexts, fmt.Sprintf("[%s] %v", code, err))
					return
				}

				if taskResult.TaskKey == "" && taskProfileID != "" {
					taskResult.TaskKey = taskProfileID
				}

				if taskResult.ResultText != "" {
					results = append(results, fmt.Sprintf("[%s]: %s", code, taskResult.ResultText))
				}
				if taskResult.LogText != "" {
					logs = append(logs, fmt.Sprintf("=== [%s] LOGS ===\n%s", code, taskResult.LogText))
				}
				if taskResult.Summary != "" {
					summaries = append(summaries, taskResult.Summary)
				}

				if !taskResult.OK {
					eText := strings.TrimSpace(taskResult.Error)
					if eText == "" {
						eText = "ok=false"
					}
					errorTexts = append(errorTexts, fmt.Sprintf("[%s] %s", code, eText))
				}
			}(targetCode, idx)
		}

		wg.Wait()

		finalResult := strings.Join(results, "\n")
		finalLog := strings.Join(logs, "\n\n")
		finalSummary := appendAutomationRunSummary(strings.Join(summaries, "; "), targetSummary)
		finalErr := strings.Join(errorTexts, " | ")

		if finalErr != "" && len(errorTexts) == len(targetCodes) {
			// 全部失败
			return finalResult, finalLog, "脚本执行失败", finalErr
		}

		return finalResult, finalLog, finalSummary, finalErr
	}

	// 单实例保持原有的线性逻辑不变
	selector, taskProfileID, err := a.ensurePlaywrightTargetReady(selector)
	if err != nil {
		return "", "", "脚本执行失败", err.Error()
	}
	if err := ctx.Err(); err != nil {
		return "", "", "脚本执行失败", automationRunContextErrorMessage(err)
	}
	params, err := parseAutomationJSONObject(paramsText, false)
	if err != nil {
		return "", "", "脚本执行失败", err.Error()
	}
	singleCode := automationSelectorCode(selector)
	params = mergeAutomationParams(params, paramsByCode[singleCode])
	delete(selector, "targetCodes")
	delete(selector, "codes")
	delete(selector, "profileParamsByCode")

	baseURL, authHeader, authValue, err := a.automationDemoEndpoint()
	if err != nil {
		return "", "", "脚本执行失败", err.Error()
	}

	scriptPath, artifactDir, cleanup, err := a.preparePlaywrightScriptWorkspace(state.RuntimeDir, script)
	if err != nil {
		return "", "", "脚本执行失败", err.Error()
	}
	defer cleanup()
	if err := ctx.Err(); err != nil {
		return "", "", "脚本执行失败", automationRunContextErrorMessage(err)
	}

	taskResult, err := a.automationMgr.RunScriptTask(ctx, automation.ScriptTaskRequest{
		TaskKey:          automationScriptTaskKey(script.ID, selector),
		ScriptID:         script.ID,
		ScriptPath:       scriptPath,
		Selector:         selector,
		Params:           params,
		LaunchBaseURL:    baseURL,
		LaunchAuthHeader: authHeader,
		LaunchAuthValue:  authValue,
		ArtifactDir:      artifactDir,
		Timeout:          automationScriptRunTimeout(input),
	})
	if err != nil {
		return "", "", "脚本执行失败", err.Error()
	}
	if taskResult.TaskKey == "" && taskProfileID != "" {
		taskResult.TaskKey = taskProfileID
	}
	if !taskResult.OK {
		errorText := strings.TrimSpace(taskResult.Error)
		if errorText == "" {
			errorText = "playwright script returned ok=false"
		}
		return taskResult.ResultText, taskResult.LogText, appendAutomationRunSummary(taskResult.Summary, targetSummary), errorText
	}
	return taskResult.ResultText, taskResult.LogText, appendAutomationRunSummary(taskResult.Summary, targetSummary), ""
}
