import {
  Alert,
  Breadcrumb,
  BreadcrumbItem,
  Button,
  Card,
  CardBody,
  CardTitle,
  Content,
  ExpandableSection,
  Flex,
  Label,
  List,
  ListItem,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  PageSection,
  Spinner,
  Title,
} from "@patternfly/react-core";
import { ExternalLinkAltIcon } from "@patternfly/react-icons/dist/esm/icons/external-link-alt-icon";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api";
import { StatusLabel } from "../components/Labels";
import { useToast } from "../components/Toasts";
import { when, words } from "../format";
import type { ConversationDetail } from "../types";

const ARCHIVE_COLORS = { requested: "grey", indexed: "green", failed: "red" } as const;

export function ConversationPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const client = useQueryClient();
  const toast = useToast();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const detail = useQuery({ queryKey: ["conversation", id], queryFn: () => api.conversation(id) });

  const refresh = () => {
    client.invalidateQueries({ queryKey: ["conversation", id] });
    client.invalidateQueries({ queryKey: ["conversations"] });
  };
  const archive = useMutation({
    mutationFn: () => api.archive(id),
    onSuccess: (r) => {
      refresh();
      if (!r.requested && r.reason) toast("warning", `Not archived: ${r.reason}.`);
      else if (!r.requested) toast("warning", "Archive recorded, but the archival workflow could not be reached.");
      else toast("success", `Archived${r.doc_url ? " to Google Docs" : ""}; re-ingestion runs now.`);
    },
    onError: (e: Error) => toast("danger", e.message),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteConversation(id),
    onSuccess: (r) => {
      client.invalidateQueries({ queryKey: ["conversations"] });
      client.removeQueries({ queryKey: ["conversation", id] });
      toast("success", `Conversation deleted: ${r.messages} messages, ${r.archives} archives.`);
      navigate("/conversations");
    },
    onError: (e: Error) => {
      setConfirmDelete(false);
      toast("danger", e.message);
    },
  });

  if (detail.isPending) return <PageSection><Spinner aria-label="Loading the conversation" /></PageSection>;
  if (detail.isError) return <PageSection><Alert variant="danger" isInline title={detail.error.message} /></PageSection>;
  const c = detail.data;

  return (
    <PageSection>
      <Breadcrumb>
        <BreadcrumbItem>
          <Link to="/conversations">Conversations</Link>
        </BreadcrumbItem>
        <BreadcrumbItem isActive>{c.session_id.slice(0, 8)}</BreadcrumbItem>
      </Breadcrumb>
      <Flex justifyContent={{ default: "justifyContentSpaceBetween" }} alignItems={{ default: "alignItemsCenter" }}>
        <Title headingLevel="h1">
          {c.user_id ?? "Anonymous"} · {c.channel ?? "-"} · {when(c.started)}
        </Title>
        <Flex spaceItems={{ default: "spaceItemsSm" }}>
          <Button variant="secondary" size="sm" onClick={() => archive.mutate()} isLoading={archive.isPending} isDisabled={archive.isPending}>
            Archive
          </Button>
          <Button variant="secondary" size="sm" component="a" href={api.exportUrl(id, "md")}>
            Export Markdown
          </Button>
          <Button variant="secondary" size="sm" component="a" href={api.exportUrl(id, "txt")}>
            Export text
          </Button>
          <Button variant="danger" size="sm" onClick={() => setConfirmDelete(true)}>
            Delete
          </Button>
        </Flex>
      </Flex>
      <Content component="small">
        Session <code>{c.session_id}</code>
      </Content>

      <Flex direction={{ default: "column", lg: "row" }} alignItems={{ lg: "alignItemsFlexStart" }} style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
        <Flex direction={{ default: "column" }} flex={{ default: "flex_2" }}>
          <Transcript conversation={c} />
        </Flex>
        <Flex direction={{ default: "column" }} flex={{ default: "flex_1" }}>
          <Side conversation={c} />
        </Flex>
      </Flex>

      {confirmDelete && (
        <Modal variant="small" isOpen onClose={() => setConfirmDelete(false)} aria-labelledby="delete-title">
          <ModalHeader title="Delete this conversation?" labelId="delete-title" titleIconVariant="danger" />
          <ModalBody>
            <List>
              <ListItem>
                Its {c.messages.length} messages, {c.notices.length} notices and {c.archives.length} archive records are deleted.
              </ListItem>
              {c.archives.length > 0 && <ListItem>The archived transcript is removed from the transcripts bucket and from search.</ListItem>}
              {c.tickets.length > 0 && (
                <ListItem>
                  Tickets filed from it are kept ({c.tickets.map((t) => t.ticket_ref).join(", ")}) and show the conversation as deleted.
                </ListItem>
              )}
              {c.archives.some((a) => a.doc_url) && (
                <ListItem>
                  The Google Docs are not deleted (they may already be shared):{" "}
                  {c.archives.filter((a) => a.doc_url).map((a) => (
                    <a key={a.id} href={a.doc_url!} target="_blank" rel="noreferrer">
                      {a.title}{" "}
                    </a>
                  ))}
                </ListItem>
              )}
            </List>
          </ModalBody>
          <ModalFooter>
            <Button variant="danger" onClick={() => remove.mutate()} isLoading={remove.isPending} isDisabled={remove.isPending}>
              Delete
            </Button>
            <Button variant="link" onClick={() => setConfirmDelete(false)}>
              Keep it
            </Button>
          </ModalFooter>
        </Modal>
      )}
    </PageSection>
  );
}

