import { useQuery } from "@tanstack/react-query";
import { fetchOverview, fetchReadyz } from "./client";

export const overviewQueryKey = ["overview"] as const;
export const readyzQueryKey = ["readyz"] as const;

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
