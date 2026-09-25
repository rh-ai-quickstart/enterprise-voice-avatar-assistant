import {
  Alert,
  Card,
  CardBody,
  CardFooter,
  CardTitle,
  Content,
  Flex,
  Gallery,
  Label,
  List,
  ListItem,
  PageSection,
  Spinner,
  Title,
} from "@patternfly/react-core";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api";
import { SeverityLabel, SlaBadge } from "../components/Labels";
import { when, words } from "../format";
import { STATUSES } from "../types";

export function OverviewPage() {
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  if (overview.isPending) return <PageSection><Spinner aria-label="Loading" /></PageSection>;
  if (overview.isError) return <PageSection><Alert variant="danger" isInline title={overview.error.message} /></PageSection>;
  const o = overview.data;
  const week = STATUSES.filter((s) => o.tickets_last_7_days[s]);

  return (
    <PageSection>
      <Title headingLevel="h1">Overview</Title>
      <Gallery hasGutter minWidths={{ default: "260px" }} style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
        <Card isCompact>
          <CardTitle>Waiting for approval</CardTitle>
          <CardBody>
            <div className="admin-tile__number">{o.pending.count}</div>
            {o.pending.oldest_ref && (
              <Flex spaceItems={{ default: "spaceItemsSm" }}>
                <span>oldest {o.pending.oldest_ref}</span>
                <SlaBadge minutes={o.pending.oldest_minutes} sla={o.sla} />
              </Flex>
            )}
          </CardBody>
          <CardFooter>
            <Link to="/approvals">Open approvals</Link>
          </CardFooter>
        </Card>
        <Card isCompact>
          <CardTitle>Approved, not fulfilled after 5 minutes</CardTitle>
          <CardBody>
            <div className="admin-tile__number">{o.approved_not_fulfilled.count}</div>
            {o.approved_not_fulfilled.count > 0 && (
              <Content component="small">Fulfilment did not run (was n8n down?). Mark them fulfilled once done.</Content>
            )}
          </CardBody>
          <CardFooter>
            <Link to="/tickets?status=approved">Approved tickets</Link>
          </CardFooter>
        </Card>
        <Card isCompact>
          <CardTitle>Tickets in the last 7 days</CardTitle>
          <CardBody>
            {week.length === 0 ? (
              <Content component="small">None filed.</Content>
            ) : (
              <List isPlain>
                {week.map((s) => (
                  <ListItem key={s}>
                    <Link to={`/tickets?status=${s}`}>{words(s)}</Link>: {o.tickets_last_7_days[s]}
                  </ListItem>
                ))}
              </List>
            )}
          </CardBody>
        </Card>
        <Card isCompact>
          <CardTitle>Integrations</CardTitle>
          <CardBody>
            <List isPlain>
              <ListItem>
                Slack <Label isCompact color={o.integrations.slack ? "green" : "grey"}>{o.integrations.slack ? "on" : "off"}</Label>
              </ListItem>
              <ListItem>
                Google Docs{" "}
                <Label isCompact color={o.integrations.google_docs ? "green" : "grey"}>
                  {o.integrations.google_docs ? "on" : "off"}
                </Label>
              </ListItem>
            </List>
            {!o.integrations.slack && <Content component="small">Requests are approved here.</Content>}
          </CardBody>
        </Card>
      </Gallery>
      <Card isCompact style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
        <CardTitle>Recent activity</CardTitle>
        <CardBody>
          {o.recent_activity.length === 0 ? (
            <Content component="small">Nothing yet.</Content>
          ) : (
            <List isPlain isBordered>
              {o.recent_activity.map((e) => (
                <ListItem key={e.id}>
                  <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
                    <SeverityLabel severity={e.severity} />
                    <span className="admin-timeline__when">{when(e.created_at)}</span>
                    {e.ref_type === "ticket" && e.ref_id ? <Link to={`/tickets/${e.ref_id}`}>{e.title}</Link> : <span>{e.title}</span>}
                  </Flex>
                </ListItem>
              ))}
            </List>
          )}
        </CardBody>
      </Card>
    </PageSection>
  );
}
