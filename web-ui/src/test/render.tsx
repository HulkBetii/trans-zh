import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { ConfirmProvider } from "../components/ConfirmDialog";

export function renderApp(ui: ReactElement, route: string | string[] = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const initialEntries = Array.isArray(route) ? route : [route];
  const router = createMemoryRouter([{ path: "*", element: ui }], {
    initialEntries,
    initialIndex: initialEntries.length - 1,
  });
  const result = render(
    <QueryClientProvider client={queryClient}>
      <ConfirmProvider>
        <RouterProvider router={router} />
      </ConfirmProvider>
    </QueryClientProvider>,
  );
  return { ...result, queryClient, router };
}

export function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}
