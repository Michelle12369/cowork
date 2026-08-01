import { useSuspenseQuery } from '@tanstack/react-query';
import { getSession } from '@/api/sessionApi';
import type { SessionDetail } from '@/types';

export function useSessionDetail(sessionId: string): SessionDetail {
  const { data } = useSuspenseQuery({
    queryKey: ['session', sessionId],
    queryFn: () => getSession(sessionId),
    // The seeded draft entry must not be background-refetched (GET would 404 until the first
    // send/upload persists it); every mutation path invalidates ['session', id] instead.
    staleTime: Infinity,
  });
  return data;
}
