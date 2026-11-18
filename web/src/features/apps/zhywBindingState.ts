import { useQuery } from '@tanstack/react-query';
import { getZhywBinding } from '../../api/systemEntry';
import { useAuthStore } from '../../stores/authStore';

export const zhywBindingKey = (generation: number) => ['zhyw-binding', generation] as const;

export function useZhywBinding(enabled = true) {
  const generation = useAuthStore((state) => state.generation);
  return useQuery({
    queryKey: zhywBindingKey(generation),
    queryFn: ({ signal }) => getZhywBinding(signal),
    enabled,
    retry: false,
  });
}
