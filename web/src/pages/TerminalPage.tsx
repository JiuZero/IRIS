import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Terminal as XTerm } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { Keyboard, KeyboardOff, RefreshCw, TriangleAlert } from 'lucide-react'

import type { ITheme } from '@xterm/xterm'
import { Badge, Button, ErrorState, Panel } from '../components/ui'
import { terminalSocketUrl } from '../lib/api'
import { classNames } from '../lib/format'
import { useInstanceStats } from '../hooks/queries'
import { useAppearanceStore } from '../store/appearance'
import type { ClientFrame, HelloFrame, ServerTextFrame } from '../lib/types'

type LinkState = 'connecting' | 'open' | 'closed' | 'refused'

const CLOSE_REASON: Record<number, string> = {
  4404: '无权访问该实例的终端（不存在，或不属于当前调用方）',
  4403: '该实例没有可接入的串口（可能由命令行启动，或尚未发布端口）',
  1011: '串口通道不可用：容器内 QEMU 未在监听串口端口',
}

/** Read a token off `<html>`. The fallbacks are the `iris` theme's values, which
 *  are what an unresolved variable would have been anyway -- xterm rejects an
 *  empty colour string and renders nothing rather than degrading. */
function themeColour(name: string, fallback: string): string {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return value || fallback
}

/** The terminal's palette, resolved from the active theme rather than hard-coded.
 *  A console left at the dark palette on a light theme is a white rectangle with
 *  pale grey text in it, which is worse than no theming at all. */
function readTerminalTheme(): ITheme {
  return {
    background: themeColour('--surface-base', '#0a0d14'),
    foreground: themeColour('--text-primary', '#eef1f6'),
    cursor: themeColour('--iris-400', '#5b8dff'),
    selectionBackground: themeColour('--selection-bg', 'rgba(91, 141, 255, 0.3)'),
    black: themeColour('--surface-base', '#0a0d14'),
    brightBlack: themeColour('--text-faint', '#4a5162'),
    white: themeColour('--text-primary', '#eef1f6'),
  }
}

/**
 * The interactive console.
 *
 * Three things this page is careful about, because each is a way to look capable
 * while being wrong:
 *
 *  - **Fixed 80x24.** QEMU's serial has no window-size channel (`TIOCSWINSZ` does
 *    not exist for it), so the guest never learns the browser window is wider. The
 *    page says so rather than letting the terminal silently wrap at 80 while the
 *    panel is 900px wide. A `resize` frame is still sent, and the server answers
 *    `applied: false` -- the round trip is kept so the capability is visible.
 *  - **Binary frames for keystrokes.** The server sniffs the first byte of every
 *    message; JSON-wrapped keystrokes come back as `BAD_FRAME`. So `onData` sends
 *    encoded bytes, not a JSON string.
 *  - **One holder of the keyboard.** Two tabs with the input right are a coin flip
 *    over who types. The second tab is told it is read-only rather than silently
 *    swallowing keys.
 */
