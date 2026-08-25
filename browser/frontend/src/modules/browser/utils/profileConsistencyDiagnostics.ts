import type {
  BrowserCore,
  BrowserFingerprintCapabilityReport,
  ProxyLocationResolveResult,
} from '../types'
import type { LaunchServerInfo } from '../api/launch'
import { deserialize, validateFingerprintArgs } from './fingerprintSerializer'

export type ConsistencyDiagnosticLevel = 'warning' | 'info'

export interface ConsistencyDiagnosticItem {
  id: string
  level: ConsistencyDiagnosticLevel
  title: string
  detail: string
}

export interface ProfileConsistencyDiagnosticInput {
  fingerprintArgs: string[]
  launchArgs: string[]
  proxyMode: 'pool' | 'local'
  proxyId: string
  proxyName?: string
  proxyLocation?: ProxyLocationResolveResult | null
  cores: BrowserCore[]
  selectedCoreId: string
  fingerprintMatrix?: BrowserFingerprintCapabilityReport | null
  launchServer?: LaunchServerInfo | null
  hostPlatform?: string
}

function addItem(
  items: ConsistencyDiagnosticItem[],
  id: string,
  level: ConsistencyDiagnosticLevel,
  title: string,
  detail: string,
) {
  if (!items.some(item => item.id === id)) items.push({ id, level, title, detail })
}

function normalizePlatform(value: string): string {
  const normalized = value.trim().toLowerCase()
  if (normalized.includes('win')) return 'windows'
  if (normalized.includes('mac')) return 'macos'
  if (normalized.includes('linux') || normalized.includes('x11')) return 'linux'
  return normalized
}

function primaryLanguage(value: string): string {
  return value.split(',')[0]?.trim().toLowerCase() || ''
}

function majorVersion(value: string): number {
  const match = value.match(/\d+/)
  return match ? Number.parseInt(match[0], 10) : 0
}

function launchArgValue(args: string[], key: string): string {
  const normalizedKey = key.toLowerCase()
  for (let index = args.length - 1; index >= 0; index -= 1) {
    const arg = args[index].trim()
    const separator = arg.indexOf('=')
    const argKey = (separator >= 0 ? arg.slice(0, separator) : arg).toLowerCase()
    if (argKey === normalizedKey) return separator >= 0 ? arg.slice(separator + 1).trim() : 'true'
  }
  return ''
}

export function detectHostPlatform(): string {
  if (typeof navigator === 'undefined') return ''
  return normalizePlatform(`${navigator.platform || ''} ${navigator.userAgent || ''}`)
}

