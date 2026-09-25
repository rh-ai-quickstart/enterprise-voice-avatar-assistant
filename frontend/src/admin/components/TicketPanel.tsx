import {
  Alert,
  Content,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Divider,
  DrawerActions,
  DrawerCloseButton,
  DrawerHead,
  DrawerPanelBody,
  DrawerPanelContent,
  Flex,
  Spinner,
  Title,
} from "@patternfly/react-core";
import { ExternalLinkAltIcon } from "@patternfly/react-icons/dist/esm/icons/external-link-alt-icon";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { duration, when, words } from "../format";
import type { TicketDetail } from "../types";
import { SlaBadge, StatusLabel } from "./Labels";
import { TicketActions } from "./TicketActions";

// Payload keys shown elsewhere in the panel, or bookkeeping
const HIDDEN_PAYLOAD = new Set(["channel", "summary", "slack", "decision", "model_needs_approval"]);

/** The ticket drawer: fields, classification, events, conversation, Slack card, actions. */
export function TicketPanel({ ticketRef, onClose }: { ticketRef: string; onClose: () => void }) {
  const detail = useQuery({ queryKey: ["ticket", ticketRef], queryFn: () => api.ticket(ticketRef) });
  return (
    <DrawerPanelContent widths={{ default: "width_50" }} isResizable minSize="420px">
      <DrawerHead>
        <Title headingLevel="h2" size="lg">
          {ticketRef}
          {detail.data ? ` · ${detail.data.title}` : ""}
        </Title>
        <DrawerActions>
          <DrawerCloseButton onClick={onClose} />
        </DrawerActions>
      </DrawerHead>
      <DrawerPanelBody>
        {detail.isPending && <Spinner size="lg" aria-label="Loading the ticket" />}
        {detail.isError && <Alert variant="danger" isInline title={detail.error.message} />}
        {detail.data && <Details ticket={detail.data} />}
      </DrawerPanelBody>
    </DrawerPanelContent>
  );
}

function Details({ ticket }: { ticket: TicketDetail }) {
  const summary = typeof ticket.payload.summary === "string" ? ticket.payload.summary : null;
  const extracted = Object.entries(ticket.payload).filter(([k]) => !HIDDEN_PAYLOAD.has(k));
  const decision = ticket.payload.decision as { via?: string; by?: string } | undefined;
  return (
    <Flex direction={{ default: "column" }} spaceItems={{ default: "spaceItemsLg" }}>
      <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
        <StatusLabel status={ticket.status} />
        <SlaBadge minutes={ticket.pending_minutes} sla={ticket.sla} />
        {ticket.slack?.permalink && (
          <a href={ticket.slack.permalink} target="_blank" rel="noreferrer">
            Slack card <ExternalLinkAltIcon />
          </a>
        )}
      </Flex>
      {ticket.actions.length > 0 && <TicketActions ticket={ticket} actions={ticket.actions} />}
      <DescriptionList isHorizontal isCompact>
        <Field term="Requester">{ticket.requester ?? "-"}</Field>
        <Field term="Channel">{ticket.channel ?? "-"}</Field>
        <Field term="Category">{words(ticket.category)}</Field>
        <Field term="Priority">{ticket.priority}</Field>
        <Field term="Filed">{when(ticket.created_at)}</Field>
        <Field term="Last change">{when(ticket.updated_at)}</Field>
        {ticket.approver && (
          <Field term="Decided by">
            {ticket.approver}
            {decision?.via ? ` (${decision.via === "portal" ? "in the portal" : `in ${decision.via}`})` : ""}
          </Field>
        )}
        {ticket.decision_note && <Field term="Decision note">{ticket.decision_note}</Field>}
      </DescriptionList>
      {(summary || ticket.description) && (
        <Content>
          {summary && <p>{summary}</p>}
          {ticket.description && ticket.description !== summary && (
            <blockquote>{ticket.description}</blockquote>
          )}
        </Content>
      )}
      {extracted.length > 0 && (
        <>
          <Title headingLevel="h3" size="md">
            From the classification
          </Title>
          <DescriptionList isHorizontal isCompact>
            {extracted.map(([k, v]) => (
              <Field key={k} term={words(k)}>
                {typeof v === "string" ? v : JSON.stringify(v)}
              </Field>
            ))}
          </DescriptionList>
        </>
      )}
      <Divider />
      <Title headingLevel="h3" size="md">
        Conversation
      </Title>
      {ticket.conversation ? (
        <DescriptionList isHorizontal isCompact>
          <Field term="Session">
            <code>{ticket.conversation.session_id}</code>
            {!ticket.conversation.exists && " (conversation deleted)"}
          </Field>
          {ticket.conversation.exists && (
            <>
              <Field term="Messages">{ticket.conversation.messages}</Field>
              <Field term="Last activity">{when(ticket.conversation.last_activity)}</Field>
            </>
          )}
        </DescriptionList>
      ) : (
        <Content component="p">Not filed from a conversation.</Content>
      )}
      <Divider />
      <Title headingLevel="h3" size="md">
        History
      </Title>
      <ol className="admin-timeline">
        {ticket.events.map((e, i) => (
          <li key={i}>
            <span className="admin-timeline__when">{when(e.created_at)}</span>{" "}
            {e.from_status === e.to_status ? (
              <strong>note</strong>
            ) : (
              <strong>{e.from_status ? `${words(e.from_status)} → ${words(e.to_status)}` : words(e.to_status)}</strong>
            )}
            {e.actor && <> by {e.actor}</>}
            {e.note && <div className="admin-timeline__note">{e.note}</div>}
          </li>
        ))}
      </ol>
      {ticket.status === "approved" && ticket.pending_minutes === null && (
        <Content component="small">
          Approved {duration((Date.now() - new Date(ticket.updated_at).getTime()) / 60000)} ago; the workflow
          normally fulfils it within seconds.
        </Content>
      )}
    </Flex>
  );
}

function Field({ term, children }: { term: string; children: React.ReactNode }) {
  return (
    <DescriptionListGroup>
      <DescriptionListTerm>{term}</DescriptionListTerm>
      <DescriptionListDescription>{children}</DescriptionListDescription>
    </DescriptionListGroup>
  );
}
