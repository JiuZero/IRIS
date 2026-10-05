import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Play } from 'lucide-react'

import { Modal } from './Modal'
import { Badge, Button, FileInput, Segmented, Select, TextInput } from './ui'
import { api } from '../lib/api'
import { classNames, formatBytes, seconds } from '../lib/format'
import { useFirmware } from '../hooks/queries'
import { useTicker } from '../hooks'
import type { EmulateResponse, UploadLaunchResponse } from '../lib/types'

/** Either launch route's answer. `UploadLaunchResponse` extends
 *  `EmulateResponse`, so the boot verdict is read off one field name and the
 *  upload-only extras are narrowed by `'source' in result`. */
type LaunchOutcome = EmulateResponse | UploadLaunchResponse

type LaunchSource = 'ready' | 'rootfs-archive' | 'firmware'

/**
 * Start an emulation, in a window over whatever page you happened to be on.
 *
 * The source picker exists because "启动一次仿真" was only ever reachable for a
 * rootfs that `iris extract` had already written to the staging directory. Anyone
 * who arrived with a tar of a rootfs, or with the vendor's own `.bin`, had no way
 * in except the command line. All three routes end at the same QEMU run; they
 * differ in how much work happens before it.
 *
 * The honest part is the timing: these endpoints run the whole boot and only then
 * answer -- there is no job id to poll, because `iris.api.server` awaits
 * `emulate_firmware` in a thread and returns its result. So the button shows an
 * elapsed counter and says plainly that the answer takes as long as the boot does
 * (tens of seconds to a few minutes on this corpus). A progress bar over a request
 * that reports nothing would be a lie with a percentage on it.
 *
 * It is a window rather than a panel on the dashboard because launching does not
 * need the dashboard: the decision is "which firmware, which port", and both are
 * knowable from any screen. A form pinned to one page means the people on the
 * other four have to go somewhere first, and the fastest path to the terminal they
 * already left is not via the overview. The verdict stays in the window rather than
 * closing on success -- a launch that answers "Web 不可达, link-no-arp" is exactly
 * the answer somebody needs to read before moving on.
 */