export function TerminalPage() {
  const params = useParams()
  const iid = Number.parseInt(params.iid ?? '', 10)
  const stats = useInstanceStats(Number.isFinite(iid) ? iid : null)
  const theme = useAppearanceStore((state) => state.theme)

  const hostRef = useRef<HTMLDivElement>(null)
  const termRef = useRef<XTerm | null>(null)
  const socketRef = useRef<WebSocket | null>(null)
  const [link, setLink] = useState<LinkState>('connecting')
  const [note, setNote] = useState<string | null>(null)
  const [holder, setHolder] = useState<string | null>(null)
  const [isHolder, setIsHolder] = useState(false)
  const [subscribers, setSubscribers] = useState(1)
  const [dropped, setDropped] = useState(0)
  const [received, setReceived] = useState(0)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const host = hostRef.current
    if (!host || !Number.isFinite(iid)) return

    const term = new XTerm({
      // Matches the guest's 80 columns rather than the panel's width -- see the
      // note above. `convertEol` because a guest tty emits bare LF.
      cols: 80,
      rows: 24,
      fontFamily: '"JetBrains Mono", ui-monospace, Consolas, monospace',
      fontSize: 12,
      lineHeight: 1.2,
      letterSpacing: 0,
      cursorBlink: true,
      convertEol: true,
      scrollback: 5_000,
      theme: readTerminalTheme(),
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host)
    termRef.current = term

    // Fit only to *tell* the user the truth: the guest keeps 80 columns, so the
    // measured size is reported instead of applied.
    let measured = { cols: 0, rows: 0 }
    try {
      fit.fit()
      measured = { cols: term.cols, rows: term.rows }
    } catch {
      /* The addon throws when the host has no layout yet; the fixed size stands. */
    }

    const socket = new WebSocket(terminalSocketUrl(iid))
    socket.binaryType = 'arraybuffer'
    socketRef.current = socket
    setLink('connecting')
    setReceived(0)

    // Bound to *this* terminal and guarded by `live`, never routed through
    // `termRef`. A closed socket keeps delivering until its close frame lands, and
    // by then a reconnect has already replaced the ref -- so the outgoing socket's
    // bytes used to land in the incoming terminal and every guest line appeared
    // twice. Writing to the terminal this effect created makes that impossible:
    // after cleanup `live` is false, so a late frame has nowhere to go.
    let live = true
    const write = (text: string) => {
      if (live) term.write(text)
    }

    const send = (frame: ClientFrame) => {
      if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(frame))
    }

    socket.onopen = () => {
      setLink('open')
      // Probe the resize capability honestly: the answer is `applied: false`, and
      // showing that is more useful than pretending the feature does not exist.
      send({ type: 'resize', cols: measured.cols, rows: measured.rows })
    }

    socket.onmessage = (event) => {
      if (typeof event.data !== 'string') {
        // Guest bytes. Decoded as UTF-8 with replacement: a serial line is allowed
        // to contain anything, and a decode error must not kill the socket.
        const chunk = event.data as ArrayBuffer
        setReceived((total) => total + chunk.byteLength)
        write(new TextDecoder('utf-8', { fatal: false }).decode(chunk))
        return
      }
      let frame: ServerTextFrame
      try {
        frame = JSON.parse(event.data) as ServerTextFrame
      } catch {
        return
      }
      switch (frame.type) {
        case 'hello': {
          const hello = frame as HelloFrame
          setSubscribers(hello.subscriber_count)
          setHolder(hello.input_holder)
          // Not "the keyboard is mine because nobody holds it": the server names
          // subscribers by peer address plus a connection number and never tells
          // this tab which of those it is, so only an explicit `claim` answer
          // settles it -- which is what the button does.
          setIsHolder(false)
          setNote(
            hello.resize_supported
              ? `串口就绪：端口 ${hello.serial_port ?? '—'}，尺寸 ${hello.size.cols}x${hello.size.rows}`
              : `串口就绪：端口 ${hello.serial_port ?? '—'}，QEMU 串口无窗口尺寸通道，固定 ${hello.size.cols}x${hello.size.rows}`,
          )
          break
        }
        case 'pong':
          setDropped(frame.dropped_bytes)
          break
        case 'claim':
          setHolder(frame.input_holder)
          setIsHolder(frame.granted)
          setNote(frame.granted ? '已取得键盘输入权' : '另一个客户端持有输入权，本页为只读')
          break
        case 'release':
          setHolder(frame.input_holder)
          setIsHolder(false)
          setNote('已释放键盘输入权')
          break
        case 'readonly':
          setIsHolder(false)
          setNote(frame.reason)
          break
        case 'error':
          setNote(`${frame.code}: ${frame.message}`)
          break
        case 'resize':
          setNote(`尺寸未应用：${frame.reason}`)
          break
        case 'server.shutdown':
          setLink('closed')
          setNote(frame.message)
          write('\r\n\x1b[33m[iris web 正在退出，串口已关闭]\x1b[0m\r\n')
          break
      }
    }

    socket.onerror = () => setLink('refused')

    socket.onclose = (event) => {
      setLink('closed')
      setIsHolder(false)
      // A code with no explanation here is either a clean close (1000) or a transport
      // failure the browser will not name; only the ones we know are worth annotating.
      const reason = CLOSE_REASON[event.code]
      if (reason) {
        setNote(reason)
      } else if (event.code) {
        setNote(`连接已关闭（code ${event.code}）`)
      }
    }

    const onData = term.onData((data) => {
      // Raw bytes, not JSON. The server decides text-vs-binary by the first byte,
      // and a JSON object would be answered with BAD_FRAME.
      if (socket.readyState === WebSocket.OPEN) socket.send(new TextEncoder().encode(data))
    })

    const keepAlive = window.setInterval(() => send({ type: 'ping' }), 15_000)

    return () => {
      live = false
      window.clearInterval(keepAlive)
      onData.dispose()
      socket.close()
      socketRef.current = null
      term.dispose()
      termRef.current = null
    }
    // `attempt` re-runs the whole effect, which is the retry: a new socket, a new
    // terminal, a clean slate. `write` is no longer a dependency because it is
    // created inside the effect and closes over this run's terminal.
  }, [iid, attempt])

  // Repaint the existing terminal on a theme change instead of rebuilding it.
  // Rebuilding would drop the scrollback and close the socket, which is a far
  // worse price than re-reading four variables. Declared after the effect above so
  // the first run finds a terminal that has already been created.
  useEffect(() => {
    const term = termRef.current
    if (term) term.options.theme = readTerminalTheme()
  }, [theme])

  const claim = () => socketRef.current?.send(JSON.stringify({ type: 'claim' }))
  const release = () => socketRef.current?.send(JSON.stringify({ type: 'release' }))

  if (!Number.isFinite(iid)) {
    return (
      <div className="p-4">
        <Panel title="无效的实例号">
          <ErrorState title="缺少实例号" detail="终端需要一个 /instances/:iid/terminal 形式的地址" />
        </Panel>
      </div>
    )
  }

  const consoleMissing = stats.data && !stats.data.console_available

  return (
    <div className="flex h-full flex-col gap-3 p-4">
      <Panel
        title={
          <span className="flex items-center gap-2">
            实例 {iid} 交互式终端
            <Badge
              tone={
                link === 'open' ? (isHolder ? 'success' : 'warning') : link === 'connecting' ? 'iris' : 'danger'
              }
            >
              {link === 'open' ? (isHolder ? '可输入' : '只读') : link === 'connecting' ? '连接中' : '已断开'}
            </Badge>
          </span>
        }
        subtitle="QEMU 串口 chardev · 80×24 固定 · 安全等级等于 guest shell"
        actions={
          <div className="flex items-center gap-1.5">
            <Button size="sm" variant={isHolder ? 'outline' : 'primary'} onClick={claim} disabled={link !== 'open'}>
              <Keyboard className="h-3.5 w-3.5" aria-hidden="true" />
              取得输入权
            </Button>
            <Button size="sm" variant="ghost" onClick={release} disabled={link !== 'open' || !isHolder}>
              <KeyboardOff className="h-3.5 w-3.5" aria-hidden="true" />
              释放
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setAttempt((value) => value + 1)}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              重连
            </Button>
          </div>
        }
        bodyClassName="p-3"
      >
        {consoleMissing && (
          <p className="mb-2 flex items-start gap-1.5 rounded-card border border-warning/30 bg-warning/10 px-2 py-1.5 text-2xs text-warning">
            <TriangleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" />
            此实例没有发布串口端口，命令行启动的仿真（iris emulate run）不经过 API，因此无法接入控制台
          </p>
        )}

        <div
          ref={hostRef}
          role="log"
          aria-live="polite"
          aria-label={`实例 ${iid} 的串口输出`}
          className={classNames(
            'min-h-[480px] rounded-card border border-surface-border bg-surface-base p-2',
            link === 'closed' && 'opacity-70',
          )}
        />

        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-ink-700">
          <span>订阅者 {subscribers}</span>
          <span>·</span>
          <span>本连接已收字节 {received}</span>
          <span>·</span>
          <span>本订阅者丢弃字节 {dropped}</span>
          <span>·</span>
          <span>输入持有者：{holder ?? '无'}</span>
          <span>·</span>
          <span className="text-ink-700">Ctrl/Cmd+` 在任意页面回到终端</span>
          <Link to={`/instances/${iid}`} className="ml-auto text-iris-400 hover:underline">
            返回实例详情
          </Link>
        </div>
        <p className="mt-1 text-2xs text-ink-700">
          上方窗口是 guest 的原始串口输出，其中{' '}
          <code className="rounded bg-surface-code px-1 font-mono text-[10px]">IRIS-RC:</code> 与固件自身的启动告警（如{' '}
          <code className="rounded bg-surface-code px-1 font-mono text-[10px]">lookup xxx failed</code>）属正常日志，不代表链路异常；链路是否正常请看本行的已收字节与丢弃字节
        </p>
        {note && (
          <p className="mt-1 text-2xs text-ink-500" role="status">
            {note}
          </p>
        )}
      </Panel>
    </div>
  )
}
