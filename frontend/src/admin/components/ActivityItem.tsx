import { Content, Flex, Label } from "@patternfly/react-core";
import { Link } from "react-router";
import { when } from "../format";
import type { ActivityEvent } from "../types";
import { SeverityLabel } from "./Labels";

/** Where an event or audit entry's reference is shown in the portal. */
export function refPath(refType: string | null, refId: string | null): string | null {
  if (refType === "gap") return "/gaps";
  if (refType === "integration") return "/integrations";
  if (!refId) return null;
  if (refType === "ticket") return `/tickets/${encodeURIComponent(refId)}`;
  if (refType === "conversation") return `/conversations/${encodeURIComponent(refId)}`;
  if (refType === "document") return `/documents/indexed?doc=${encodeURIComponent(refId)}`;
  return null;
}

/** One activity event: severity, time and title linking to what it is about; with `full`, also
 * its kind, source and detail. */
export function ActivityItem({ event, full = false }: { event: ActivityEvent; full?: boolean }) {
  const to = refPath(event.ref_type, event.ref_id);
  return (
    <>
      <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
        <SeverityLabel severity={event.severity} />
        <span className="admin-timeline__when">{when(event.created_at)}</span>
        {to ? <Link to={to}>{event.title}</Link> : <span>{event.title}</span>}
        {full && (
          <Label isCompact variant="outline" title={`from ${event.source}`}>
            {event.kind}
          </Label>
        )}
      </Flex>
      {full && event.detail && (
        <Content component="small" className="admin-timeline__note admin-pre">
          {event.detail}
        </Content>
      )}
    </>
  );
}
