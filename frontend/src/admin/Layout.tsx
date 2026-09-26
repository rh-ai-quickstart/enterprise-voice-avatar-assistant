import {
  Badge,
  Button,
  Flex,
  Label,
  Masthead,
  MastheadBrand,
  MastheadContent,
  MastheadMain,
  MastheadToggle,
  Nav,
  NavItem,
  NavList,
  Page,
  PageSidebar,
  PageSidebarBody,
  PageToggleButton,
} from "@patternfly/react-core";
import { BarsIcon } from "@patternfly/react-icons/dist/esm/icons/bars-icon";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, type AnchorHTMLAttributes, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { api } from "./api";
import type { Me, StreamMessage } from "./types";
import { useEventStream, type StreamState } from "./useEventStream";

/** What a stream message makes stale; react-query refetches what is on screen. */
export function invalidateFor(client: QueryClient, message: StreamMessage) {
  client.invalidateQueries({ queryKey: ["overview"] });
  client.invalidateQueries({ queryKey: ["activity"] });
  if (message.ref_type === "ticket" || message.kind.startsWith("ticket.")) {
    client.invalidateQueries({ queryKey: ["tickets"] });
    if (message.ref_id) client.invalidateQueries({ queryKey: ["ticket", message.ref_id] });
    // A ticket's conversation lists it, and its notices
    client.invalidateQueries({ queryKey: ["conversation"] });
  }
  if (message.ref_type === "conversation") {
    client.invalidateQueries({ queryKey: ["conversations"] });
    if (message.ref_id) client.invalidateQueries({ queryKey: ["conversation", message.ref_id] });
  }
}

// PatternFly's NavItem renders its own anchor with href; these render the router's Link instead.
// NavItem still needs `to`: without it, it cancels the click before the Link can navigate.
const linkTo = (to: string) =>
  function NavLinkTo({ href: _href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
    return <Link {...props} to={to} />;
  };
const SECTIONS = [
  { to: "/", label: "Overview", component: linkTo("/") },
  { to: "/approvals", label: "Approvals", component: linkTo("/approvals") },
  { to: "/tickets", label: "Tickets", component: linkTo("/tickets") },
  { to: "/conversations", label: "Conversations", component: linkTo("/conversations") },
];

const STREAM_LABELS: Record<StreamState, { text: string; color: "green" | "grey" | "orange"; title: string }> = {
  live: { text: "Live", color: "green", title: "Changes appear as they happen" },
  connecting: { text: "Connecting", color: "grey", title: "Opening the live update stream" },
  polling: {
    text: "Refreshing every 30 s",
    color: "orange",
    title: "The live update stream is not available (a proxy that buffers, or the RAG API restarting); the portal polls instead",
  },
};

export function Layout({ me, onSignOut, children }: { me: Me; onSignOut: () => void; children: ReactNode }) {
  const client = useQueryClient();
  const location = useLocation();
  const stream = useEventStream(
    (message) => invalidateFor(client, message),
    // The stream was refused: an expired session answers 401, which sends the portal to sign-in
    () => client.invalidateQueries({ queryKey: ["me"] }),
  );
  useEffect(() => {
    client.setDefaultOptions({ queries: { refetchInterval: stream === "polling" ? 30_000 : false } });
  }, [client, stream]);
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  const pending = overview.data?.pending.count ?? 0;
  const status = STREAM_LABELS[stream];
  const active = (to: string) => (to === "/" ? location.pathname === "/" : location.pathname.startsWith(to));

  const masthead = (
    <Masthead>
      <MastheadMain>
        <MastheadToggle>
          <PageToggleButton variant="plain" aria-label="Navigation">
            <BarsIcon />
          </PageToggleButton>
        </MastheadToggle>
        <MastheadBrand>
          <strong className="admin-brand">Enterprise Assistant · Admin</strong>
        </MastheadBrand>
      </MastheadMain>
      <MastheadContent>
        <Flex justifyContent={{ default: "justifyContentFlexEnd" }} alignItems={{ default: "alignItemsCenter" }} style={{ width: "100%" }}>
          <Label color={status.color} isCompact title={status.title}>
            {status.text}
          </Label>
          <span>{me.name}</span>
          <Button variant="secondary" size="sm" onClick={onSignOut}>
            Sign out
          </Button>
        </Flex>
      </MastheadContent>
    </Masthead>
  );
  const sidebar = (
    <PageSidebar>
      <PageSidebarBody>
        <Nav aria-label="Sections">
          <NavList>
            {SECTIONS.map((s) => (
              <NavItem key={s.to} to={s.to} isActive={active(s.to)} component={s.component}>
                {s.label}
                {s.to === "/approvals" && pending > 0 && (
                  <>
                    {" "}
                    <Badge isRead={false}>{pending}</Badge>
                  </>
                )}
              </NavItem>
            ))}
          </NavList>
        </Nav>
      </PageSidebarBody>
    </PageSidebar>
  );
  return (
    <Page masthead={masthead} sidebar={sidebar} isManagedSidebar>
      {children}
    </Page>
  );
}
