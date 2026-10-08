/**
 * Talking to the IRIS API.
 *
 * Three decisions live here rather than in the components that call them:
 *
 *  - **Root-absolute URLs.** The workbench and the API are served from the same
 *    origin by the same process, so every path starts at `/`. A relative
 *    `api/v1/...` would resolve against the *current page*, and `/instances/7100`
 *    would turn it into `/instances/api/v1/stats` -- a 404 that looks like a
 *    backend problem on exactly the pages people navigate to most.
 *  - **The token is a header, never a query string.** `iris.api.auth` accepts
 *    `X-IRIS-Token` precisely so a browser can authenticate without putting a
 *    credential in a URL that ends up in history and logs. The one exception is
 *    the websocket, which cannot set headers at all -- see `terminalSocketUrl`.
 *  - **Errors keep the status code.** A 404 and a 401 mean different things to the
 *    person looking at the screen, and a client that flattens both to "request
 *    failed" cannot tell them apart either.
 */

import type {
  ActiveEmulation,
  AiStatus,
  Capabilities,
  ConsoleLog,
  CorpusView,
  DiagnosisResponse,
  DraftInstallResponse,
  DraftList,
  DraftMutationResponse,
  DraftRemovalResponse,
  DraftSaveRequest,
  EffectiveConfig,
  EmulateResponse,
  EvalSet,
  FailureMatrix,
  FirmwareInfo,
  InstanceStats,
  LatencyView,
  PluginInstalled,
  RootCauseCard,
  RuleLibrary,
  RunDetail,
  RunsPage,
  Stats,
  SystemReading,
  UploadLaunchResponse,
} from './types'

/** Where the token lives between page loads. Not a cookie: the API takes a header,
 *  and a cookie would add a CSRF surface the server does not need. */
const TOKEN_KEY = 'iris.api.token'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }

  /** True when the token is missing or wrong -- the one error with a fix the
   *  person on screen can apply without reading the server logs. */
  get isAuth() {
    return this.status === 401
  }

  get isMissing() {
    return this.status === 404
  }
}

export function readToken(): string {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    // Private browsing modes can refuse localStorage outright. An empty token
    // means "local mode", which is the right fallback: the server decides.
    return ''
  }
}

export function writeToken(token: string): void {
  try {
    if (token) {
      window.localStorage.setItem(TOKEN_KEY, token)
    } else {
      window.localStorage.removeItem(TOKEN_KEY)
    }
  } catch {
    /* Nothing to do: the token simply will not persist across reloads. */
  }
}

type Query = Record<string, string | number | undefined>

function withQuery(path: string, query?: Query): string {
  if (!query) return path
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const suffix = search.toString()
  return suffix ? `${path}?${suffix}` : path
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readToken()
  const headers = new Headers(init?.headers)
  if (token) headers.set('X-IRIS-Token', token)
  // FormData has to be left alone: the browser writes its own Content-Type with the
  // multipart boundary, and setting `application/json` here is what turns an upload
  // into "There was an error parsing the body" from starlette.
  const isFormData = typeof FormData !== 'undefined' && init?.body instanceof FormData
  if (init?.body !== undefined && !headers.has('Content-Type') && !isFormData) {
    headers.set('Content-Type', 'application/json')
  }

  let response: Response
  try {
    response = await fetch(path, { ...init, headers })
  } catch (cause) {
    // A network-level failure has no status. Reporting it as one keeps the UI's
    // "server unreachable" panel from having to catch a different exception type.
    throw new ApiError(0, `无法连接到 IRIS 服务：${(cause as Error).message}`)
  }

  if (response.status === 204) return undefined as T

  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    const detail =
      typeof payload === 'string'
        ? payload
        : ((payload as { detail?: string } | null)?.detail ?? response.statusText)
    throw new ApiError(response.status, detail || `HTTP ${response.status}`)
  }
  return payload as T
}

