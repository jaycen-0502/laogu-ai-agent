import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { EventsOn } from "../../../wailsjs/runtime/runtime";
import { toast } from "../../../shared/components";
import { openCorePath } from "../api";
import {
  isAutomationAdminUnlocked,
  unlockAutomationAdmin,
} from "../adminAccess";
import {
  fetchAutomationActiveTasks,
  runAutomationScript,
  stopAutomationTasksByProfile,
} from "../automationScriptApi";
import {
  fetchAutomationAccountState,
  saveAutomationAccountKeywords,
} from "../automationAccountStateApi";
import {
  DUAL_INSTANCE_RUNTIME_SCRIPT_ID,
  applyAutomationScriptPublicAPIVariables,
  createAutomationScriptTargetSelector,
  normalizeAutomationScriptTargetSelector,
  resolveAutomationScriptPublicAPIConfig,
  type AutomationScriptPublicAPIConfig,
  type AutomationScriptRunRecord,
  type AutomationScriptTargetSelector,
} from "../automationScripts";
import { useAutomationDemoSession } from "../hooks/useAutomationDemoSession";
import { useAutomationScriptRunProfiles } from "./useAutomationScriptRunProfiles";
import type {
  AutomationScriptRunModalProps,
  RunVariableInputs,
} from "./AutomationScriptRunModal.types";
import {
  buildParamsTextFromPublicAPIRequest,
  buildPublicAPIVariableInputs,
  buildSelectableProfileOptions,
  buildTemplateProfileOptions,
  buildProfileKeywordParams,
  isPlaceholderSelectorText,
  mergeParamsTextWithProfileKeywords,
  parseProfileKeywordText,
  resolveInitialSelectorText,
  resolveRunnableSelectorText,
  resolveSelectorLaunchCode,
  validateJsonObjectText,
} from "./AutomationScriptRunModal.helpers";
import { AutomationScriptRunModalView } from "./AutomationScriptRunModalView";
import { AutomationAdminUnlockModal } from "./AutomationAdminUnlockModal";

