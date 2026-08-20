import { useMemo, type Dispatch, type SetStateAction } from "react";
import { FileText, LockKeyhole, Play, Square } from "lucide-react";
import {
  Badge,
  Button,
  FormItem,
  Input,
  Modal,
  Textarea,
} from "../../../shared/components";
import {
  describeAutomationScriptTargetConfig,
  getAutomationScriptTypeLabel,
  type AutomationScriptRecord,
  type AutomationScriptRunRecord,
  type AutomationScriptTargetSelector,
} from "../automationScripts";
import { TargetSelectorEditor } from "../pages/automationScriptDetail/shared";
import type { SelectorSuggestion } from "../pages/automationScriptDetail/helpers";
import { AutomationInstanceSelector } from "./AutomationInstanceSelector";
import { AutomationScriptRunResultPanel } from "./AutomationScriptRunResultPanel";
import type { DemoCreateDraft, DemoPreparationMode, RunVariableInputs, SelectableProfile } from "./AutomationScriptRunModal.types";
import { formatDateTime } from "./AutomationScriptRunModal.helpers";

type Option = { value: string; label: string };

interface AutomationScriptRunModalViewProps {
  open: boolean;
  dirty: boolean;
  script: AutomationScriptRecord;
  running: boolean;
  demoBusy: boolean;
  launchApiExecutable: boolean;
  showDemoProfilePicker: boolean;
  isManualTargetMode: boolean;
  usesStoredTargetConfig: boolean;
  isDualInstanceRuntimeScript: boolean;
  selectorDetachedFromSelectedProfile: boolean;
  showsSelectorInput: boolean;
  hasPublicAPIVariables: boolean;
  hasUnusedPublicAPIVariables: boolean;
  profilesLoading: boolean;
  selectedProfileId: string;
  selectedProfile: SelectableProfile | null;
  selectedLaunchCode: string;
  selectorText: string;
  paramsText: string;
  paramsFieldLabel: string;
  paramsPlaceholder: string;
  demoMode: DemoPreparationMode;
  createDraft: DemoCreateDraft;
  rotateSelector: AutomationScriptTargetSelector;
  variableInputs: RunVariableInputs;
  publicAPIVariables: Array<{ name: string; description?: string; defaultValue?: string }>;
  selectableProfileOptions: Option[];
  templateProfileOptions: Option[];
  codeSuggestions: SelectorSuggestion[];
  profileIdSuggestions: SelectorSuggestion[];
  profileNameSuggestions: SelectorSuggestion[];
  groupOptions: Option[];
  lastRun: AutomationScriptRunRecord | null;
  activeAutomationProfileIds: string[];
  profileActionPendingIds: string[];
  profileKeywordTexts: Record<string, string>;
  advancedConfigUnlocked: boolean;
  handleClose: () => void;
  handleOpenScriptDetail: () => void;
  handleUnlockAdvancedConfig: () => void;
  handlePrimaryAction: () => Promise<void>;
  handleSelectedProfileChange: (profileId: string) => void;
  handleLaunchCodeChange: (code: string) => void;
  handleRestoreSelectedProfileSelector: () => void;
  handleSelectorTextChange: (value: string) => void;
  handleOpenOutputPath: (path: string) => Promise<void>;
  handleStartProfileAutomation: (profileId: string, launchCode: string) => Promise<void>;
  handleStopProfileAutomation: (profileId: string) => Promise<void>;
  updateProfileKeywordText: (profileId: string, value: string) => void;
  setCreateDraft: Dispatch<SetStateAction<DemoCreateDraft>>;
  updateVariableInput: (name: string, value: string) => void;
  updateParamsText: (value: string) => void;
  updateRotateSelector: (patch: Partial<AutomationScriptTargetSelector>) => void;
}