export function LaunchDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const firmware = useFirmware()
  const client = useQueryClient()
  const [source, setSource] = useState<LaunchSource>('ready')
  const [selected, setSelected] = useState('')
  const [archive, setArchive] = useState<File | null>(null)
  const [image, setImage] = useState<File | null>(null)
  const [arch, setArch] = useState('')
  const [port, setPort] = useState('0')
  const [timeoutValue, setTimeoutValue] = useState('200')
  const [lastResult, setLastResult] = useState<LaunchOutcome | null>(null)

  const shared = {
    port: Number.parseInt(port, 10) || 0,
    timeout: Number.parseInt(timeoutValue, 10) || 200,
  }

  const start = useMutation({
    mutationFn: async (): Promise<LaunchOutcome> => {
      if (source === 'ready') {
        const target = (firmware.data ?? []).find((item) => item.path === selected)
        if (!target) throw new Error('请先选择一个已提取的 rootfs')
        return api.emulate({
          rootfs_path: target.path,
          // The census reports "unknown" for a rootfs with no ELF in bin/; refusing to
          // guess here would block a legitimate launch, and the server's preflight is
          // the authority that actually rejects a wrong architecture.
          arch: target.arch === 'unknown' ? 'mipsel' : target.arch,
          port: shared.port,
          timeout: shared.timeout,
        })
      }
      const file = source === 'rootfs-archive' ? archive : image
      if (!file) throw new Error(source === 'rootfs-archive' ? '请先选择一个 rootfs 归档' : '请先选择一个固件镜像')
      // `kind` is stated rather than left to `auto` so the window's choice is the
      // request's choice; a mislabelled tar then fails as a firmware image with a
      // message about the format instead of quietly extracting to the wrong tree.
      return api.uploadLaunch(file, {
        kind: source === 'rootfs-archive' ? 'rootfs' : 'firmware',
        arch: arch.trim() || undefined,
        port: shared.port,
        timeout: shared.timeout,
      })
    },
    onSuccess: (result) => {
      setLastResult(result)
      void client.invalidateQueries({ queryKey: ['emulations'] })
      void client.invalidateQueries({ queryKey: ['stats'] })
      void client.invalidateQueries({ queryKey: ['runs'] })
    },
  })

  const elapsed = useTicker(1_000, start.isPending)
  const file = source === 'rootfs-archive' ? archive : source === 'firmware' ? image : null
  const ready = source === 'ready' ? Boolean(selected) : Boolean(file)

  return (
    <Modal
      open={open}
      title="新建实例"
      subtitle="来源可选已提取的 rootfs、rootfs 归档或厂商固件镜像；接口会等启动过程结束才返回，通常需要数十秒到数分钟"
      onClose={onClose}
      // Wider than the other window on purpose: the four controls on one row need
      // the room, and a form that wraps its last field is what put 启动超时 on a
      // line of its own.
      width="max-w-3xl"
      footer={
        <>
          <Badge tone={start.isPending ? 'iris' : 'neutral'}>
            {start.isPending ? `已等待 ${elapsed}s` : '空闲'}
          </Badge>
          <div className="ml-auto flex items-center gap-2">
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
            <Button
              variant="primary"
              disabled={start.isPending || !ready}
              onClick={() => start.mutate()}
              title="启动（接口会阻塞到仿真结束）"
            >
              <Play className="h-3.5 w-3.5" aria-hidden="true" />
              {start.isPending ? '启动中…' : '启动'}
            </Button>
          </div>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Segmented<LaunchSource>
          label="固件来源"
          value={source}
          onChange={(value) => {
            setSource(value)
            // The previous answer describes a different route, so leaving it up
            // would put an upload's unpack numbers under the "已提取 rootfs" tab.
            setLastResult(null)
            start.reset()
          }}
          options={[
            { value: 'ready', label: '已提取 rootfs', hint: '从暂存目录选择 iris extract 已经解出的 rootfs' },
            { value: 'rootfs-archive', label: 'rootfs 归档', hint: '上传一个 rootfs 的 tar 包，服务器会解包并补修' },
            { value: 'firmware', label: '厂商 bin', hint: '上传厂商原始固件镜像，先剖分再仿真' },
          ]}
        />

        {/* One row, three baselines. Every field is a `.field-cell` spanning the
            label, control and hint tracks of this grid, so a field with a hint and a
            field without one put their controls on the same line -- which a flex row
            with `items-end` cannot do, and which it also cannot do after wrapping,
            and it wrapped: 624px of controls and gaps against a 632px body. The three
            and four column templates differ because the architecture box appears only
            for the two upload routes; one template would slide 宿主端口 sideways every
            time the source changed. */}
        <div className={classNames('field-row', source === 'ready' ? 'field-row-3' : 'field-row-4')}>
          {source === 'ready' ? (
            <Select
              label="rootfs"
              value={selected}
              onChange={setSelected}
              options={[
                {
                  value: '',
                  placeholder: true,
                  label: firmware.isLoading
                    ? '读取中…'
                    : firmware.data?.length
                      ? '选择一个已提取的固件'
                      : '暂存目录中没有 *-rootfs',
                },
                ...(firmware.data ?? []).map((item) => ({
                  value: item.path,
                  label: `${item.name} · ${item.arch}`,
                })),
              ]}
              hint="iris extract 已解出的 rootfs"
            />
          ) : (
            <FileInput
              label={source === 'rootfs-archive' ? 'rootfs 归档' : '固件镜像'}
              accept={
                source === 'rootfs-archive'
                  ? '.tar,.tar.gz,.tgz,.tar.bz2,.tar.xz'
                  : '.bin,.img,.trx,.squashfs,.fs'
              }
              file={file}
              disabled={start.isPending}
              onPick={source === 'rootfs-archive' ? setArchive : setImage}
              hint={
                source === 'rootfs-archive'
                  ? 'tar / tar.gz；越界路径与逃逸软链会被拒绝并计数'
                  : '厂商原始镜像；先剖分出 rootfs 再启动'
              }
            />
          )}

          {source !== 'ready' && (
            <TextInput
              label="架构"
              value={arch}
              placeholder="如 mipsel"
              onChange={(event) => setArch(event.target.value)}
              hint="留空则自动判定"
            />
          )}
          <TextInput
            label="宿主端口"
            type="number"
            value={port}
            hint="0 = 自动选择"
            onChange={(event) => setPort(event.target.value)}
          />
          <TextInput
            label="启动超时（秒）"
            type="number"
            value={timeoutValue}
            onChange={(event) => setTimeoutValue(event.target.value)}
            hint="接口等启动结束才返回"
          />
        </div>

        {start.isError && (
          <p className="text-2xs text-danger" role="alert">
            启动失败：{(start.error as Error).message}
          </p>
        )}
        {lastResult && <LaunchOutcome result={lastResult} />}
      </div>
    </Modal>
  )
}