function Transcript({ conversation }: { conversation: ConversationDetail }) {
  if (conversation.messages.length === 0) return <Content component="p">No messages.</Content>;
  return (
    <div className="admin-transcript">
      {conversation.messages.map((m) => {
        const used = m.citations.filter((s) => s.used);
        return (
          <div key={m.id} className={`admin-message admin-message--${m.role}`}>
            <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
              <strong>{m.role === "user" ? "User" : m.role === "assistant" ? "Assistant" : m.role}</strong>
              <span className="admin-timeline__when">{when(m.created_at)}</span>
              {m.blocked && (
                <Label isCompact color="red">
                  blocked by the guardrail
                </Label>
              )}
            </Flex>
            <div className="admin-message__text">{m.content}</div>
            {m.citations.length > 0 && (
              <ExpandableSection toggleText={`Sources (${used.length} cited of ${m.citations.length})`}>
                <List isPlain>
                  {m.citations.map((s) => (
                    <ListItem key={s.n}>
                      <Label isCompact color={s.used ? "blue" : "grey"}>
                        [{s.n}]
                      </Label>{" "}
                      <strong>{s.source}</strong>
                      {s.page ? `, page ${s.page}` : ""} <span className="admin-timeline__when">score {s.score.toFixed(2)}</span>
                      <div className="admin-timeline__note">{s.snippet}</div>
                    </ListItem>
                  ))}
                </List>
              </ExpandableSection>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Side({ conversation: c }: { conversation: ConversationDetail }) {
  return (
    <>
      <Card isCompact>
        <CardTitle>Tickets filed</CardTitle>
        <CardBody>
          {c.tickets.length === 0 ? (
            <Content component="small">None.</Content>
          ) : (
            <List isPlain>
              {c.tickets.map((t) => (
                <ListItem key={t.ticket_ref}>
                  <Link to={`/tickets/${t.ticket_ref}`}>{t.ticket_ref}</Link> {t.title} <StatusLabel status={t.status} />
                </ListItem>
              ))}
            </List>
          )}
        </CardBody>
      </Card>
      <Card isCompact>
        <CardTitle>Archives</CardTitle>
        <CardBody>
          {c.archives.length === 0 ? (
            <Content component="small">Not archived.</Content>
          ) : (
            <List isPlain>
              {c.archives.map((a) => (
                <ListItem key={a.id}>
                  <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
                    <span>{when(a.created_at)}</span>
                    <Label isCompact color={ARCHIVE_COLORS[a.status]}>
                      {a.status}
                    </Label>
                    <a href={api.archiveUrl(c.session_id, a.id)}>Download</a>
                    {a.doc_url && (
                      <a href={a.doc_url} target="_blank" rel="noreferrer">
                        Google Doc <ExternalLinkAltIcon />
                      </a>
                    )}
                  </Flex>
                  {a.requested_by && <Content component="small">by {a.requested_by}</Content>}
                  {a.error && <Content component="small">{a.error}</Content>}
                </ListItem>
              ))}
            </List>
          )}
        </CardBody>
      </Card>
      <Card isCompact>
        <CardTitle>Notices to the requester</CardTitle>
        <CardBody>
          {c.notices.length === 0 ? (
            <Content component="small">None.</Content>
          ) : (
            <List isPlain>
              {c.notices.map((n) => (
                <ListItem key={n.id}>
                  <span className="admin-timeline__when">{when(n.created_at)}</span>{" "}
                  <Label isCompact color={n.delivered_at ? "green" : "orange"}>
                    {n.delivered_at ? "delivered" : "waiting"}
                  </Label>{" "}
                  {words(n.kind)}
                  <div className="admin-timeline__note">{n.text}</div>
                </ListItem>
              ))}
            </List>
          )}
        </CardBody>
      </Card>
    </>
  );
}
