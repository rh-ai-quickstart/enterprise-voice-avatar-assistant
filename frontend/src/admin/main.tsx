import "@patternfly/react-core/dist/styles/base.css";
import "./admin.css";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { ApiError } from "./api";
import { App } from "./App";
import { ToastProvider } from "./components/Toasts";

const client: QueryClient = new QueryClient({
  queryCache: new QueryCache({
    // Any call answered 401 means the session is gone: back to sign-in
    onError: (error) => {
      if (error instanceof ApiError && error.status === 401) client.setQueryData(["me"], null);
    },
  }),
  defaultOptions: { queries: { staleTime: 5_000, retry: (count, error) => !(error instanceof ApiError) && count < 2 } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter basename="/admin">
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
