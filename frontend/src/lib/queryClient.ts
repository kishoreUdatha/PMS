import { QueryClient } from '@tanstack/react-query'

/** The application's one query cache.
 *
 *  With the library default (staleTime 0) every mount and every return to the
 *  tab refetched every query on screen: moving between two screens and back
 *  fetched the same lists each time. Thirty seconds is fresh enough for a
 *  front desk, and focus refetches still happen once data is older than that.
 *  Pollers never run in a background tab. */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: true,
      refetchIntervalInBackground: false,
    },
  },
})

/** Called by the HTTP client after any successful write.
 *
 *  Plenty of screens save and then navigate ("save, back to the list") and
 *  relied on the list refetching on mount, which staleTime would otherwise
 *  suppress for thirty seconds. Marking everything stale after a write keeps
 *  that behaviour without refetching anything now: `refetchType: 'none'`
 *  only flags queries, so the next screen to mount one fetches it fresh.
 *  Screens that invalidate specific keys themselves are unaffected. */
export function markAllStale(): void {
  void queryClient.invalidateQueries({ refetchType: 'none' })
}
