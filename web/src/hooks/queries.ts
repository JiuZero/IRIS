import { useQuery } from '@tanstack/react-query'

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