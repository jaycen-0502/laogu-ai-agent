import { useCallback, useEffect, useState } from 'react'
import { ConfirmModal, toast } from '../../../shared/components'
import type { SortOrder } from '../../../shared/components/Table'
import type { BrowserProxy } from '../types'
import { fetchBrowserProxies, fetchBrowserProxyGroups, saveBrowserProxies } from '../api'
import {
  buildChainImportCandidate,
  createInitialChainImportForm,
  ensureBuiltinProxies,
  toChainImportForm,
  toDisplayList,
  type ChainImportForm,
  type ProxyDisplayInfo,
} from './proxyPool/helpers'
import {
  ProxyPoolEditModal,
  ProxyPoolImportModal,
  ProxyPoolPreviewModal,
  type ProxyEditFormValue,
} from './proxyPool/ProxyPoolModals'
import { ProxyPoolHeader } from './proxyPool/ProxyPoolHeader'
import { ProxyPoolTableCard } from './proxyPool/ProxyPoolTableCard'
import { ProxyPoolCheckSettingsModal } from './proxyPool/ProxyPoolCheckSettingsModal'
import { ProxyCoreDownloadModal } from './proxyPool/ProxyCoreDownloadModal'
import { useProxySourceRefresh } from './proxyPool/useProxySourceRefresh'
import { useProxyImportFlow } from './proxyPool/useProxyImportFlow'
import { useProxyChecks } from './proxyPool/useProxyChecks'
import { useProxySelection } from './proxyPool/useProxySelection'
import { useProxyCheckSettingsModal } from './proxyPool/useProxyCheckSettingsModal'
import { useProxyGlobalRefreshConfig } from './proxyPool/useProxyGlobalRefreshConfig'
import { useProxyDeleteFlow } from './proxyPool/useProxyDeleteFlow'
import { useProxyCoreDownload } from './proxyPool/useProxyCoreDownload'
import { useProxyPoolFilter } from './proxyPool/useProxyPoolFilter'

