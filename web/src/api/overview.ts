import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { fetchEvaluations } from "./evaluations";
import { fetchOverview, fetchReadyz } from "./client";

export const overviewQueryKey = ["overview"] as const;
export const readyzQueryKey = ["readyz"] as const;
export const recentEvaluationsQueryKey = (page: number, pageSize: number) =>
  ["overview", "recent-evaluations", page, pageSize] as const;

export function useOverviewQuery() {
  return useQuery({
    queryKey: overviewQueryKey,
    queryFn: ({ signal }) => fetchOverview(signal),
    staleTime: 15_000,
    retry: 1,
  });
}

export function useReadyzQuery() {
  return useQuery({
    queryKey: readyzQueryKey,
    queryFn: ({ signal }) => fetchReadyz(signal),
    staleTime: 30_000,
    retry: false,
    refetchInterval: 60_000,
  });
}

/**
 * Server-paginated recent evaluation runs.
 *
 * The API already returns `{ total, items }` for the requested window, so the
 * page only has to supply the offset; keeping previous data avoids the table
 * collapsing to an empty placeholder while the next page loads.
 */
export function useRecentEvaluationsQuery(page: number, pageSize = 5) {
  return useQuery({
    queryKey: recentEvaluationsQueryKey(page, pageSize),
    queryFn: ({ signal }) =>
      fetchEvaluations({ limit: pageSize, offset: (page - 1) * pageSize }, signal),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
    retry: 1,
  });
}