export function AutomationScriptRunModal({
  open,
  script,
  dirty = false,
  onClose,
}: AutomationScriptRunModalProps) {
  const navigate = useNavigate();
  const [selectorText, setSelectorText] = useState("");
  const [paramsText, setParamsText] = useState("");
  const [variableInputs, setVariableInputs] = useState<RunVariableInputs>({});
  const [running, setRunning] = useState(false);
  const [activeAutomationProfileIds, setActiveAutomationProfileIds] = useState<string[]>([]);
  const [profileActionPendingIds, setProfileActionPendingIds] = useState<string[]>([]);
  const [profileKeywordTexts, setProfileKeywordTexts] = useState<Record<string, string>>({});
  const [profileKeywordsLoading, setProfileKeywordsLoading] = useState(true);
  const [advancedConfigUnlocked, setAdvancedConfigUnlocked] = useState(false);
  const [adminUnlockOpen, setAdminUnlockOpen] = useState(false);
  const [adminUnlockBusy, setAdminUnlockBusy] = useState(false);
  const legacyProfileKeywordsRef = useRef<Record<string, string>>({});
  const keywordLoadKeyRef = useRef("");
  const editedKeywordProfilesRef = useRef(new Set<string>());
  const keywordSaveTimersRef = useRef<Record<string, number>>({});
  const demoBusy = false;
  const [lastRun, setLastRun] = useState<AutomationScriptRunRecord | null>(
    null,
  );
  const [rotateSelector, setRotateSelector] =
    useState<AutomationScriptTargetSelector>(() =>
      createAutomationScriptTargetSelector(),
    );
  const {
    demoSession,
    setDemoSession,
    reloadDemoSession,
  } = useAutomationDemoSession({ enabled: open });
  const isDualInstanceRuntimeScript =
    script?.id === DUAL_INSTANCE_RUNTIME_SCRIPT_ID;
  const isManualTargetMode =
    !!script &&
    (script.targetConfig.mode === "manual" || script.targetConfig.mode === "existing");
  const usesStoredTargetConfig =
    !!script && !isManualTargetMode;
  const showsSelectorInput =
    !!script && !usesStoredTargetConfig && !isDualInstanceRuntimeScript;
  const publicAPIConfig = useMemo(
    () => (script ? resolveAutomationScriptPublicAPIConfig(script) : null),
    [script],
  );
  const publicAPIVariables = publicAPIConfig?.variables || [];
  const usedPublicAPIVariableNames = useMemo(() => {
    if (!publicAPIConfig) {
      return new Set<string>();
    }
    return new Set(
      applyAutomationScriptPublicAPIVariables(
        publicAPIConfig.requestBodyText,
        publicAPIConfig.variables,
        variableInputs,
      ).usedVariables,
    );
  }, [publicAPIConfig, variableInputs]);
  const hasPublicAPIVariables = publicAPIVariables.length > 0;
  const hasUnusedPublicAPIVariables =
    hasPublicAPIVariables &&
    publicAPIVariables.some(
      (variable) => !usedPublicAPIVariableNames.has(variable.name),
    );
  const {
    demoMode,
    setDemoMode,
    availableProfiles,
    templateProfiles,
    profilesLoading,
    selectedProfileId,
    setSelectedProfileId,
    createDraft,
    setCreateDraft,
    selectedProfile,
    selectorDetachedFromSelectedProfile,
    selectedLaunchCode,
    codeSuggestions,
    profileIdSuggestions,
    profileNameSuggestions,
    groupOptions,
    syncDemoSessionFromProfile,
    handleSelectedProfileChange,
    handleLaunchCodeChange,
    handleSelectorTextChange,
    handleRestoreSelectedProfileSelector,
  } = useAutomationScriptRunProfiles({
    open,
    script,
    isManualTargetMode,
    usesStoredTargetConfig,
    selectorText,
    setSelectorText,
    setDemoSession,
    reloadDemoSession,
  });

  const syncParamsFromPublicAPIVariables = (
    config: AutomationScriptPublicAPIConfig,
    inputs: RunVariableInputs,
    fallbackParamsText: string,
  ) => {
    const resolved = buildParamsTextFromPublicAPIRequest(
      config,
      inputs,
      fallbackParamsText,
    );
    setParamsText(resolved.paramsText);
    return resolved;
  };
  const updateVariableInput = (name: string, value: string) => {
    setVariableInputs((current) => {
      const nextInputs = {
        ...current,
        [name]: value,
      };
      if (publicAPIConfig) {
        syncParamsFromPublicAPIVariables(
          publicAPIConfig,
          nextInputs,
          script?.paramsText || paramsText,
        );
      }
      return nextInputs;
    });
  };
  const updateParamsText = (nextParamsText: string) => {
    setParamsText(nextParamsText);
  };
  const updateRotateSelector = (
    patch: Partial<AutomationScriptTargetSelector>,
  ) => {
    setRotateSelector((current) =>
      normalizeAutomationScriptTargetSelector({
        ...current,
        ...patch,
      }),
    );
  };
  const resolveParamsTextForRun = (): string => {
    if (!publicAPIConfig || !hasPublicAPIVariables) {
      return paramsText;
    }
    return syncParamsFromPublicAPIVariables(
      publicAPIConfig,
      variableInputs,
      script?.paramsText || paramsText,
    ).paramsText;
  };
  const paramsLabel = isDualInstanceRuntimeScript ? "启动配置" : "运行参数";
  const paramsFieldLabel = isDualInstanceRuntimeScript
    ? "浏览器列表 / 启动配置 JSON"
    : "运行参数 JSON";
  const paramsPlaceholder = isDualInstanceRuntimeScript
    ? `{
  "browsers": [
    { "code": "BUYER_001", "skipDefaultStartUrls": true },
    { "code": "BUYER_002", "skipDefaultStartUrls": true }
  ],
  "timeoutMs": 45000
}`
    : '{"startUrls":["https://example.com"]}';

  useEffect(() => {
    if (!open || !script) {
      return;
    }

    setAdvancedConfigUnlocked(isAutomationAdminUnlocked());
    setAdminUnlockOpen(false);

    const nextDemoSession = reloadDemoSession();
    const nextSelectorText = resolveInitialSelectorText(script, nextDemoSession);
    setSelectorText(nextSelectorText);
    const nextInputs = publicAPIConfig
      ? buildPublicAPIVariableInputs(publicAPIConfig)
      : {};
    setVariableInputs(nextInputs);
    if (publicAPIConfig && publicAPIConfig.variables.length > 0) {
      syncParamsFromPublicAPIVariables(
        publicAPIConfig,
        nextInputs,
        script.paramsText || "",
      );
    } else {
      updateParamsText(script.paramsText || "");
    }
    setLastRun(null);
    setCreateDraft({
      profileName: script.targetConfig.createNameTemplate || "",
      templateProfileId: script.targetConfig.templateSelector.profileId || "",
    });
    setSelectedProfileId(script.targetConfig.selector.profileId || "");
    setRotateSelector(
      normalizeAutomationScriptTargetSelector(script.targetConfig.selector),
    );
    setDemoMode(
      nextDemoSession.launchCode ||
        resolveSelectorLaunchCode(nextSelectorText)
        ? "select"
        : "create",
    );
    try {
      const saved = localStorage.getItem(`automation_profile_keywords_v1:${script.id}`);
      const parsed = saved ? JSON.parse(saved) : {};
      const legacy = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
      legacyProfileKeywordsRef.current = legacy;
      keywordLoadKeyRef.current = "";
      editedKeywordProfilesRef.current.clear();
      setProfileKeywordsLoading(true);
      setProfileKeywordTexts(legacy);
    } catch {
	  legacyProfileKeywordsRef.current = {};
	  keywordLoadKeyRef.current = "";
	  editedKeywordProfilesRef.current.clear();
	  setProfileKeywordsLoading(true);
      setProfileKeywordTexts({});
    }
  }, [open, script, publicAPIConfig]);

  useEffect(() => {
    if (!open || !script || availableProfiles.length === 0) {
	  if (open && script && !profilesLoading) {
		setProfileKeywordsLoading(false);
	  }
      return;
    }
    const loadKey = `${script.id}:${availableProfiles.map((item) => item.profileId).sort().join(",")}`;
    if (keywordLoadKeyRef.current === loadKey) {
      return;
    }
    keywordLoadKeyRef.current = loadKey;
    setProfileKeywordsLoading(true);
    let disposed = false;

    void Promise.all(
      availableProfiles.map(async (profile) => {
        const state = await fetchAutomationAccountState(profile.profileId, "generic", script.id);
        if (state.keywords.length > 0) {
          return [profile.profileId, state.keywords.join("\n")] as const;
        }
        const legacyText = String(legacyProfileKeywordsRef.current[profile.profileId] || "");
        const legacyKeywords = parseProfileKeywordText(legacyText);
        if (legacyKeywords.length > 0) {
          const migrated = await saveAutomationAccountKeywords(
            profile.profileId,
            "generic",
            script.id,
            legacyKeywords,
          );
          return [profile.profileId, migrated.keywords.join("\n")] as const;
        }
        return [profile.profileId, ""] as const;
      }),
    ).then(
      (entries) => {
        if (disposed) {
          return;
        }
        setProfileKeywordTexts((current) => {
          const next = { ...current };
          entries.forEach(([profileId, value]) => {
            if (!editedKeywordProfilesRef.current.has(profileId)) {
              next[profileId] = value;
            }
          });
          return next;
        });
		try {
		  localStorage.removeItem(`automation_profile_keywords_v1:${script.id}`);
		} catch {
		  // SQLite is authoritative even when WebView storage cannot be changed.
		}
		setProfileKeywordsLoading(false);
      },
      () => {
        if (!disposed) {
          keywordLoadKeyRef.current = "";
		  setProfileKeywordsLoading(false);
        }
      },
    );
    return () => {
      disposed = true;
    };
  }, [open, script?.id, availableProfiles, profilesLoading]);

  useEffect(() => () => {
    Object.values(keywordSaveTimersRef.current).forEach((timer) => window.clearTimeout(timer));
    keywordSaveTimersRef.current = {};
  }, []);

  const updateProfileKeywordText = (profileId: string, value: string) => {
    if (!script) {
      return;
    }
    editedKeywordProfilesRef.current.add(profileId);
    setProfileKeywordTexts((current) => ({ ...current, [profileId]: value }));
    const existingTimer = keywordSaveTimersRef.current[profileId];
    if (existingTimer) {
      window.clearTimeout(existingTimer);
    }
    keywordSaveTimersRef.current[profileId] = window.setTimeout(() => {
      delete keywordSaveTimersRef.current[profileId];
      void saveAutomationAccountKeywords(
        profileId,
        "generic",
        script.id,
        parseProfileKeywordText(value),
      ).catch(() => {
        // Keep the current edit in memory; a later edit will retry persistence.
      });
    }, 350);
  };

  const persistProfileKeywords = async (profileId: string, value: string) => {
    if (!script) {
      return;
    }
    const existingTimer = keywordSaveTimersRef.current[profileId];
    if (existingTimer) {
      window.clearTimeout(existingTimer);
      delete keywordSaveTimersRef.current[profileId];
    }
    await saveAutomationAccountKeywords(
      profileId,
      "generic",
      script.id,
      parseProfileKeywordText(value),
    );
  };

  useEffect(() => {
    if (!open) {
      setActiveAutomationProfileIds([]);
      setProfileActionPendingIds([]);
      return;
    }

    let disposed = false;
    const refreshActiveTasks = async () => {
      try {
        const tasks = await fetchAutomationActiveTasks();
        if (!disposed) {
          const activeProfileIds = Array.from(
            new Set(tasks.map((task) => task.profileId)),
          );
          setActiveAutomationProfileIds(activeProfileIds);
          setProfileActionPendingIds((current) =>
            current.filter((profileId) => !activeProfileIds.includes(profileId)),
          );
        }
      } catch {
        // The event stream will retry on the next task state change.
      }
    };

    void refreshActiveTasks();
    const off = EventsOn("automation:task:state", () => {
      void refreshActiveTasks();
    });
    return () => {
      disposed = true;
      off();
    };
  }, [open]);

  // 【解决方案修复点1】：解锁“关闭”拦截，不论 running / demoBusy 状态如何，允许用户随时关掉 Modal 避免页面死锁
  const handleClose = () => {
    onClose();
  };

  const buildRunTargetInput = (): Record<string, unknown> => {
    if (!script || isManualTargetMode) {
      return {};
    }
    if (script.targetConfig.mode === "create") {
      return {
        templateSelector: createDraft.templateProfileId
          ? { profileId: createDraft.templateProfileId }
          : {},
        createNameTemplate: createDraft.profileName.trim(),
      };
    }
    if (script.targetConfig.mode === "rotate") {
      return rotateSelector as unknown as Record<string, unknown>;
    }
    return {};
  };

  const validateRunTargetInput = (): string => {
    if (!script || isManualTargetMode) {
      return "";
    }
    if (script.targetConfig.mode === "create") {
      if (!createDraft.templateProfileId) {
        return "先选择一个模板实例";
      }
      if (!createDraft.profileName.trim()) {
        return "先输入新实例名称";
      }
    }
    if (script.targetConfig.mode === "rotate") {
      const selector = normalizeAutomationScriptTargetSelector(rotateSelector);
      if (
        !selector.code &&
        !selector.profileId &&
        !selector.profileName &&
        !selector.groupId &&
        selector.keywords.length === 0 &&
        selector.tags.length === 0
      ) {
        return "先填写至少一个轮询条件";
      }
    }
    return "";
  };

  const executeRun = async (
    nextSelectorText: string,
    nextParamsText: string,
    trackGlobalRunning = true,
  ) => {
    if (!script) {
      return;
    }

    const runnableSelectorText = usesStoredTargetConfig ? "" : nextSelectorText;
    const launchCode =
      script.type === "playwright-cdp" && !usesStoredTargetConfig
        ? resolveSelectorLaunchCode(runnableSelectorText)
        : "";

    if (trackGlobalRunning) {
      setRunning(true);
    }
    try {
      const run = await runAutomationScript({
        scriptId: script.id,
        selectorText: runnableSelectorText,
        targetInput: buildRunTargetInput(),
        paramsText: nextParamsText,
        useScriptSelector: usesStoredTargetConfig,
        useScriptParams: false,
        launchCode,
        startByCodeBeforeRun:
          script.type === "playwright-cdp" &&
          !usesStoredTargetConfig &&
          !!launchCode,
      });
      setLastRun(run);
      if (run.status === "success") {
        toast.success(run.summary || "脚本执行完成");
      } else {
        toast.error(run.error || run.summary || "脚本执行失败");
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "脚本执行失败";
      toast.error(message);
    } finally {
      if (trackGlobalRunning) {
        setRunning(false);
      }
    }
  };

  const handleStartProfileAutomation = async (profileId: string, launchCode: string) => {
    if (!script || profileActionPendingIds.includes(profileId)) {
      return;
    }
	if (profileKeywordsLoading) {
	  toast.warning("账号关键词正在从数据库加载，请稍候再运行");
	  return;
	}

    let nextParamsText = resolveParamsTextForRun();
    const paramsError = validateJsonObjectText(nextParamsText, paramsLabel, false);
    if (paramsError) {
      toast.warning(paramsError);
      return;
    }

    try {
      nextParamsText = mergeParamsTextWithProfileKeywords(
        nextParamsText,
        profileKeywordTexts[profileId] || "",
      );
    } catch (error: unknown) {
      toast.warning(error instanceof Error ? error.message : "关键词参数合并失败");
      return;
    }

    const nextSelectorText = JSON.stringify(
      { code: launchCode, targetCodes: [launchCode] },
      null,
      2,
    );
    setProfileActionPendingIds((current) => [...current, profileId]);
    try {
	  await persistProfileKeywords(profileId, profileKeywordTexts[profileId] || "");
      await executeRun(nextSelectorText, nextParamsText, false);
	} catch (error: unknown) {
	  toast.error(error instanceof Error ? error.message : "账号关键词保存失败");
    } finally {
      setProfileActionPendingIds((current) => current.filter((id) => id !== profileId));
    }
  };

  const handleStopProfileAutomation = async (profileId: string) => {
    if (profileActionPendingIds.includes(profileId)) {
      return;
    }
    setProfileActionPendingIds((current) => [...current, profileId]);
    try {
      const stopped = await stopAutomationTasksByProfile(profileId);
      setActiveAutomationProfileIds((current) => current.filter((id) => id !== profileId));
      if (stopped > 0) {
        toast.success("已停止该实例的 Playwright 脚本");
      } else {
        toast.warning("该实例当前没有运行中的 Playwright 脚本");
      }
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "停止脚本失败");
    } finally {
      setProfileActionPendingIds((current) => current.filter((id) => id !== profileId));
    }
  };

  const handleRun = async () => {
    if (!script) {
      return;
    }
	if (profileKeywordsLoading) {
	  toast.warning("账号关键词正在从数据库加载，请稍候再运行");
	  return;
	}

    let nextSelectorText = usesStoredTargetConfig
      ? ""
      : resolveRunnableSelectorText(
          script,
          selectorText,
          demoSession,
        );
	const keywordSaves: Promise<void>[] = [];

    // 【解决方案修复点2】：注入多实例 targetCodes 支持，确保多进程并发生效
    try {
      if (nextSelectorText && !usesStoredTargetConfig) {
        const parsed = JSON.parse(nextSelectorText);
        let codes: string[] = [];
        if (Array.isArray(parsed.targetCodes) && parsed.targetCodes.length > 0) {
          codes = parsed.targetCodes;
        } else if (parsed.code) {
          codes = [parsed.code];
        } else if (selectedProfile?.launchCode) {
          codes = [selectedProfile.launchCode];
        }

        if (codes.length > 0) {
          parsed.targetCodes = Array.from(new Set(codes));
          parsed.code = parsed.targetCodes[0];
          const profileParamsByCode: Record<string, Record<string, unknown>> = {};
          for (const code of parsed.targetCodes as string[]) {
            const profile = availableProfiles.find(
              (item) => item.launchCode.toUpperCase() === String(code).toUpperCase(),
            );
            if (!profile) {
              continue;
            }
            const keywordParams = buildProfileKeywordParams(
              profileKeywordTexts[profile.profileId] || "",
            );
			keywordSaves.push(
			  persistProfileKeywords(
				profile.profileId,
				profileKeywordTexts[profile.profileId] || "",
			  ),
			);
            if (Object.keys(keywordParams).length > 0) {
              profileParamsByCode[profile.launchCode] = keywordParams;
            }
          }
          if (Object.keys(profileParamsByCode).length > 0) {
            parsed.profileParamsByCode = profileParamsByCode;
          } else {
            delete parsed.profileParamsByCode;
          }
          nextSelectorText = JSON.stringify(parsed, null, 2);
        }
      }
    } catch {
      // 容错处理：若为非标准 JSON 则退回默认逻辑
    }

	try {
	  await Promise.all(keywordSaves);
	} catch (error: unknown) {
	  toast.error(error instanceof Error ? error.message : "账号关键词保存失败");
	  return;
	}

    const selectorError = usesStoredTargetConfig
      ? ""
      : validateJsonObjectText(
          nextSelectorText,
          "目标选择器",
          script.type === "launch-api" &&
            !usesStoredTargetConfig &&
            !isDualInstanceRuntimeScript,
        );
    if (selectorError) {
      toast.warning(selectorError);
      return;
    }

    const nextParamsText = resolveParamsTextForRun();
    const paramsError = validateJsonObjectText(nextParamsText, paramsLabel, false);
    if (paramsError) {
      toast.warning(paramsError);
      return;
    }

    const targetInputError = validateRunTargetInput();
    if (targetInputError) {
      toast.warning(targetInputError);
      return;
    }

    if (
      script.type === "playwright-cdp" &&
      !usesStoredTargetConfig &&
      isPlaceholderSelectorText(nextSelectorText)
    ) {
      if (demoMode === "select" && selectedProfile) {
        nextSelectorText = JSON.stringify(
          {
            code: selectedProfile.launchCode,
            targetCodes: [selectedProfile.launchCode],
          },
          null,
          2,
        );
        setSelectorText(nextSelectorText);
        syncDemoSessionFromProfile(selectedProfile, "选择实例");
        toast.success("已自动回填所选实例 selector");
      } else {
        toast.warning(
          demoMode === "create"
            ? "先创建一个实例，或填入可用 code"
            : "先选择实例，或填入可用 Code",
        );
        return;
      }
    }

    if (nextSelectorText !== selectorText) {
      setSelectorText(nextSelectorText);
    }
    if (
      script.type === "playwright-cdp" &&
      !usesStoredTargetConfig &&
      demoMode === "select" &&
      selectedProfile &&
      !selectorDetachedFromSelectedProfile
    ) {
      syncDemoSessionFromProfile(selectedProfile, "选择实例");
    }

    await executeRun(nextSelectorText, nextParamsText);
  };

  const handlePrimaryAction = async () => {
    if (!script) {
      return;
    }
    await handleRun();
  };

  const handleOpenScriptDetail = () => {
    if (!script) {
      return;
    }
    onClose();
    navigate(`/browser/automation/${script.id}`);
  };

  const handleUnlockAdvancedConfig = () => {
    setAdminUnlockOpen(true);
  };

  const handleAdminUnlockSubmit = async (password: string) => {
    setAdminUnlockBusy(true);
    try {
      if (!(await unlockAutomationAdmin(password))) {
        toast.error("管理员密码错误");
        return;
      }
      setAdvancedConfigUnlocked(true);
      setAdminUnlockOpen(false);
      toast.success("高级配置已解锁");
    } catch (error: unknown) {
      toast.error(error instanceof Error ? error.message : "管理员验证失败");
    } finally {
      setAdminUnlockBusy(false);
    }
  };

  const handleOpenOutputPath = async (path: string) => {
    try {
      await openCorePath(path);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : "打开目录失败";
      toast.error(message);
    }
  };

  if (!script) {
    return null;
  }

  const launchApiExecutable = script.status !== "disabled";
  const showDemoProfilePicker =
    script.type === "playwright-cdp" && !usesStoredTargetConfig;
  const selectableProfileOptions = buildSelectableProfileOptions(availableProfiles);
  const templateProfileOptions = buildTemplateProfileOptions(templateProfiles);
  const viewProps = {
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
    selectedProfileId,
    selectedProfile,
    selectedLaunchCode,
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
    handleLaunchCodeChange,
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
  };

  return (
    <>
      <AutomationScriptRunModalView {...viewProps} />
      <AutomationAdminUnlockModal
        open={adminUnlockOpen}
        busy={adminUnlockBusy}
        onClose={() => {
          if (!adminUnlockBusy) {
            setAdminUnlockOpen(false);
          }
        }}
        onSubmit={handleAdminUnlockSubmit}
      />
    </>
  );
}
