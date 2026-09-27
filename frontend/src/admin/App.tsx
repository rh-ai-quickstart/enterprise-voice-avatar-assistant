import { Bullseye, PageSection, Spinner } from "@patternfly/react-core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router";
import { ApiError, api } from "./api";
import { Layout } from "./Layout";
import { Login } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";

// Every section but the overview loads when it is first opened, so signing in does not wait for
// the tables, drawers and upload code of pages that may never be visited
const ActivityPage = lazy(() => import("./pages/Activity").then((m) => ({ default: m.ActivityPage })));
const ApprovalsPage = lazy(() => import("./pages/Approvals").then((m) => ({ default: m.ApprovalsPage })));
const AuditPage = lazy(() => import("./pages/Audit").then((m) => ({ default: m.AuditPage })));
const ConversationPage = lazy(() => import("./pages/Conversation").then((m) => ({ default: m.ConversationPage })));
const ConversationsPage = lazy(() => import("./pages/Conversations").then((m) => ({ default: m.ConversationsPage })));
const DocumentsPage = lazy(() => import("./pages/Documents").then((m) => ({ default: m.DocumentsPage })));
const IntegrationsPage = lazy(() => import("./pages/Integrations").then((m) => ({ default: m.IntegrationsPage })));
const KnowledgeGapsPage = lazy(() => import("./pages/KnowledgeGaps").then((m) => ({ default: m.KnowledgeGapsPage })));
const TicketsPage = lazy(() => import("./pages/Tickets").then((m) => ({ default: m.TicketsPage })));

const loading = (
  <PageSection>
    <Spinner aria-label="Loading" />
  </PageSection>
);

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
    // The sign-in first, so the portal gives way to the sign-in page (clearing the whole cache
    // would detach the query above from it); then everything else that belonged to the session
    client.setQueryData(["me"], null);
    client.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
  };
  return (
    <Layout me={me.data} onSignOut={signOut}>
      <Suspense fallback={loading}>
        <Routes>
          <Route path="/" element={<OverviewPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/approvals/:ref" element={<ApprovalsPage />} />
          <Route path="/tickets" element={<TicketsPage />} />
          <Route path="/tickets/:ref" element={<TicketsPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/conversations/:id" element={<ConversationPage />} />
          <Route path="/gaps" element={<KnowledgeGapsPage />} />
          <Route path="/documents" element={<Navigate to="/documents/indexed" replace />} />
          <Route path="/documents/:tab" element={<DocumentsPage />} />
          <Route path="/activity" element={<ActivityPage />} />
          <Route path="/integrations" element={<IntegrationsPage />} />
          <Route path="/audit" element={<AuditPage />} />
          <Route path="/login" element={<Navigate to="/" replace />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Suspense>
    </Layout>
  );
}