export function ProxyPoolPage() {
  // ---------------- 安全验证逻辑 ----------------
  const [isUnlocked, setIsUnlocked] = useState(false)
  const [passwordInput, setPasswordInput] = useState('')
  
  // 管理员解锁密码
  const ACCESS_PASSWORD = 'laogu88888888' 

  const handleVerifyPassword = () => {
    if (passwordInput === ACCESS_PASSWORD) {
      toast.success('身份验证成功')
      setIsUnlocked(true)
      setPasswordInput('')
    } else {
      toast.error('密码错误，请重新输入')
    }
  }
  // ----------------------------------------------

  const [proxies, setProxies] = useState<BrowserProxy[]>([])
  const [displayList, setDisplayList] = useState<ProxyDisplayInfo[]>([])
  const [loading, setLoading] = useState(true)
  const {
    coreDownloadOpen,
    coreDownloadType,
    setCoreDownloadType,
    coreDownloadGOOS,
    setCoreDownloadGOOS,
    coreDownloadGOARCH,
    setCoreDownloadGOARCH,
    coreDownloadProxy,
    setCoreDownloadProxy,
    coreDownloadProgress,
    currentCoreStatus,
    downloadCoreStatus,
    downloadCoreStatusLoading,
    loadBrowserSettings,
    handleStartCoreDownload,
    openCoreDownload,
    closeCoreDownload,
  } = useProxyCoreDownload()
  const [groups, setGroups] = useState<string[]>([])

  const [filterProtocol, setFilterProtocol] = useState<string>('all')
  const [filterKeyword, setFilterKeyword] = useState('')
  const [filterGroup, setFilterGroup] = useState<string>('all')
  const [filterAvailableOnly, setFilterAvailableOnly] = useState(false)
  const [sortColumn, setSortColumn] = useState<string>('')
  const [sortOrder, setSortOrder] = useState<SortOrder>(undefined)

  const {
    checkSettingsOpen,
    setCheckSettingsOpen,
    checkSettings,
    setCheckSettings,
    checkTargetsText,
    setCheckTargetsText,
    savingCheckSettings,
    openCheckSettings,
    saveCheckSettings,
  } = useProxyCheckSettingsModal()

  const {
    globalAutoRefreshEnabled,
    setGlobalAutoRefreshEnabled,
    globalRefreshInterval,
    globalRefreshIntervalM,
    setGlobalRefreshIntervalM,
  } = useProxyGlobalRefreshConfig()

  const [editModalOpen, setEditModalOpen] = useState(false)
  const [editingProxy, setEditingProxy] = useState<BrowserProxy | null>(null)
  const [chainEditMode, setChainEditMode] = useState(false)
  const [chainEditForm, setChainEditForm] = useState<ChainImportForm>(() => createInitialChainImportForm())
  const [editForm, setEditForm] = useState<ProxyEditFormValue>({
    proxyName: '',
    proxyConfig: '',
    preferredKernel: 'auto',
    dnsServers: '',
    groupName: '',
  })
  const [saving, setSaving] = useState(false)
  const saveProxies = useCallback(async (list: BrowserProxy[]) => {
    await saveBrowserProxies(list)
    setProxies(list)
    setDisplayList(toDisplayList(list))
    const grps = await fetchBrowserProxyGroups()
    setGroups(grps)
  }, [])

  const {
    importModalOpen, setImportModalOpen, importMode, importUrl, importFetchProxyId, importResolvedUrl, importText,
    importDnsServers, importNamePrefix, importGroupName, chainImportText, directImportText,
    chainImportForm, directImportForm, previewModalOpen, setPreviewModalOpen, previewList, removedPreviewProxyNames,
    importing, fetchingImportUrl, canParseImport, setImportText, setImportDnsServers,
    setImportNamePrefix, setImportGroupName, setImportFetchProxyId, setChainImportText, setDirectImportText,
    setChainImportForm, setDirectImportForm, handleRemovePreviewProxy, updateChainImportHop,
    handleImportModeChange, handleFillChainTemplate, handleFillDirectTemplate, handleCopyChainTemplate,
    handleCopyDirectTemplate, handleApplyChainJSON, handleApplyDirectText, handleImportUrlChange,
    handleFetchImportURL, handleParseImport, handleConfirmImport,
  } = useProxyImportFlow({
    proxies,
    globalAutoRefreshEnabled,
    globalRefreshInterval,
    saveProxies,
  })

  const {
    hasURLImportSources,
    refreshingAllSources,
    refreshingSourceIds,
    refreshSingleSource,
    handleRefreshAllSources,
  } = useProxySourceRefresh({
    proxies,
    globalAutoRefreshEnabled,
    globalRefreshInterval,
    saveProxies,
  })

  const {
    latencyMap,
    latencyEngineMap,
    latencyErrorMap,
    testingAll,
    warmingBridgeIds,
    warmingAllBridges,
    setLatencyMap,
    setLatencyEngineMap,
    handleTestOne,
    handleTestAll,
    handleWarmupOne,
    handleWarmupAll,
  } = useProxyChecks({ proxies })

  const loadProxies = useCallback(async () => {
    setLoading(true)
    try {
      const [list, groupList] = await Promise.all([
        fetchBrowserProxies(),
        fetchBrowserProxyGroups(),
      ])
      const finalList = await ensureBuiltinProxies(list)
      setProxies(finalList)
      setDisplayList(toDisplayList(finalList))
      setGroups(groupList)

      setLatencyMap(prev => {
        const validIds = new Set(finalList.map(p => p.proxyId))
        const next: Record<string, number> = {}
        Object.entries(prev).forEach(([proxyId, latency]) => {
          if (validIds.has(proxyId)) next[proxyId] = latency
        })
        return next
      })

      setLatencyEngineMap(prev => {
        const validIds = new Set(finalList.map(p => p.proxyId))
        const next: Record<string, string> = {}
        Object.entries(prev).forEach(([proxyId, engine]) => {
          if (validIds.has(proxyId)) next[proxyId] = engine
        })
        return next
      })
    } catch (error: any) {
      toast.error(error?.message || '加载代理失败')
    } finally {
      setLoading(false)
    }
  }, [setLatencyEngineMap, setLatencyMap])

  useEffect(() => {
    void loadProxies()
    void loadBrowserSettings()
  }, [loadProxies, loadBrowserSettings])

  const { protocolOptions, filteredList } = useProxyPoolFilter({
    displayList,
    filterProtocol,
    filterKeyword,
    filterGroup,
    filterAvailableOnly,
    sortColumn,
    sortOrder,
    latencyMap,
    ipHealthMap: {}, // 传入空对象以匹配类型约束
  })

  const {
    selectedIds,
    selectedCount,
    allFilteredSelected,
    someFilteredSelected,
    batchDeleteConfirmOpen,
    setBatchDeleteConfirmOpen,
    handleToggleAll,
    handleToggleOne,
    handleBatchDeleteConfirm,
    removeSelectedId,
  } = useProxySelection({ proxies, filteredList, saveProxies })

  const updateChainEditHop = (hop: 'first' | 'second', field: keyof ChainImportForm['first'], value: string) => {
    setChainEditForm(prev => ({
      ...prev,
      [hop]: {
        ...prev[hop],
        [field]: value,
      },
    }))
  }

  const handleEdit = (record: ProxyDisplayInfo) => {
    const proxy = proxies.find(p => p.proxyId === record.proxyId)
    if (proxy) {
      setEditingProxy(proxy)
      setEditForm({
        proxyName: proxy.proxyName,
        proxyConfig: proxy.proxyConfig,
        preferredKernel: proxy.preferredKernel || 'auto',
        dnsServers: proxy.dnsServers || '',
        groupName: proxy.groupName || '',
      })
      const nextChainForm = toChainImportForm(proxy.proxyName, proxy.proxyConfig)
      if (nextChainForm) {
        setChainEditMode(true)
        setChainEditForm(nextChainForm)
      } else {
        setChainEditMode(false)
        setChainEditForm(createInitialChainImportForm())
      }
      setEditModalOpen(true)
    }
  }

  const handleSaveProxy = async () => {
    if (!editingProxy) return

    let nextProxyName = editForm.proxyName.trim()
    let nextProxyConfig = editForm.proxyConfig
    if (chainEditMode) {
      try {
        const candidate = buildChainImportCandidate(chainEditForm)
        nextProxyName = candidate.proxyName
        nextProxyConfig = candidate.proxyConfig
      } catch (error: any) {
        toast.error(error?.message || '链式代理配置无效')
        return
      }
    } else if (!nextProxyName) {
      toast.error('请输入代理名称')
      return
    }

    setSaving(true)
    try {
      const newProxies = proxies.map(p =>
        p.proxyId === editingProxy.proxyId
          ? {
            ...p,
            proxyName: nextProxyName,
            proxyConfig: nextProxyConfig,
            preferredKernel: editForm.preferredKernel === 'auto' ? undefined : editForm.preferredKernel,
            dnsServers: editForm.dnsServers.trim() || undefined,
            groupName: editForm.groupName.trim() || undefined,
          }
          : p
      )
      await saveProxies(newProxies)
      setEditModalOpen(false)
      toast.success('代理已更新')
    } catch (error: any) {
      toast.error(error?.message || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const {
    deleteConfirmOpen,
    setDeleteConfirmOpen,
    handleDeleteClick,
    handleDeleteConfirm,
  } = useProxyDeleteFlow({ proxies, saveProxies, removeSelectedId })

  // ---------------- 拦截未解锁画面 ----------------
  if (!isUnlocked) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="w-full max-w-md p-6 bg-white dark:bg-zinc-800 rounded-xl shadow-lg border border-zinc-200 dark:border-zinc-700 space-y-4">
          <div className="text-center space-y-1">
            <h3 className="text-lg font-bold text-zinc-900 dark:text-zinc-100">🔒 代理池配置安全验证</h3>
            <p className="text-sm text-zinc-500 dark:text-zinc-400">代理池包含核心网络与节点配置，请输入密码解锁：</p>
          </div>
          <div className="space-y-3">
            <input
              type="password"
              placeholder="请输入管理员密码"
              value={passwordInput}
              onChange={(e) => setPasswordInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleVerifyPassword()}
              autoFocus
              className="w-full px-4 py-2 border rounded-lg border-zinc-300 dark:border-zinc-600 bg-transparent text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-blue-500"
            />
            <button
              onClick={handleVerifyPassword}
              className="w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-700 text-white font-medium rounded-lg transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              解锁配置
            </button>
          </div>
        </div>
      </div>
    )
  }
  // ------------------------------------------------

  return (
    <div className="space-y-5 animate-fade-in">
      <ProxyPoolHeader
        currentConnectorStatus={currentCoreStatus?.message || '未知'}
        hasURLImportSources={hasURLImportSources}
        onOpenSettings={() => void openCheckSettings()}
        onOpenImport={() => setImportModalOpen(true)}
        onOpenCoreDownload={openCoreDownload}
        onRefreshAllSources={() => void handleRefreshAllSources()}
        onTestAll={() => void handleTestAll(filteredList)}
        refreshingAllSources={refreshingAllSources}
        testingAll={testingAll}
        totalCount={filteredList.length}
      />

      <ProxyPoolTableCard
        allFilteredSelected={allFilteredSelected}
        data={filteredList}
        filterGroup={filterGroup}
        filterKeyword={filterKeyword}
        filterProtocol={filterProtocol}
        filterAvailableOnly={filterAvailableOnly}
        globalAutoRefreshEnabled={globalAutoRefreshEnabled}
        globalRefreshInterval={globalRefreshInterval}
        globalRefreshIntervalM={globalRefreshIntervalM}
        groups={groups}
        ipHealthMap={{}}
        latencyMap={latencyMap}
        latencyEngineMap={latencyEngineMap}
        latencyErrorMap={latencyErrorMap}
        loading={loading}
        onClearFilters={() => {
          setFilterProtocol('all')
          setFilterKeyword('')
          setFilterGroup('all')
          setFilterAvailableOnly(false)
        }}
        onDelete={handleDeleteClick}
        onEdit={handleEdit}
        onFilterGroupChange={setFilterGroup}
        onFilterKeywordChange={setFilterKeyword}
        onFilterProtocolChange={setFilterProtocol}
        onFilterAvailableOnlyChange={setFilterAvailableOnly}
        onGlobalAutoRefreshEnabledChange={setGlobalAutoRefreshEnabled}
        onGlobalRefreshIntervalMChange={setGlobalRefreshIntervalM}
        onOpenBatchDelete={() => setBatchDeleteConfirmOpen(true)}
        onOpenIPHealthDetail={() => {}}
        onRefreshSingleSource={(sourceId) => void refreshSingleSource(sourceId, false)}
        onSort={({ column, order }) => {
          setSortColumn(column)
          setSortOrder(order)
        }}
        onTestOne={(record) => void handleTestOne(record)}
        onToggleAll={handleToggleAll}
        onToggleOne={handleToggleOne}
        onWarmupOne={(record) => void handleWarmupOne(record)}
        onWarmupSelected={() => void handleWarmupAll(filteredList.filter(item => selectedIds.has(item.proxyId)))}
        protocolOptions={protocolOptions}
        refreshingSourceIds={refreshingSourceIds}
        selectedCount={selectedCount}
        selectedIds={selectedIds}
        someFilteredSelected={someFilteredSelected}
        sortColumn={sortColumn}
        sortOrder={sortOrder}
        warmingAllBridges={warmingAllBridges}
        warmingBridgeIds={warmingBridgeIds}
      />

      <ProxyPoolImportModal
        open={importModalOpen}
        groups={groups}
        importMode={importMode}
        importUrl={importUrl}
        importFetchProxyId={importFetchProxyId}
        importResolvedUrl={importResolvedUrl}
        importText={importText}
        importDnsServers={importDnsServers}
        importNamePrefix={importNamePrefix}
        importGroupName={importGroupName}
        chainImportText={chainImportText}
        directImportText={directImportText}
        chainImportForm={chainImportForm}
        directImportForm={directImportForm}
        fetchingImportUrl={fetchingImportUrl}
        fetchProxyOptions={proxies.filter(proxy => proxy.proxyConfig.trim() && !proxy.proxyConfig.trim().toLowerCase().startsWith('direct://'))}
        canParseImport={canParseImport}
        onClose={() => setImportModalOpen(false)}
        onParse={handleParseImport}
        onFetchImportUrl={handleFetchImportURL}
        onImportModeChange={handleImportModeChange}
        onImportUrlChange={handleImportUrlChange}
        onImportFetchProxyIdChange={setImportFetchProxyId}
        onImportTextChange={setImportText}
        onImportDnsServersChange={setImportDnsServers}
        onImportNamePrefixChange={setImportNamePrefix}
        onImportGroupNameChange={setImportGroupName}
        onChainImportTextChange={setChainImportText}
        onDirectImportTextChange={setDirectImportText}
        onApplyChainJSON={handleApplyChainJSON}
        onApplyDirectText={handleApplyDirectText}
        onChainImportFormChange={(patch) => setChainImportForm((prev) => ({ ...prev, ...patch }))}
        onChainImportHopChange={updateChainImportHop}
        onFillChainTemplate={handleFillChainTemplate}
        onCopyChainTemplate={() => void handleCopyChainTemplate()}
        onFillDirectTemplate={handleFillDirectTemplate}
        onCopyDirectTemplate={() => void handleCopyDirectTemplate()}
        onDirectImportFormChange={(patch) => setDirectImportForm((prev) => ({ ...prev, ...patch }))}
      />

      <ProxyPoolPreviewModal
        open={previewModalOpen}
        importMode={importMode}
        importDnsServers={importDnsServers}
        previewList={previewList}
        removedPreviewProxyNames={removedPreviewProxyNames}
        importing={importing}
        onClose={() => setPreviewModalOpen(false)}
        onBack={() => {
          setPreviewModalOpen(false)
          setImportModalOpen(true)
        }}
        onConfirm={handleConfirmImport}
        onRemoveProxy={handleRemovePreviewProxy}
      />

      <ProxyPoolEditModal
        open={editModalOpen}
        saving={saving}
        groups={groups}
        editForm={editForm}
        chainEditMode={chainEditMode}
        chainEditForm={chainEditForm}
        onClose={() => setEditModalOpen(false)}
        onSave={handleSaveProxy}
        onChange={(patch) => setEditForm((prev) => ({ ...prev, ...patch }))}
        onChainEditFormChange={(patch) => setChainEditForm((prev) => ({ ...prev, ...patch }))}
        onChainEditHopChange={updateChainEditHop}
      />

      <ProxyPoolCheckSettingsModal
        open={checkSettingsOpen}
        checkSettings={checkSettings}
        checkTargetsText={checkTargetsText}
        saving={savingCheckSettings}
        onClose={() => setCheckSettingsOpen(false)}
        onSave={saveCheckSettings}
        onCheckSettingsChange={setCheckSettings}
        onCheckTargetsTextChange={setCheckTargetsText}
      />

      <ProxyCoreDownloadModal
        open={coreDownloadOpen}
        core={coreDownloadType}
        goos={coreDownloadGOOS}
        goarch={coreDownloadGOARCH}
        downloadProxy={coreDownloadProxy}
        progress={coreDownloadProgress}
        status={downloadCoreStatus}
        statusLoading={downloadCoreStatusLoading}
        onCoreChange={setCoreDownloadType}
        onGOOSChange={setCoreDownloadGOOS}
        onGOARCHChange={setCoreDownloadGOARCH}
        onDownloadProxyChange={setCoreDownloadProxy}
        onClose={closeCoreDownload}
        onStart={handleStartCoreDownload}
      />

      <ConfirmModal open={deleteConfirmOpen} onClose={() => setDeleteConfirmOpen(false)} onConfirm={handleDeleteConfirm}
        title="确认删除" content="确定要删除这个代理吗？此操作不可恢复。" confirmText="删除" danger />

      <ConfirmModal open={batchDeleteConfirmOpen} onClose={() => setBatchDeleteConfirmOpen(false)} onConfirm={handleBatchDeleteConfirm}
        title="批量删除" content={`确定要删除选中的 ${selectedCount} 个代理吗？此操作不可恢复。`} confirmText="删除" danger />
    </div>
  )
}