export function AutomationScriptRunModalView({
  open,
  dirty,
  script,
  running,
  demoBusy,
  launchApiExecutable,
  showDemoProfilePicker,
  isManualTargetMode,
  usesStoredTargetConfig,
  isDualInstanceRuntimeScript,
  selectorDetachedFromSelectedProfile,
  showsSelectorInput,
  hasPublicAPIVariables,
  hasUnusedPublicAPIVariables,
  profilesLoading,
  selectedProfileId: _selectedProfileId,
  selectedProfile,
  selectedLaunchCode: _selectedLaunchCode,
  selectorText,
  paramsText,
  paramsFieldLabel,
  paramsPlaceholder,
  demoMode,
  createDraft,
  rotateSelector,
  variableInputs,
  publicAPIVariables,
  selectableProfileOptions,
  templateProfileOptions,
  codeSuggestions,
  profileIdSuggestions,
  profileNameSuggestions,
  groupOptions,
  lastRun,
  activeAutomationProfileIds,
  profileActionPendingIds,
  profileKeywordTexts,
  advancedConfigUnlocked,
  handleClose,
  handleOpenScriptDetail,
  handleUnlockAdvancedConfig,
  handlePrimaryAction,
  handleSelectedProfileChange,
  handleLaunchCodeChange: _handleLaunchCodeChange,
  handleRestoreSelectedProfileSelector,
  handleSelectorTextChange,
  handleOpenOutputPath,
  handleStartProfileAutomation,
  handleStopProfileAutomation,
  updateProfileKeywordText,
  setCreateDraft,
  updateVariableInput,
  updateParamsText,
  updateRotateSelector,
}: AutomationScriptRunModalViewProps) {

  // 解析当前 JSON 里面选中的多实例 Code 列表
  const currentSelectedCodes = useMemo(() => {
    try {
      if (!selectorText) return [];
      const parsed = JSON.parse(selectorText);
      if (Array.isArray(parsed.targetCodes) && parsed.targetCodes.length > 0) {
        return parsed.targetCodes as string[];
      }
      if (parsed.code) {
        return [parsed.code as string];
      }
    } catch {
      // ignore
    }
    return [];
  }, [selectorText]);

  // 更新勾选的多实例并发列表并同步更新 Selector JSON
  const handleToggleProfileCode = (code: string) => {
    let nextCodes: string[];
    if (currentSelectedCodes.includes(code)) {
      nextCodes = currentSelectedCodes.filter((c) => c !== code);
    } else {
      nextCodes = [...currentSelectedCodes, code];
    }

    const payload = {
      targetCodes: nextCodes,
      code: nextCodes[0] || "",
    };

    handleSelectorTextChange(JSON.stringify(payload, null, 2));

    // 如果选了环境，同步选中首个 Profile ID
    if (nextCodes.length > 0) {
      const targetOpt = selectableProfileOptions.find((opt) =>
        opt.label.includes(nextCodes[0])
      );
      if (targetOpt) {
        handleSelectedProfileChange(targetOpt.value);
      }
    }
  };

  // 全选/反选所有实例
  const handleSelectAllProfiles = (selectAll: boolean) => {
    if (!selectAll) {
      handleSelectorTextChange(JSON.stringify({ targetCodes: [], code: "" }, null, 2));
      return;
    }
    const allCodes = selectableProfileOptions
      .map((opt) => {
        const match = opt.label.match(/^([A-Za-z0-9_-]+)/);
        return match ? match[1] : opt.value;
      })
      .filter(Boolean);

    const payload = {
      targetCodes: allCodes,
      code: allCodes[0] || "",
    };
    handleSelectorTextChange(JSON.stringify(payload, null, 2));
  };

  return (
    <Modal
      open={open}
      onClose={handleClose}
      title="执行脚本"
      width="880px"
      footer={
        <>
          <Button
            variant="secondary"
            onClick={handleClose}
            /* 【方案 B 关键点】允许强行点击关闭，不再被 running 截胡 */
          >
            关闭
          </Button>
          <Button
            onClick={() => void handlePrimaryAction()}
            loading={running}
            disabled={!launchApiExecutable || demoBusy}
          >
            <Play className="h-4 w-4" />
            立即执行
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--color-border-muted)] pb-3">
          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-center gap-2">
              <div className="max-w-[26rem] truncate text-sm font-semibold text-[var(--color-text-primary)]">
                {script.name}
              </div>
              <span className="text-xs text-[var(--color-text-muted)]">
                {formatDateTime(script.updatedAt)}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Badge
                variant={script.type === "launch-api" ? "info" : "default"}
                size="sm"
              >
                {getAutomationScriptTypeLabel(script.type)}
              </Badge>
              <Badge
                variant={
                  script.status === "ready"
                    ? "success"
                    : script.status === "disabled"
                      ? "default"
                      : "warning"
                }
                size="sm"
                dot
              >
                {script.status === "ready"
                  ? "可用"
                  : script.status === "disabled"
                    ? "停用"
                    : "草稿"}
              </Badge>
            </div>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={
              advancedConfigUnlocked
                ? handleOpenScriptDetail
                : handleUnlockAdvancedConfig
            }
            disabled={running || demoBusy}
          >
            {advancedConfigUnlocked ? (
              <FileText className="h-4 w-4" />
            ) : (
              <LockKeyhole className="h-4 w-4" />
            )}
            {advancedConfigUnlocked ? "脚本详情" : "管理员设置"}
          </Button>
        </div>

        {dirty && advancedConfigUnlocked && (
          <div className="rounded-xl border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/10 px-4 py-3 text-sm text-[var(--color-text-secondary)]">
            {isDualInstanceRuntimeScript
              ? "当前详情页还有未保存修改。本次执行只使用弹窗里的启动配置，不会自动保存页面内容。"
              : "当前详情页还有未保存修改。本次执行只使用弹窗里的 selector / params，不会自动保存页面内容。"}
          </div>
        )}

        {usesStoredTargetConfig && (
          <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-3 text-sm text-[var(--color-text-secondary)]">
            <div>
              {describeAutomationScriptTargetConfig(script.targetConfig)}
            </div>
            <div className="mt-2 text-xs text-[var(--color-text-muted)]">
              本次执行沿用脚本配置的实例策略，只填写本策略需要的执行配置。
            </div>
          </div>
        )}

        {/* 【方案 B 关键点】手动/实例选择增加多选并发勾选面板 */}
        {(showDemoProfilePicker && isManualTargetMode) ||
        (showDemoProfilePicker && !isManualTargetMode && demoMode === "select") ? (
          <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-sm font-semibold text-[var(--color-text-primary)]">
                并发实例选择 (可勾选多个)
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => handleSelectAllProfiles(true)}
                  disabled={running || demoBusy || selectableProfileOptions.length === 0}
                >
                  全选
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => handleSelectAllProfiles(false)}
                  disabled={running || demoBusy || currentSelectedCodes.length === 0}
                >
                  清空
                </Button>
              </div>
            </div>

            {profilesLoading ? (
              <div className="text-xs text-[var(--color-text-muted)]">实例加载中...</div>
            ) : selectableProfileOptions.length === 0 ? (
              <div className="text-xs text-[var(--color-text-muted)]">暂无可选实例</div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-h-52 overflow-y-auto pr-1">
                {selectableProfileOptions.map((opt) => {
                  const match = opt.label.match(/^([A-Za-z0-9_-]+)/);
                  const code = match ? match[1] : opt.value;
                  const isChecked = currentSelectedCodes.includes(code);
                  const isAutomationRunning = activeAutomationProfileIds.includes(opt.value);
                  const isActionPending = profileActionPendingIds.includes(opt.value);

                  return (
                    <div
                      key={opt.value}
                      className={`space-y-2 p-2 rounded-lg border text-xs transition-colors ${
                        isChecked
                          ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10 text-[var(--color-text-primary)] font-medium"
                          : "border-[var(--color-border-muted)] bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]"
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                          <input
                            type="checkbox"
                            checked={isChecked}
                            onChange={() => handleToggleProfileCode(code)}
                            disabled={running || demoBusy}
                            className="rounded border-[var(--color-border-default)]"
                          />
                          <span className="truncate">{opt.label}</span>
                        </label>
                        {!isManualTargetMode ? null : isAutomationRunning ? (
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => void handleStopProfileAutomation(opt.value)}
                            loading={isActionPending}
                            disabled={demoBusy}
                            title="只停止此实例的 Playwright 脚本"
                          >
                            <Square className="h-3.5 w-3.5" />
                            停止脚本
                          </Button>
                        ) : (
                          <Button
                            size="sm"
                            onClick={() => void handleStartProfileAutomation(opt.value, code)}
                            loading={isActionPending}
                            disabled={!launchApiExecutable || demoBusy || running}
                            title="只启动此实例的 Playwright 脚本"
                          >
                            <Play className="h-3.5 w-3.5" />
                            单独运行
                          </Button>
                        )}
                      </div>
                      {isManualTargetMode ? (
                        <Input
                          value={profileKeywordTexts[opt.value] || ""}
                          onChange={(event) =>
                            updateProfileKeywordText(opt.value, event.target.value)
                          }
                          placeholder="本账号关键词：AI, OpenAI, 创业"
                          disabled={demoBusy}
                          className="h-8 text-xs"
                          title="逗号、中文逗号、分号或换行分隔；留空沿用脚本原参数"
                        />
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}

            {advancedConfigUnlocked && selectorDetachedFromSelectedProfile ? (
              <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-[var(--color-border-muted)] bg-[var(--color-bg-secondary)] px-3 py-2 text-xs text-[var(--color-text-secondary)]">
                <span>当前 selector 已手动修改，执行以下方 JSON 为准。</span>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={handleRestoreSelectedProfileSelector}
                  disabled={running || demoBusy || !selectedProfile}
                >
                  恢复实例联动
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}

        {script.targetConfig.mode === "create" ? (
          <AutomationInstanceSelector
            title="模板创建"
            mode="create"
            modes={["create"]}
            loading={profilesLoading}
            disabled={running || demoBusy}
            createName={createDraft.profileName}
            templateProfileId={createDraft.templateProfileId}
            templateOptions={templateProfileOptions}
            templatePlaceholder="暂无模板"
            onCreateNameChange={(profileName) =>
              setCreateDraft((current) => ({
                ...current,
                profileName,
              }))
            }
            onTemplateChange={(templateProfileId) =>
              setCreateDraft((current) => ({
                ...current,
                templateProfileId,
              }))
            }
          />
        ) : null}

        {script.targetConfig.mode === "rotate" ? (
          <AutomationInstanceSelector
            title="条件轮询"
            mode="rotate"
            modes={["rotate"]}
            disabled={running || demoBusy}
            extra={
              <TargetSelectorEditor
                selector={rotateSelector}
                onChange={updateRotateSelector}
                codeSuggestions={codeSuggestions}
                profileIdSuggestions={profileIdSuggestions}
                profileNameSuggestions={profileNameSuggestions}
                groupOptions={groupOptions}
                disabled={running || demoBusy}
              />
            }
          />
        ) : null}

        {script.status === "disabled" ? (
          <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-4 text-sm text-[var(--color-text-secondary)]">
            该脚本当前处于停用状态，先把状态切回可用再执行。
          </div>
        ) : (
          <div className="space-y-3">
            {hasPublicAPIVariables ? (
              <div className="rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] px-4 py-3">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div className="text-sm font-semibold text-[var(--color-text-primary)]">
                    接口变量
                  </div>
                  {hasUnusedPublicAPIVariables ? (
                    <div className="text-xs text-[var(--color-text-muted)]">
                      未引用变量不生效
                    </div>
                  ) : null}
                </div>
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  {publicAPIVariables.map((variable) => (
                    <FormItem key={variable.name} label={variable.name}>
                      <Input
                        value={variableInputs[variable.name] || ""}
                        onChange={(event) =>
                          updateVariableInput(variable.name, event.target.value)
                        }
                        placeholder={variable.description || variable.defaultValue}
                        className="h-10 rounded-lg"
                        disabled={running || demoBusy}
                      />
                    </FormItem>
                  ))}
                </div>
              </div>
            ) : null}

            {advancedConfigUnlocked ? <div
              className={
                showsSelectorInput
                  ? "grid grid-cols-1 gap-3 xl:grid-cols-2"
                  : "grid grid-cols-1 gap-3"
              }
            >
              {showsSelectorInput && (
                <FormItem label="目标选择器 JSON">
                  <Textarea
                    rows={hasPublicAPIVariables ? 6 : 9}
                    value={selectorText}
                    onChange={(event) => handleSelectorTextChange(event.target.value)}
                    className="font-mono text-xs"
                    placeholder='{"targetCodes":["VYOMZB","BUYER_002"]}'
                    disabled={running || demoBusy}
                  />
                </FormItem>
              )}

              <FormItem label={paramsFieldLabel}>
                <Textarea
                  rows={hasPublicAPIVariables ? 6 : 9}
                  value={paramsText}
                  onChange={(event) => updateParamsText(event.target.value)}
                  className="font-mono text-xs"
                  placeholder={paramsPlaceholder}
                  disabled={running || demoBusy}
                />
              </FormItem>
            </div> : (
              <div className="rounded-xl border border-[var(--color-border-muted)] bg-[var(--color-bg-secondary)] px-4 py-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium text-[var(--color-text-primary)]">
                      高级运行配置已隐藏
                    </div>
                    <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                      普通用户只需选择实例、填写关键词并运行；Selector 和参数 JSON 仅管理员可见。
                    </div>
                  </div>
                  <Button size="sm" variant="secondary" onClick={handleUnlockAdvancedConfig}>
                    <LockKeyhole className="h-4 w-4" />
                    输入密码查看
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}

        {lastRun && (
          <AutomationScriptRunResultPanel
            lastRun={lastRun}
            handleOpenOutputPath={handleOpenOutputPath}
          />
        )}
      </div>
    </Modal>
  );
}
