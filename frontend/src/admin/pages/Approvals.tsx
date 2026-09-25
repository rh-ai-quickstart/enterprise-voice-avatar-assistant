import {
  Alert,
  Drawer,
  DrawerContent,
  DrawerContentBody,
  EmptyState,
  EmptyStateBody,
  PageSection,
  Title,
} from "@patternfly/react-core";
import { CheckCircleIcon } from "@patternfly/react-icons/dist/esm/icons/check-circle-icon";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router";
import { api } from "../api";
import { SlaBadge } from "../components/Labels";
import { TicketActions } from "../components/TicketActions";
import { TicketPanel } from "../components/TicketPanel";
import { words } from "../format";

const DEFAULT_SLA = { reminder_minutes: 60, escalation_minutes: 240 };

/** Requests waiting for a decision, oldest first, with Approve and Reject on each row. */
export function ApprovalsPage() {
  const { ref } = useParams();
  const navigate = useNavigate();
  const pending = useQuery({
    queryKey: ["tickets", { status: "pending_approval", order: "oldest" }],
    queryFn: () => api.tickets({ status: "pending_approval", order: "oldest", limit: 200 }),
  });
  const overview = useQuery({ queryKey: ["overview"], queryFn: api.overview });
  const sla = overview.data?.sla ?? DEFAULT_SLA;

  return (
    <Drawer isExpanded={Boolean(ref)} isInline>
      <DrawerContent
        panelContent={ref ? <TicketPanel ticketRef={ref} onClose={() => navigate("/approvals")} /> : undefined}
      >
        <DrawerContentBody>
          <PageSection>
            <Title headingLevel="h1">Approvals</Title>
            {pending.isError && <Alert variant="danger" isInline title={pending.error.message} />}
            {pending.data && pending.data.items.length === 0 ? (
              <EmptyState titleText="Nothing waits for approval" icon={CheckCircleIcon} headingLevel="h2">
                <EmptyStateBody>New requests from the chat and voice appear here as they are filed.</EmptyStateBody>
              </EmptyState>
            ) : (
              <Table aria-label="Requests waiting for approval" variant="compact">
                <Thead>
                  <Tr>
                    <Th>Reference</Th>
                    <Th>Request</Th>
                    <Th>Requester</Th>
                    <Th>Category</Th>
                    <Th>Priority</Th>
                    <Th>Channel</Th>
                    <Th>Waiting</Th>
                    <Th screenReaderText="Decision" />
                  </Tr>
                </Thead>
                <Tbody>
                  {pending.data?.items.map((t) => (
                    <Tr key={t.id} isClickable isRowSelected={t.ticket_ref === ref} onRowClick={() => navigate(`/approvals/${t.ticket_ref}`)}>
                      <Td dataLabel="Reference">{t.ticket_ref}</Td>
                      <Td dataLabel="Request">{t.title}</Td>
                      <Td dataLabel="Requester">{t.requester ?? "-"}</Td>
                      <Td dataLabel="Category">{words(t.category)}</Td>
                      <Td dataLabel="Priority">{t.priority}</Td>
                      <Td dataLabel="Channel">{t.channel ?? "-"}</Td>
                      <Td dataLabel="Waiting">
                        <SlaBadge minutes={t.pending_minutes} sla={sla} />
                      </Td>
                      <Td dataLabel="Decision" onClick={(e) => e.stopPropagation()}>
                        <TicketActions ticket={t} actions={["approve", "reject"]} />
                      </Td>
                    </Tr>
                  ))}
                </Tbody>
              </Table>
            )}
          </PageSection>
        </DrawerContentBody>
      </DrawerContent>
    </Drawer>
  );
}
