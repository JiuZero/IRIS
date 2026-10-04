/**
 * The shapes the server returns, written down once.
 *
 * These mirror `iris.api.web_data` and `iris.api.server`. They are hand-written
 * rather than generated because there is no OpenAPI client in the toolchain and
 * adding one to save thirty field names would be the larger dependency -- but the
 * discipline is the same as a generated client: every field the UI reads is
 * declared here, and a field the server stops sending becomes a type error rather
 * than an `undefined` that renders as "nothing" on a dashboard.
 *
 * `null` and `0` are kept distinct everywhere, because the server went to some
 * trouble to keep them distinct: an unmeasured container is `null`, not `0%`.
 */

/** `iris.db.runs.LayerState` -- three states, not two. `unknown` means the probe
 *  could not run, which is not a claim about the network. */
export type LayerState = 'ok' | 'blocked' | 'unknown'

export interface LinkLayer {
  layer: string
  state: LayerState
  detail: string
}

export interface LinkProfile {
  guest_ip: string
  first_break: string
  unavailable: string
  layers: LinkLayer[]
  /** Present only on the profile rebuilt from stored evidence (`run_detail`), where it
   *  names the fields that were never written to the database. Absent on the live
   *  `POST /api/v1/emulate` profile, which is complete. */
  note?: string
}

export interface ActiveEmulation {
  iid: number
  client_id: string
  arch: string
  container_id: string
  rootfs_path: string
  web_url: string
  success: boolean
  web_ok: boolean
  started_at: string
}

export interface RunItem {
  id: number
  iid: number
  image_id: number | null
  arch: string
  /** `null` when the run ended before the web probe answered. */
  web_ok: boolean | null
  ping_ok: boolean | null
  ip: string
  time_web: number | null
  time_ping: number | null
  result: boolean | null
  result_kind: string
  started_at: string
  finished_at: string
}

export interface RunsPage {
  total: number
  offset: number
  limit: number
  items: RunItem[]
}

export interface FailureRow {
  stage: string
  signal: string
  detail: Record<string, unknown>
  log_fingerprint: string
}

export interface RepairRow {
  source: string
  rule_id: string
  evidence: string
  applied: boolean
  promoted: boolean
}

export interface RunDetail extends RunItem {
  failures: FailureRow[]
  repairs: RepairRow[]
  /** Rebuilt from the probe evidence stored with the run; `null` when the run never
   *  stored one (a clean run is not probed, and an unprobeable one records no
   *  evidence). Not re-measured: this is what the run saw at the time. */
  link: LinkProfile | null
}

/** One failure cluster, from `iris.db.knowledge.root_cause_cards`. */
export interface RootCauseCard {
  kind: string
  stage: string
  runs: number
  recovered: number
  images: number
  archs: string[]
  repairs: string[]
  sample: string
  /** True when the kind was seen in one of the most recent failing runs. */
  candidate: boolean
}

export interface Stats {
  available: boolean
  detail?: string
  total: number | null
  web_ok: number | null
  environment_failures: number | null
  web_reach_rate: number | null
  running: number | null
  by_arch: Record<string, { seen: number; web_ok: number }>
  failures: Array<{ stage: string; kind: string; count: number }>
  stages: Record<string, number>
  active: ActiveEmulation[]
}

export type CapabilityState = 'available' | 'planned' | 'unavailable'

export interface CapabilityItem {
  id: string
  name: string
  state: CapabilityState
  detail: string
  evidence: string
}

export interface Capabilities {
  version: string
  items: CapabilityItem[]
}

export interface EvalSet {
  release: string
  scope: string
  denominator: number
  web_ok: number
  web_reach_rate: number
  by_arch: Record<string, { seen: number; web_ok: number }>
  denominator_note: string
  items: RunItem[]
  items_note: string
}

export interface ConsoleLog {
  available: boolean
  reason?: string
  lines: string[]
  next_line: number
  total_lines?: number
  complete?: boolean
  path: string
}

export interface InstanceStats {
  iid: number
  container: string
  /** False means "not measured yet", not "idle". */
  sampled: boolean
  cpu_pct: number | null
  mem_mb: number | null
  mem_limit_mb: number | null
  serial_port: number | null
  console_available: boolean
}

export interface EffectiveConfig {
  database_url: string
  scratch_dir: string
  api_max_upload_mb: number
  api_token_configured: boolean
  read_only: boolean
  note: string
}

export interface FirmwareInfo {
  name: string
  path: string
  arch: string
}

export interface EmulateResponse {
  iid: number
  success: boolean
  web_ok: boolean
  web_url: string
  duration_sec: number
  error: string
  container_id: string
  link: LinkProfile | null
}

/** `POST /api/v1/emulate/upload`. Shares the boot verdict with `EmulateResponse`
 *  and adds what only an upload knows: which input it came from, the port actually
 *  published, and how the unpack went. */
export interface UploadLaunchResponse extends EmulateResponse {
  /** `rootfs` (an archive of an extracted tree) or `firmware` (a vendor image).
   *  The two fail differently, so the page says which one it tried. */
  source: 'rootfs' | 'firmware'
  name: string
  arch: string
  rootfs_path: string
  /** Never 0 -- the server resolves "pick a free one" before answering. */
  host_port: number
  members: number
  total_bytes: number
  /** Links present in the archive that were recreated. A rootfs is mostly links,
   *  so a low number here is the first explanation for a guest that cannot boot. */
  links_created: number
  links_skipped: number
  rejected_members: number
  matched_rule_ids: string[]
  notes: string[]
}

/**
 * The terminal socket's protocol, as `iris.api.web_terminal` actually speaks it.
 *
 * The split that matters: **guest bytes travel as binary frames, everything else
 * travels as JSON text frames.** The server sniffs the first byte of every inbound
 * message to tell them apart, so a client that JSON-wrapped its keystrokes would be
 * answered with `BAD_FRAME` and nothing would appear in the terminal.
 */
export interface HelloFrame {
  type: 'hello'
  iid: number
  serial_port: number | null
  /** Always 80x24. QEMU's serial has no window-size channel -- see `resize`. */
  size: { cols: number; rows: number }
  resize_supported: boolean
  /** Name of the client currently allowed to type, or null when it is free. */
  input_holder: string | null
  subscriber_count: number
}

export type ServerTextFrame =
  | HelloFrame
  | { type: 'server.shutdown'; message: string }
  | { type: 'error'; code: string; message: string }
  | { type: 'readonly'; reason: string }
  | { type: 'claim'; granted: boolean; input_holder: string | null }
  | { type: 'release'; input_holder: string | null }
  /** `dropped_bytes` is this subscriber's own backlog overflow, not the server's. */
  | { type: 'pong'; dropped_bytes: number }
  | { type: 'resize'; applied: false; reason: string }

export type ClientFrame =
  | { type: 'claim' }
  | { type: 'release' }
  /** base64, so a command with a newline survives the JSON round trip. */
  | { type: 'input'; data: string }
  | { type: 'ping' }
  | { type: 'resize'; cols: number; rows: number }

/** Close codes `iris.api.web_terminal` sends, named. */
export const CLOSE_NOT_FOUND = 4404
export const CLOSE_NO_CONSOLE = 4403
export const CLOSE_UNAVAILABLE = 1011