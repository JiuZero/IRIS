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
  /** Where the instance stands now: `running` is an entry in the active table,
   *  `stopped` is a scratch artefact left behind by a run that ended (a stop
   *  keeps the directory on purpose), `deleted` is no artefact at all. Closed
   *  vocabulary because the history table renders a badge on it. */
  state: 'running' | 'stopped' | 'deleted'
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
  /** Whether anything is still running under this id. A closed vocabulary because a
   *  panel branches on it, and `gone` is the state that ends the polling: the answer
   *  it carries can no longer change. */
  state: 'running' | 'gone'
  /** False means "not measured yet", not "idle". */
  sampled: boolean
  cpu_pct: number | null
  mem_mb: number | null
  mem_limit_mb: number | null
  serial_port: number | null
  console_available: boolean
}

/** One editable setting row, as `GET /api/v1/config` reports it.
 *
 * `value` is what this process is actually using and `stored` is what the panel
 * has written. They differ until a restart, which is why both are here rather
 * than one of them: a panel showing only the stored value would claim a setting
 * is in effect when it is not, and one showing only the live value would hide
 * what a restart is about to change.
 *
 * `kind` is a closed set because both the widget and the JSON type hang off it.
 * `value` is `null` on a `secret` row -- `secret_configured` is the fact instead,
 * the same way `api_token_configured` works.
 */
export type SettingKind = 'text' | 'number' | 'switch' | 'secret'

export interface SettingRow {
  key: string
  label: string
  kind: SettingKind
  help: string
  value: string | number | boolean | null
  stored: string | number | boolean | null
  pending_restart: boolean
  secret_configured: boolean
  minimum: number | null
  maximum: number | null
  placeholder: string
}

export interface EffectiveConfig {
  database_url: string
  scratch_dir: string
  api_token_configured: boolean
  rows: SettingRow[]
  /** False when the store could not be read. The form stays visible and says why
   *  rather than offering fields that would not stick. */
  writable: boolean
  detail: string
  note: string
}

/** The answer to a save or a reset. `restart_required` is its own field rather than
 *  something to read out of the note, because the panel puts a badge on it. */
export interface ConfigUpdateResult {
  saved: string[]
  cleared: string[]
  restart_required: boolean
  note: string
}

/**
 * `GET /api/v1/system`: what the host is doing right now.
 *
 * Every field is `number | null`, and the null is the point. These are live
 * observations with no record behind them, so "not measured yet" and "measured
 * zero" must stay distinguishable -- `cpu_pct` is null for exactly one poll
 * interval after start-up, because the first sample has no window to divide by,
 * and a page that rendered that as 0 would report an idle machine.
 */
export interface SystemReading {
  host: {
    /** Share of all host CPU across the window between the two most recent reads.
     *  Null on a sampler's first read, and null again if the counters did not
     *  move. */
    cpu_pct: number | null
    cpu_cores: number | null
    mem_used_mb: number | null
    mem_total_mb: number | null
    /** `psutil`'s own percentage of physical memory. Not derived from the two
     *  figures above, so it can disagree with them on a host with a large page
     *  cache -- which is why it is a separate field rather than a computed one. */
    mem_pct: number | null
    /** Seconds since the host booted. Distinct from the service's own uptime. */
    uptime_sec: number | null
    /** Free space on the volume holding the scratch directory -- the one that
     *  fills up, since every container's rootfs tarball lands there. */
    scratch_free_gb: number | null
  }
  service: {
    version: string
    /** Seconds since this process started, from its own monotonic clock. */
    uptime_sec: number
    /** How long a reading is reused before being re-taken. The page uses it to
     *  explain why two numbers drawn a moment apart can be identical. */
    sample_ttl_sec: number
  }
}

export interface FirmwareInfo {
  name: string
  path: string
  arch: string
}

/** `GET /api/v1/rules`: one rule document the engine would load, as
 *  `iris.api.web_data.rule_plugins` summarises it.
 *
 *  `detect` and `actions` are *kinds*, not patterns: `["path_exists", "all"]` says
 *  the rule gates on the presence of paths, and deliberately does not publish the
 *  globs and regexes themselves. `verify` and `warnings` are the rule's own
 *  post-action checks and the loader's complaints about it. */
export interface RulePlugin {
  id: string
  description: string
  stage: string
  /** Which directory this rule was read from: `builtin` for the rules IRIS ships,
   *  `external` for one installed through the workbench. Only an external rule can
   *  be uninstalled, so the two are never presented as the same kind of thing. */
  origin: 'builtin' | 'external'
  /** The file name inside that directory. A document's id and its file name are
   *  independent -- an uploaded document is stored under its id -- so the name
   *  travels with the rule rather than being reconstructed from the id. */
  source_file: string
  detect: string[]
  actions: string[]
  verify: string[]
  warnings: string[]
  /** Repair-ledger tallies. These count repairs *recorded against* this rule id --
   *  which is not the same as "the rule matched", because the engine's per-run match
   *  report is not retained. See `rule_plugins` in `web_data.py`. */
  applied: number
  promoted: number
  recorded: number
}

export interface RuleLibrary {
  /** Every directory rules are read from, in precedence order, so an empty list is
   *  explainable: the page can name which paths it looked in. Two entries rather
   *  than one because an installed plugin lives outside the source tree. */
  dirs: string[]
  items: RulePlugin[]
}

/** `POST /api/v1/plugins`: what the installer actually put on disk. `source_file`
 *  is not necessarily the uploaded name -- a `.yml` is stored under the rule's id. */
