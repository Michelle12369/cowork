import { useSuspenseQuery } from '@tanstack/react-query';
import { fetchAppConfig } from '@/api/configApi';
import type { AppConfig } from '@/api/configApi';

export const useAppConfig = (): AppConfig => {
  const { data } = useSuspenseQuery({
    queryKey: ['appConfig'],
    queryFn: fetchAppConfig,
    staleTime: Infinity,
  });
  return data;
};
