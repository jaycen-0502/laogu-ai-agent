import { AlertTriangle, Info, RefreshCw } from 'lucide-react'
import { Button, Card } from '../../../shared/components'
import type { ConsistencyDiagnosticItem } from '../utils/profileConsistencyDiagnostics'

interface ProfileConsistencyDiagnosticsProps {
  items: ConsistencyDiagnosticItem[]
  running: boolean
  checkedAt?: string
  onRun: () => void
}

export function ProfileConsistencyDiagnostics({ items, running, checkedAt, onRun }: ProfileConsistencyDiagnosticsProps) {
  const warningCount = items.filter(item => item.level === 'warning').length

  return (
    <Card
      title="一致性诊断"
      actions={(
        <Button variant="secondary" size="sm" onClick={onRun} loading={running}>
          <RefreshCw className="w-4 h-4" />
          运行诊断
        </Button>
      )}
    >
      <div className="flex flex-col gap-1 pb-3 border-b border-[var(--color-border-muted)]">
        <div className="text-sm text-[var(--color-text-secondary)]">
          仅提供配置提醒，不会修改参数、阻止保存或中断启动。
        </div>
        <div className="text-xs text-[var(--color-text-muted)]">
          {warningCount > 0 ? `${warningCount} 项提醒` : '当前没有高优先级提醒'}
          {checkedAt ? ` · 最近检查 ${checkedAt}` : ' · 代理出口和 Launch API 状态尚未主动读取'}
        </div>
      </div>

      {items.length > 0 ? (
        <div className="divide-y divide-[var(--color-border-muted)]">
          {items.map(item => {
            const warning = item.level === 'warning'
            const Icon = warning ? AlertTriangle : Info
            return (
              <div key={item.id} className="flex gap-3 py-3 first:pt-4 last:pb-0">
                <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${warning ? 'text-amber-600' : 'text-blue-600'}`} />
                <div className="min-w-0">
                  <div className="text-sm font-medium text-[var(--color-text-primary)]">{item.title}</div>
                  <div className="mt-0.5 text-xs leading-5 text-[var(--color-text-muted)]">{item.detail}</div>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="pt-4 text-sm text-[var(--color-text-muted)]">当前配置未发现需要提醒的一致性问题。</div>
      )}
    </Card>
  )
}
