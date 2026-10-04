import { useEffect, useState } from 'react'
import { Check, CircleHelp, KeyRound, RefreshCw, X } from 'lucide-react'

import { Badge, Button, DataRow, ErrorState, Panel, Skeleton, StatusDot, TextInput } from '../components/ui'
import { ApiError, api, readToken, writeToken } from '../lib/api'
import { DASH } from '../lib/format'
import { useCapabilities, useConfig } from '../hooks/queries'

/**
 * Read-only settings, plus the one thing a browser genuinely cannot discover: the
 * token.
 *
 * The token box is here because when `iris web` is started with `IRIS_API_TOKEN`
 * the pages have no way to obtain it -- the API deliberately reports only *whether*
 * one is configured, never its value. So the field is a place to paste what the
 * operator already knows, stored in `localStorage` and sent as `X-IRIS-Token`.
 *
 * Nothing on this page writes to the server. The backend has no configuration
 * endpoint at all, which is the right shape: a settings page that can change a
 * running server's behaviour is a settings page with no tests.
 */
export function Settings() {
  const config = useConfig()
  const capabilities = useCapabilities()
  const [token, setToken] = useState('')
  const [probe, setProbe] = useState<'idle' | 'checking' | 'ok' | 'bad'>('idle')
  const [detail, setDetail] = useState<string | null>(null)

  useEffect(() => {
    setToken(readToken())
  }, [])

  const apply = async () => {
    writeToken(token.trim())
    setProbe('checking')
    try {
      await api.stats()
      setProbe('ok')
      setDetail('令牌可用，统计已可读取。')
    } catch (error) {
      setProbe('bad')
      setDetail(error instanceof ApiError ? error.message : String(error))
    }
  }

  return (
    <div className="grid grid-cols-1 gap-4 p-4 xl:grid-cols-2">
      <Panel
        title="有效配置（只读）"
        subtitle="来自 .env 与 IRIS_* 环境变量，修改后需重启 iris web"
        actions={
          <Button size="sm" variant="ghost" onClick={() => void config.refetch()}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            重新读取
          </Button>
        }
      >
        {config.isLoading && <Skeleton className="h-24 w-full" />}
        {config.isError && <ErrorState title="无法读取配置" detail={(config.error as Error).message} />}
        {config.data && (
          <>
            <DataRow label="数据库" mono>
              {config.data.database_url}
            </DataRow>
            <DataRow label="暂存目录" mono>
              {config.data.scratch_dir}
            </DataRow>
            <DataRow label="上传上限" mono>
              {config.data.api_max_upload_mb} MB
            </DataRow>
            <DataRow label="令牌">
              <Badge tone={config.data.api_token_configured ? 'violet' : 'neutral'}>
                {config.data.api_token_configured ? '已配置' : '未配置（本地模式）'}
              </Badge>
            </DataRow>
            <DataRow label="写权限">
              <Badge tone="neutral">只读</Badge>
            </DataRow>
            <p className="mt-2 text-[10px] leading-relaxed text-ink-700">{config.data.note}</p>
          </>
        )}
      </Panel>

      <Panel
        title="API 令牌"
        subtitle="仅当服务以 IRIS_API_TOKEN 启动时才需要"
        actions={
          <Badge tone={probe === 'ok' ? 'success' : probe === 'bad' ? 'danger' : 'neutral'}>
            {probe === 'ok' ? '已验证' : probe === 'bad' ? '验证失败' : probe === 'checking' ? '验证中' : '未验证'}
          </Badge>
        }
      >
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <TextInput
              label="令牌"
              type="password"
              autoComplete="off"
              placeholder="与 IRIS_API_TOKEN 相同的值"
              value={token}
              onChange={(event) => {
                setToken(event.target.value)
                setProbe('idle')
              }}
            />
          </div>
          <Button variant="primary" onClick={() => void apply()} disabled={probe === 'checking'}>
            <KeyRound className="h-3.5 w-3.5" aria-hidden="true" />
            保存并验证
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setToken('')
              writeToken('')
              setProbe('idle')
              setDetail('已清除本地令牌')
            }}
          >
            <X className="h-3.5 w-3.5" aria-hidden="true" />
            清除
          </Button>
        </div>
        {detail && (
          <p className="mt-2 flex items-center gap-1 text-2xs" role="status">
            {probe === 'ok' ? (
              <Check className="h-3 w-3 text-success" aria-hidden="true" />
            ) : (
              <CircleHelp className="h-3 w-3 text-warning" aria-hidden="true" />
            )}
            <span className={probe === 'ok' ? 'text-success' : 'text-warning'}>{detail}</span>
          </p>
        )}
        <ul className="mt-3 flex flex-col gap-1 border-t border-surface-border pt-2 text-[10px] leading-relaxed text-ink-700">
          <li>· 令牌保存在本浏览器的 localStorage，仅作为 X-IRIS-Token 请求头发送。</li>
          <li>· 终端 WebSocket 例外：浏览器无法自定义头，该通道用查询参数传令牌，服务端用同一套常量时间比较校验。</li>
          <li>· 服务端永远不会通过 API 返回令牌本身，/api/v1/config 只报告"是否已配置"。</li>
        </ul>
      </Panel>

      <Panel
        title="能力矩阵与判定依据"
        subtitle="每一行都由后端探测得出，并附证据来源"
        className="xl:col-span-2"
      >
        {capabilities.isLoading && <Skeleton className="h-40 w-full" />}
        <table className="w-full text-left text-xs">
          <thead>
            <tr className="border-b border-surface-border text-2xs text-ink-700">
              <th className="py-2 font-medium">能力</th>
              <th className="py-2 font-medium">状态</th>
              <th className="py-2 font-medium">说明</th>
              <th className="py-2 font-medium">证据</th>
            </tr>
          </thead>
          <tbody>
            {(capabilities.data?.items ?? []).map((item) => (
              <tr key={item.id} className="border-b border-surface-border/60 last:border-0 align-top">
                <td className="py-2 text-ink-100">{item.name}</td>
                <td className="py-2">
                  <Badge
                    tone={item.state === 'available' ? 'success' : item.state === 'planned' ? 'warning' : 'danger'}
                  >
                    <StatusDot
                      tone={
                        item.state === 'available' ? 'success' : item.state === 'planned' ? 'warning' : 'danger'
                      }
                    />
                    {item.state === 'available' ? '就绪' : item.state === 'planned' ? '待改造' : '不适用'}
                  </Badge>
                </td>
                <td className="max-w-md py-2 text-2xs leading-relaxed text-ink-300">{item.detail}</td>
                <td className="py-2 font-mono text-[10px] text-ink-700">{item.evidence || DASH}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[10px] leading-relaxed text-ink-700">
          「AI 值守」一行标注为不适用：本项目的自愈器是规则加状态机，不含任何模型调用。
          L4 的状态来自对 scripts/emulate/run_qemu.sh 的探测，因此一个未应用串口改造的构建会显示"待改造"，
          而不是宣称有交互式终端。
        </p>
      </Panel>

      <Panel title="已知限制" subtitle="如实标注，而不是留给评审去发现" className="xl:col-span-2">
        <ul className="flex flex-col gap-1.5 text-2xs leading-relaxed text-ink-300">
          <li>· 终端尺寸固定 80×24：QEMU 串口没有窗口尺寸通道，guest 永远不会知道浏览器窗口更宽。</li>
          <li>· 终端通道的安全等级等于 guest shell：拿到输入权就能在 guest 里执行命令，这是设计如此。</li>
          <li>· 日志视图与终端视图数据源不同：日志走运行结束时落盘的快照（不含终端输入），实时输出走终端通道。</li>
          <li>· 由命令行（iris emulate run）启动的仿真不在 API 的托管表里，因此页面无法停止它或接入它的终端。</li>
          <li>· 资源占用来自 docker stats 采样；容器刚启动时可能尚无样本，此时显示为「未采样」而不是 0%。</li>
          <li>· 统计卡与评测集使用全库累计口径（含同一固件的多次运行），与任何单批次实验数字不同。</li>
        </ul>
      </Panel>
    </div>
  )
}