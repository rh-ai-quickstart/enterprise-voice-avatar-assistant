import { Bullseye, Spinner } from "@patternfly/react-core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, Route, Routes } from "react-router";
import { ApiError, api } from "./api";
import { Layout } from "./Layout";
import { ApprovalsPage } from "./pages/Approvals";
import { ConversationPage } from "./pages/Conversation";
import { ConversationsPage } from "./pages/Conversations";
import { Login } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";
import { TicketsPage } from "./pages/Tickets";

/** Signed in: the portal. Not signed in (or the session expired): the sign-in page. */
export function App() {
  const client = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    retry: false,
    refetchInterval: false,
  });

  if (me.isPending) {
    return (
      <Bullseye style={{ height: "100vh" }}>
        <Spinner aria-label="Loading" />
      </Bullseye>
    );
  }
  if (!me.data) {
    return (
      <Routes>
        <Route path="/login" element={<Login onSignedIn={(m) => client.setQueryData(["me"], m)} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }
  const signOut = async () => {
    await api.logout().catch(() => undefined);
    client.clear();
    client.setQueryData(["me"], null);
  };
  return (
    <Layout me={me.data} onSignOut={signOut}>
      <Routes>
        <Route path="/" element={<OverviewPage />} />
        <Route path="/approvals" element={<ApprovalsPage />} />
        <Route path="/approvals/:ref" element={<ApprovalsPage />} />
        <Route path="/tickets" element={<TicketsPage />} />
        <Route path="/tickets/:ref" element={<TicketsPage />} />
        <Route path="/conversations" element={<ConversationsPage />} />
        <Route path="/conversations/:id" element={<ConversationPage />} />
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}
