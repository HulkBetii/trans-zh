import { useEffect, useState } from "react";

/**
 * Hoãn một giá trị cho tới khi nó ngừng đổi trong `delayMs`.
 *
 * Dùng khi giá trị là đầu vào của một request thật, không phải chỉ của render:
 * `useDeferredValue` giảm số lần vẽ lại nhưng không giảm số lần gọi mạng.
 */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [delayMs, value]);

  return debounced;
}