export function buildProfileConsistencyDiagnostics(
  input: ProfileConsistencyDiagnosticInput,
): ConsistencyDiagnosticItem[] {
  const items: ConsistencyDiagnosticItem[] = []
  const fingerprint = deserialize(input.fingerprintArgs || [])
  const validation = validateFingerprintArgs(input.fingerprintArgs || [])
  const launchArgs = (input.launchArgs || []).map(item => item.trim()).filter(Boolean)
  const usesProxy = input.proxyMode === 'local' || (!!input.proxyId && input.proxyId !== '__direct__')

  for (const issue of validation.issues) {
    if (issue.level === 'warning') {
      addItem(items, `fingerprint-${issue.message}`, 'warning', '指纹参数兼容性提醒', issue.message)
    }
  }

  if (fingerprint.lang && fingerprint.acceptLang) {
    const lang = fingerprint.lang.toLowerCase()
    const acceptLang = primaryLanguage(fingerprint.acceptLang)
    if (lang !== acceptLang) {
      addItem(items, 'language-accept-language', 'warning', '语言配置不一致', `主语言为 ${fingerprint.lang}，但 Accept-Language 首项为 ${fingerprint.acceptLang.split(',')[0]}。`)
    }
  }

  const hostPlatform = normalizePlatform(input.hostPlatform || '')
  const profilePlatform = normalizePlatform(fingerprint.platform || '')
  if (hostPlatform && profilePlatform && hostPlatform !== profilePlatform) {
    addItem(items, 'host-platform', 'warning', '平台画像与宿主系统不同', `当前应用运行于 ${hostPlatform}，Profile 配置为 ${profilePlatform}。这不会阻止保存，请确认所选内核支持该平台画像。`)
  }

  const configuredCore = input.selectedCoreId
    ? input.cores.find(core => core.coreId === input.selectedCoreId)
    : input.cores.find(core => core.isDefault)
  if (input.cores.length === 0) {
    addItem(items, 'core-missing', 'warning', '尚未配置浏览器内核', '当前没有可用内核。配置仍可保存，但启动 Profile 时可能失败。')
  } else if (input.selectedCoreId && !configuredCore) {
    addItem(items, 'core-not-found', 'warning', '所选浏览器内核不存在', `未找到内核 ${input.selectedCoreId}，可能已被删除或移动。`)
  }

  const configuredMajor = majorVersion(fingerprint.brandVersion || '')
  const runtimeMajor = Number(input.fingerprintMatrix?.chromeMajor || 0)
  if (configuredMajor && runtimeMajor && configuredMajor !== runtimeMajor) {
    addItem(items, 'browser-version', 'warning', '品牌版本与内核主版本不同', `Profile 配置主版本 ${configuredMajor}，所选内核检测为 Chrome ${runtimeMajor}。`)
  }

  const cores = Number.parseInt(fingerprint.hardwareConcurrency || '', 10)
  if (Number.isFinite(cores) && (cores <= 1 || cores > 32)) {
    addItem(items, 'hardware-concurrency', 'warning', 'CPU 核心数较少见', `当前配置为 ${cores} 核。此项只提醒，不会自动调整。`)
  }

  const resolution = fingerprint.resolution === 'custom' ? fingerprint.customResolution : fingerprint.resolution
  const resolutionMatch = resolution?.match(/^(\d+),(\d+)$/)
  if (resolutionMatch) {
    const width = Number(resolutionMatch[1])
    const height = Number(resolutionMatch[2])
    if (width < 800 || height < 600 || width > 7680 || height > 4320) {
      addItem(items, 'window-size', 'warning', '窗口尺寸超出常见桌面范围', `当前窗口尺寸为 ${width} x ${height}，请确认与实际显示环境兼容。`)
    }
  }

  if (usesProxy && fingerprint.webrtcPolicy === 'default_public_and_private_interfaces') {
    addItem(items, 'webrtc-private-interface', 'warning', 'WebRTC 允许私网接口', '当前 Profile 使用代理，但 WebRTC 策略允许公网和私网接口，可能暴露与代理出口不一致的本地网络信息。')
  }

  if (input.proxyMode === 'pool' && input.proxyId === '__direct__') {
    addItem(items, 'direct-location', 'info', '直连模式无法自动核对地区', '时区和语言仍由用户配置；诊断不会根据本机公网 IP 自动修改。')
  } else if (input.proxyMode === 'local') {
    addItem(items, 'local-proxy-location', 'info', '本地代理需要人工核对地区', '本地代理未进入代理池，当前诊断不会解析或回写它的出口地区。')
  } else if (usesProxy && !input.proxyLocation) {
    addItem(items, 'proxy-location-pending', 'info', '尚未检查代理出口定位', `可点击“运行诊断”只读获取${input.proxyName ? `“${input.proxyName}”` : '当前代理'}的地区、时区和语言建议。`)
  } else if (usesProxy && input.proxyLocation && !input.proxyLocation.ok) {
    addItem(items, 'proxy-location-failed', 'warning', '代理出口定位检查失败', input.proxyLocation.error || '未能读取代理出口地区。')
  } else if (usesProxy && input.proxyLocation?.ok) {
    const location = input.proxyLocation
    if (fingerprint.timezone && fingerprint.timezone !== 'system' && location.timezone && fingerprint.timezone !== location.timezone) {
      addItem(items, 'proxy-timezone', 'warning', '时区与代理出口建议不一致', `代理出口建议 ${location.timezone}，Profile 当前为 ${fingerprint.timezone}。`)
    }
    if (!fingerprint.timezone) {
      addItem(items, 'proxy-timezone-missing', 'info', '未显式设置 Profile 时区', `代理出口建议时区为 ${location.timezone || '未知'}。诊断不会自动应用。`)
    }
    if (fingerprint.lang && location.lang && fingerprint.lang.toLowerCase() !== location.lang.toLowerCase()) {
      addItem(items, 'proxy-language', 'warning', '语言与代理出口建议不一致', `代理出口建议 ${location.lang}，Profile 当前为 ${fingerprint.lang}。`)
    }
    if (!fingerprint.lang) {
      addItem(items, 'proxy-language-missing', 'info', '未显式设置 Profile 语言', `代理出口建议语言为 ${location.lang || '未知'}。诊断不会自动应用。`)
    }
    addItem(items, 'proxy-location-observed', 'info', '代理出口检查结果', `出口 ${location.ip || '-'}，地区 ${[location.country, location.region, location.city].filter(Boolean).join(' / ') || '-'}。`)
  }

  const managedArgs = launchArgs.filter(arg => /^(--user-data-dir|--proxy-server|--remote-debugging-(?:port|address|pipe))(?:=|$)/i.test(arg))
  if (managedArgs.length > 0) {
    addItem(items, 'managed-launch-args', 'warning', '高级参数包含托管项', `以下参数由后端启动流程管理，保存后可能被忽略或覆盖：${managedArgs.join('、')}`)
  }

  const launchLanguage = launchArgValue(launchArgs, '--lang')
  if (launchLanguage && fingerprint.lang && launchLanguage.toLowerCase() !== fingerprint.lang.toLowerCase()) {
    addItem(items, 'launch-language', 'warning', '高级参数与指纹语言冲突', `高级参数为 ${launchLanguage}，指纹配置为 ${fingerprint.lang}。`)
  }
  const launchTimezone = launchArgValue(launchArgs, '--timezone')
  if (launchTimezone && fingerprint.timezone && launchTimezone !== fingerprint.timezone) {
    addItem(items, 'launch-timezone', 'warning', '高级参数与指纹时区冲突', `高级参数为 ${launchTimezone}，指纹配置为 ${fingerprint.timezone}。`)
  }

  if (input.launchServer) {
    if (input.launchServer.host !== '127.0.0.1') {
      addItem(items, 'launch-server-host', 'warning', 'Launch API 未绑定本机回环地址', `当前监听地址为 ${input.launchServer.host}，预期为 127.0.0.1。`)
    }
    if (!input.launchServer.ready) {
      addItem(items, 'launch-server-not-ready', 'info', 'Launch API 当前未就绪', '配置仍可编辑和保存；启动实例前请确认本机 Launch API 已恢复。')
    }
    if (input.launchServer.apiAuth.requested && !input.launchServer.apiAuth.configured) {
      addItem(items, 'launch-server-auth', 'warning', 'Launch API 认证配置未完成', '已请求启用 API Key，但当前没有有效密钥。')
    }
  } else {
    addItem(items, 'launch-server-pending', 'info', '尚未读取 Launch API 状态', '点击“运行诊断”可只读检查监听地址、端口和认证状态。')
  }

  for (const warning of input.fingerprintMatrix?.warnings || []) {
    addItem(items, `matrix-${warning}`, 'warning', '内核适配提醒', warning)
  }

  return items.sort((left, right) => (left.level === right.level ? 0 : left.level === 'warning' ? -1 : 1))
}
