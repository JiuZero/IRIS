import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'

/** Poll intervals, named so a page can say why it chose one.
 *
 *  The stat cards and the instance resource panel poll because they show a running
 *  process; the run table and the capability list do not, because their answers only
 *  change when someone runs something -- which is a navigation, not a wait.
 */
export const POLL = {
  live: 3_000,
  resource: 2_000,
  console: 4_000,
} as const

export function useStats() {
  return useQuery({
    queryKey: ['stats'],
    queryFn: api.stats,
    // 3s matches the container-stat cache on the server, so two panels showing the
    // same instance agree instead of straddling two different samples.
    refetchInterval: POLL.live,
  })
}

export function useCapabilities() {
  return useQuery({ queryKey: ['capabilities'], queryFn: api.capabilities, staleTime: 30_000 })
}

/**
 * The live host reading, shared by the dashboard strip and the sidebar meter.
 *
 * One query for both, because they are describing the same sample: two requests a
 * second apart are served from the server's TTL cache anyway, so splitting them
 * would buy nothing and would let the sidebar disagree with the strip. The poll
 * interval matches the server's default TTL, so a redraw that arrives just inside
 * it gets the figure the previous one showed rather than a half-updated pair.
 */
export function useSystem() {
  return useQuery({
    queryKey: ['system'],
    queryFn: api.system,
    refetchInterval: POLL.live,
  })
}

export function useEvalSet() {
  return useQuery({ queryKey: ['eval-set'], queryFn: api.evalSet, staleTime: 15_000 })
}

export function useConfig() {
  return useQuery({ queryKey: ['config'], queryFn: api.config, staleTime: 60_000 })
}

export function useRootCauses(recent = 10) {
  return useQuery({ queryKey: ['root-causes', recent], queryFn: () => api.rootCauses(recent), staleTime: 15_000 })
}

/** The instances this browser owns. The list view, the sidebar and the command
 *  palette all read this one query, so a stop in one place is a stop everywhere
 *  without a refetch storm. */
export function useEmulations() {
  return useQuery({
    queryKey: ['emulations'],
    queryFn: api.listEmulations,
    refetchInterval: POLL.live,
  })
}

export function useInstanceStats(iid: number | null) {
  return useQuery({
    queryKey: ['instance-stats', iid],
    queryFn: () => api.instanceStats(iid as number),
    // Disabled rather than skipped for a null iid: a disabled query keeps its
    // previous data, which is what stops the panel flashing empty while the user
    // switches between instances.
    enabled: iid !== null,
    refetchInterval: POLL.resource,
  })
}

export function useConsoleLog(iid: number | null, enabled: boolean) {
  return useQuery({
    queryKey: ['console', iid],
    queryFn: () => api.console(iid as number),
    enabled: enabled && iid !== null,
    refetchInterval: POLL.console,
  })
}

export function useFirmware() {
  return useQuery({ queryKey: ['firmware'], queryFn: api.firmware, staleTime: 30_000 })
}
/** The rule plugin library, read from `rules/` and the plugin directory on the
 *  server.
 *
 *  Not polled: the documents only change when a package is rebuilt or a plugin is
 *  installed, and both of those go through this file's mutations, which invalidate
 *  the query. The tallies it carries do move -- a launch records a repair against a
 *  rule id -- and they move on the pages that invalidate after one, which is why the
 *  plugin page is invalidated with the rest rather than left polling. */
export function useRules() {
  return useQuery({ queryKey: ['rules'], queryFn: api.rules, staleTime: 60_000 })
}

/** Install a rule plugin, then re-read the library.
 *
 *  Invalidating rather than pushing the new item into the cache: the listing is the
 *  engine's own view, and after an install the server has re-read the document from
 *  disk to decide whether to accept it at all. A cache patched locally would show a
 *  plugin the engine had not yet agreed to load. */
export function useInstallPlugin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => api.installPlugin(file),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['rules'] })
    },
  })
}

/** Uninstall a rule plugin, then re-read the library for the same reason. */
export function useRemovePlugin() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.removePlugin(name),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['rules'] })
    },
  })
}

/** One recorded run, in full: the four-layer evidence, the failure profiles and the
 *  repair ledger. Disabled at `null` so the record window can be mounted closed
 *  without a request for `/api/v1/runs/null`. */
export function useRun(runId: number | null) {
  return useQuery({
    queryKey: ['run', runId],
    queryFn: () => api.run(runId as number),
    enabled: runId !== null,
  })
}