/** What the window shows after a launch: the boot verdict, plus -- for an upload --
 *  where the input came from and what the unpack did. The upload extras are not
 *  decoration: a skipped symlink is the first thing to look at when a guest that
 *  came out of a tar will not boot. */
function LaunchOutcome({ result }: { result: LaunchOutcome }) {
  const upload = 'source' in result ? result : null
  return (
    <div className="flex flex-col gap-2 border-t border-surface-border pt-3 text-2xs">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={result.web_ok ? 'success' : 'danger'}>{result.web_ok ? 'Web 可达' : 'Web 不可达'}</Badge>
        {upload && <Badge tone="violet">{upload.source === 'rootfs' ? 'rootfs 归档' : '厂商固件'}</Badge>}
        <span className="font-mono text-ink-300">iid {result.iid}</span>
        <span className="tnum text-ink-500">{seconds(result.duration_sec)}</span>
        {/* Never 0: the server resolves "pick a free one" before answering, so a 0
            here would mean the boot timed out probing a port never opened. */}
        {upload && upload.host_port > 0 && (
          <span className="tnum text-ink-500" title="容器实际发布的宿主端口">
            宿主端口 {upload.host_port}
          </span>
        )}
        {result.web_url && result.web_url !== '-' && (
          <a
            href={result.web_url}
            target="_blank"
            rel="noreferrer noopener"
            className="font-mono text-cyan hover:underline"
          >
            {result.web_url}
          </a>
        )}
        {result.error && <span className="text-danger">{result.error}</span>}
        <Link to={`/instances/${result.iid}`} className="ml-auto text-iris-400 hover:underline">
          查看实例详情
        </Link>
      </div>

      {upload && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-ink-700">
          <span className="max-w-full truncate font-mono text-ink-500" title={upload.name}>
            {upload.name}
          </span>
          <span className="font-mono">解出 {upload.members} 个成员</span>
          <span className="font-mono">{formatBytes(upload.total_bytes)}</span>
          <span className="font-mono">重建软链 {upload.links_created}</span>
          {upload.links_skipped > 0 && (
            <Badge tone="warning" title="逃逸或悬空的软链已被跳过；rootfs 主要靠软链，缺失会直接导致启动失败">
              跳过软链 {upload.links_skipped}
            </Badge>
          )}
          {upload.rejected_members > 0 && (
            <Badge tone="warning" title="越界路径或特殊文件已被拒绝">
              拒绝成员 {upload.rejected_members}
            </Badge>
          )}
          {upload.matched_rule_ids.length > 0 && (
            <span className="font-mono">命中规则 {upload.matched_rule_ids.join('、')}</span>
          )}
        </div>
      )}
      {upload && upload.notes.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-ink-700">
          {upload.notes.map((note, index) => (
            <li key={`${index}-${note}`} className="leading-relaxed">
              · {note}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}