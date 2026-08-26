// ServerDataWrapper — seeds a React Query cache with server-fetched data so
// client components can read it without a refetch. In the Next.js reference
// this hydrated the query cache from SSR; in this SPA there is no SSR, but
// the component is kept for parity so pages that use it still compile.

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import type { ReactNode } from "react";

interface ServerDataWrapperProps {
  children: ReactNode;
  initialData: Array<{
    queryKey: string[];
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    data: any;
  }>;
}

export function ServerDataWrapper({ children, initialData }: ServerDataWrapperProps) {
  const queryClient = useQueryClient();

  useEffect(() => {
    initialData.forEach(({ queryKey, data }) => {
      queryClient.setQueryData(queryKey, data);
    });
  }, [queryClient, initialData]);

  return children;
}