export interface PluginInstalled {
  id: string
  origin: string
  source_file: string
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
/* ---------------------------------------------------------------- corpus views */

/**
 * The three views the dashboard could not answer from the stat cards: *which
 * firmware*, *how long*, and *where the failures are by architecture*.
 *
 * Every field below is the server's own, and `tests/test_web_corpus_views.py`
 * compares these declarations with the pydantic models key by key. There is no
 * generator behind this file, so a field renamed on one side only would arrive as
 * 200 with a blank cell and nothing else would notice.
 */

export interface FirmwareRow {
  image_id: number | null
  label: string
  /** Closed on the server, with two non-architecture values in it: `mixed` when the
   *  runs of one firmware were measured as more than one architecture, `?` when they
   *  recorded none. A single architecture for either would invent agreement. */
  arch: 'armel' | 'arm64' | 'mipseb' | 'mipsel' | 'mixed' | '?'
  target_type: string
  runs: number
  web_ok: number
  last_result_kind: string
  /** The newest runs, truncated. */
  run_ids: number[]
  run_ids_total: number
  run_ids_truncated: boolean
}

export interface CorpusTotals {
  firmwares: number
  runs: number
  web_ok: number
  web_reach_rate: number
}

export interface CorpusView {
  firmwares: FirmwareRow[]
  /** The runs no registered firmware owns, as one row of its own rather than folded
   *  into the rows above: `attribute_to_image` stores a NULL instead of the nearest
   *  candidate precisely because a wrong attribution corrupts every per-firmware
   *  number and a missing one is visible. */
  unattributed: FirmwareRow | null
  totals: CorpusTotals
  note: string
}

export interface ArchLatencyRow {
  arch: string
  samples: number
  /** Nullable means "no sample at all" for this architecture, never zero. */
  min_sec: number | null
  median_sec: number | null
  p90_sec: number | null
  max_sec: number | null
  values: number[]
  truncated: boolean
}

export interface LatencyView {
  by_arch: ArchLatencyRow[]
  /** Runs that never reached a working web plane, so they have no duration to
   *  report. An absent bar would otherwise read as "no slow runs". */
  unmeasured: number
  note: string
}

export interface FailureCell {
  kind: string
  count: number
  per_arch: Record<string, number>
}

export interface FailureStageRow {
  stage: string
  total: number
  cells: FailureCell[]
}

export interface FailureMatrix {
  stages: FailureStageRow[]
  archs: string[]
  kind_totals: Record<string, number>
  /** Failing signals whose stage resolved to nothing. Reported rather than dropped,
   *  because a total that does not add up looks like a complete picture. */
  unclassified: number
  note: string
}
/** `iris.llm.schema` -- the rule document a model proposes, still YAML text.
 *  Text rather than parsed structure, because the engine's grammar has exactly
 *  one authority and a second parser in the browser would be a second opinion. */
export interface PluginDraft {
  rule_id: string
  stage: string
  description: string
  yaml: string
}

/** `iris.llm.schema.LLMDecision` -- three actions, not five.
 *  The guardian's other recovery actions take their parameters from the serial-log
 *  pattern counts, and a round trip only reaches the model when those counts said
 *  nothing, so a model-nominated parameter set would be derived from less evidence
 *  than the derivation already in the rule engine. */
export type LlmAction = 'NONE' | 'WEB_SERVER_RESTART' | 'DRAFT_PLUGIN'

export interface LlmDecision {
  diagnosis: string
  action: LlmAction
  plugin_draft: PluginDraft | null
  /** The model's own claim, shown next to the decision and never gating it. */
  confidence: number
  verify_plan: string[]
}

/** `POST /api/v1/ai/diagnose`. Three ways to run and one shape for all of them,
 *  because the page has to say which one happened: `llm_used: false` with a
 *  `rule_recommendation` means the rules engine already handled it, while `false`
 *  with a `disabled_reason` or an `error` means nothing ran at all. */
export interface DiagnosisResponse {
  iid: number
  llm_used: boolean
  decision: LlmDecision | null
  rule_recommendation: string | null
  disabled_reason: string | null
  error: string | null
  note: string
}

/** `GET /api/v1/ai/status` -- the key is never here, only whether it is set. */
export interface AiStatus {
  state: 'disabled' | 'ready' | 'unreachable'
  base_url_configured: boolean
  model: string
  note: string
}

/** One draft waiting on a person, with the document text itself: a review that
 *  cannot read what it is confirming is a rubber stamp. */
export interface PluginDraftView {
  rule_id: string
  stage: string
  description: string
  status: 'pending' | 'accepted' | 'rejected'
  created_at: string
  source_iid: number | null
  confidence: number
  diagnosis: string
  yaml: string
}

export interface DraftList {
  drafts: PluginDraftView[]
  note: string
}

/** `POST /api/v1/ai/drafts` -- the body of "save this decision's draft". `iid`
 *  and `confidence` are optional because a draft saved by hand has no run behind
 *  it, and the provenance is what makes the promoted-repair record possible. */
export interface DraftSaveRequest {
  rule_id: string
  stage: string
  description: string
  yaml: string
  iid?: number | null
  confidence?: number
  diagnosis?: string
}

/** `POST /api/v1/ai/drafts/{rule_id}/install`. `promoted_recorded` is a separate
 *  fact from `installed`: the engine accepting the document and the repair ledger
 *  gaining a `source="llm", promoted=True` row coincide only when the run that
 *  produced the draft still exists. */
export interface DraftInstallResponse {
  installed: PluginInstalled
  promoted_recorded: boolean
}

/** The two one-word acknowledgements, each echoing the id so a page holding
 *  several drafts can tell which one the answer is about. */
export interface DraftMutationResponse {
  saved: string
}

export interface DraftRemovalResponse {
  removed: string
}