export const api = {
  health: () => request<{ status: string; version: string }>('/api/v1/health'),

  stats: () => request<Stats>('/api/v1/stats'),

  capabilities: () => request<Capabilities>('/api/v1/capabilities'),

  system: () => request<SystemReading>('/api/v1/system'),

  evalSet: () => request<EvalSet>('/api/v1/stats/eval-set'),

  /** One row per firmware. `totals` is counted by the same function the stat cards
   *  read, so the two cannot disagree about the same runs. */
  corpus: () => request<CorpusView>('/api/v1/stats/corpus'),

  /** How long a run took, per architecture, over the runs that actually got there. */
  latency: () => request<LatencyView>('/api/v1/stats/latency'),

  /** Failures crossed with architecture, counted exactly as the dashboard counts
   *  them -- same stage resolution, same informational exclusion. */
  failureMatrix: () => request<FailureMatrix>('/api/v1/stats/failure-matrix'),

  rootCauses: (recent = 10) =>
    request<{ cards: RootCauseCard[] }>(withQuery('/api/v1/knowledge/root-cause', { recent })),

  config: () => request<EffectiveConfig>('/api/v1/config'),

  runs: (params: { limit?: number; offset?: number; arch?: string; result_kind?: string; query?: string } = {}) =>
    request<RunsPage>(withQuery('/api/v1/runs', params)),

  run: (id: number) => request<RunDetail>(`/api/v1/runs/${id}`),

  /** The rule plugins the engine would load: the ones IRIS ships plus any installed
   *  through the workbench. A page that listed invented extensions would be the one
   *  panel on this workbench whose contents mean nothing, so this is the documents
   *  themselves, read through the engine's own loader. */
  rules: () => request<RuleLibrary>('/api/v1/rules'),

  /** Install a rule plugin from a file the browser holds.
   *
   * Multipart for the same reason `uploadLaunch` is: the body *is* the file, and only
   * the browser's own Content-Type carries a boundary that matches. A 422 arrives
   * with a reason written for the plugin author -- the loader's own complaints,
   * verbatim -- which is why the page shows `detail` rather than a generic failure.
   */
  installPlugin: (file: File) => {
    const form = new FormData()
    form.append('file', file, file.name)
    return request<PluginInstalled>('/api/v1/plugins', { method: 'POST', body: form })
  },

  /** Uninstall one rule plugin by the file name the listing reported. Built-in
   *  rules are not reachable this way: they are not in the plugin directory. */
  removePlugin: (name: string) =>
    request<{ removed: string }>(`/api/v1/plugins/${encodeURIComponent(name)}`, {
      method: 'DELETE',
    }),

  /** Whether the LLM layer would run, with the key reported as configured or not.
   *  The rules engine never waits on this answer -- a disabled layer is a stated
   *  fact in the diagnosis response, not a failed request. */
  aiStatus: () => request<AiStatus>('/api/v1/ai/status'),

  /** One diagnosis of one instance: pattern counts first, then the model only for
   *  the signals those counts cannot name.
   *
   *  A button-triggered call by design. There is no automatic escalation path, so
   *  this cannot spend a token on a run nobody is watching. */
  diagnose: (iid: number) =>
    request<DiagnosisResponse>('/api/v1/ai/diagnose', {
      method: 'POST',
      body: JSON.stringify({ iid }),
    }),

  /** The drafts waiting on a person, newest first, with each document's text. */
  aiDrafts: () => request<DraftList>('/api/v1/ai/drafts'),

  /** Put one decision's draft into the draft directory. Saving loads nothing: the
   *  draft directory is deliberately outside the engine's load path. */
  saveDraft: (draft: DraftSaveRequest) =>
    request<DraftMutationResponse>('/api/v1/ai/drafts', {
      method: 'POST',
      body: JSON.stringify(draft),
    }),

  /** Accept one draft. Goes through the same validation chain as an upload, so a
   *  422 arrives with the loader's own complaint -- "installed but ineffective"
   *  has no other useful answer. */
  installDraft: (ruleId: string) =>
    request<DraftInstallResponse>(
      `/api/v1/ai/drafts/${encodeURIComponent(ruleId)}/install`,
      { method: 'POST' },
    ),

  /** Refuse one draft. Its document goes; its metadata stays as the record that a
   *  review happened and said no. */
  rejectDraft: (ruleId: string) =>
    request<DraftRemovalResponse>(`/api/v1/ai/drafts/${encodeURIComponent(ruleId)}`, {
      method: 'DELETE',
    }),

  /** Erase one recorded run. Counted rather than a boolean: 404 and "removed
   *  nothing" are different answers and the page reports the difference. */
  deleteRun: (id: number) =>
    request<{ removed: number; run_id: number }>(`/api/v1/runs/${id}`, { method: 'DELETE' }),

  /** Erase the whole recorded history. The firmware corpus is not touched. */
  clearRuns: () => request<{ removed: number }>('/api/v1/runs', { method: 'DELETE' }),

  /** The CSV is fetched rather than linked so a 401 can be reported in place; a
   *  plain `<a href>` would navigate the tab to a JSON error body. */
  exportCsvUrl: () => '/api/v1/runs/export.csv',

  async exportCsv(): Promise<Blob> {
    const token = readToken()
    const headers = new Headers()
    if (token) headers.set('X-IRIS-Token', token)
    const response = await fetch(api.exportCsvUrl(), { headers })
    if (!response.ok) {
      throw new ApiError(response.status, `导出失败：HTTP ${response.status}`)
    }
    return await response.blob()
  },

  console: (iid: number, startLine = 0, maxLines = 2000) =>
    request<ConsoleLog>(withQuery(`/api/v1/console/${iid}`, { start_line: startLine, max_lines: maxLines })),

  instanceStats: (iid: number) => request<InstanceStats>(`/api/v1/instances/${iid}/stats`),

  listEmulations: () => request<ActiveEmulation[]>('/api/v1/emulate'),

  firmware: () => request<FirmwareInfo[]>('/api/v1/firmware'),

  emulate: (body: { rootfs_path: string; arch: string; iid?: number; port?: number; timeout?: number }) =>
    request<EmulateResponse>('/api/v1/emulate', { method: 'POST', body: JSON.stringify(body) }),

  /**
   * Start an emulation from a file the browser holds: a tar of an extracted rootfs,
   * or a vendor firmware image.
   *
   * Sent as multipart rather than JSON because the body *is* the file. The
   * Content-Type header is deliberately left unset: the boundary in it has to match
   * the one the browser generates for this FormData instance, and a hardcoded
   * `multipart/form-data` is how that ends in "server cannot parse the body".
   */
  uploadLaunch: (
    file: File,
    params: { kind?: 'auto' | 'rootfs' | 'firmware'; arch?: string; port?: number; timeout?: number } = {},
  ) => {
    const form = new FormData()
    form.append('file', file, file.name)
    const query: Query = { ...params }
    if (!query.arch) delete query.arch
    return request<UploadLaunchResponse>(withQuery('/api/v1/emulate/upload', query), {
      method: 'POST',
      body: form,
    })
  },

  stopEmulation: (iid: number) => request<Record<string, unknown>>(`/api/v1/emulate/${iid}`, { method: 'DELETE' }),
}

/**
 * The terminal socket for one instance.
 *
 * The token rides in the query string here and nowhere else: a browser's
 * `WebSocket` constructor cannot set headers, which is why the server checks
 * `?token=` with the same constant-time comparison the REST routes use. The cost
 * is that the URL can end up in a proxy log on a shared host -- accepted for the
 * console, avoided everywhere else.
 */
export function terminalSocketUrl(iid: number): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const params = new URLSearchParams({ iid: String(iid) })
  const token = readToken()
  if (token) params.set('token', token)
  return `${scheme}://${window.location.host}/ws/terminal?${params.toString()}`
}