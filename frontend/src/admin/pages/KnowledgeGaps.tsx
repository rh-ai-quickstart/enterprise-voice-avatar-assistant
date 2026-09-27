import {
  Alert,
  Button,
  Content,
  EmptyState,
  Flex,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Label,
  List,
  ListItem,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  PageSection,
  Spinner,
  TextArea,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { ExpandableRowContent, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api } from "../api";
import { useToast } from "../components/Toasts";
import { when, words } from "../format";
import type { Gap, GapGroup, GapStatus } from "../types";

const WINDOWS = [
  ["1", "Last 24 hours"],
  ["7", "Last 7 days"],
  ["30", "Last 30 days"],
  ["90", "Last 90 days"],
];
const STATUS_COLORS = { open: "orange", resolved: "green", dismissed: "grey" } as const;
const VERBS: Record<GapStatus, [string, string]> = {
  resolved: ["Resolve", "resolved"],
  dismissed: ["Dismiss", "dismissed"],
  open: ["Reopen", "reopened"],
};

interface Change {
  ids: number[];
  status: GapStatus;
  question: string;
}

export function KnowledgeGapsPage() {
  const [params, setParams] = useSearchParams();
  const status = (params.get("status") ?? "open") as GapStatus | "all";
  const days = params.get("days") ?? "7";
  // Fixed while the window is: a new "from" on every render would be a new query each time
  const from = useMemo(() => new Date(Date.now() - Number(days) * 86_400_000).toISOString(), [days]);
  const list = useQuery({
    queryKey: ["gaps", status, from],
    queryFn: () => api.gaps({ status, from }),
    placeholderData: keepPreviousData,
  });
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [change, setChange] = useState<Change | null>(null);
  const [retest, setRetest] = useState<Gap | null>(null);
  const set = (name: "status" | "days", value: string) => {
    const next = new URLSearchParams(params);
    next.set(name, value);
    setParams(next);
  };
  const toggle = (key: number) =>
    setExpanded((all) => {
      const next = new Set(all);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const groups = list.data?.groups ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1">Knowledge gaps</Title>
      <Content component="p">
        Questions the assistant could not answer from the documents, grouped by meaning. Upload a document that answers
        them, re-test, then resolve them; dismiss the ones that need no answer. Resolved and dismissed questions leave the
        daily digest.
      </Content>
      <Toolbar>
        <ToolbarContent>
          <ToolbarItem>
            <FormSelect aria-label="Status" value={status} onChange={(_e, v) => set("status", v)}>
              {["open", "resolved", "dismissed", "all"].map((s) => (
                <FormSelectOption key={s} value={s} label={s === "all" ? "Any status" : s} />
              ))}
            </FormSelect>
          </ToolbarItem>
          <ToolbarItem>
            <FormSelect aria-label="Window" value={days} onChange={(_e, v) => set("days", v)}>
              {WINDOWS.map(([value, label]) => (
                <FormSelectOption key={value} value={value} label={label} />
              ))}
            </FormSelect>
          </ToolbarItem>
          {list.data && (
            <ToolbarItem alignSelf="center">
              <Content component="small">
                {list.data.total} questions in {groups.length} groups
              </Content>
            </ToolbarItem>
          )}
        </ToolbarContent>
      </Toolbar>
      {list.isPending && <Spinner aria-label="Loading the knowledge gaps" />}
      {list.isError && <Alert variant="danger" isInline title={list.error.message} />}
      {list.isSuccess && groups.length === 0 && (
        <EmptyState titleText={status === "open" ? "No open knowledge gaps" : "No knowledge gaps"} headingLevel="h2">
          Every question in this window found an answer in the documents.
        </EmptyState>
      )}
      {groups.length > 0 && (
        <Table aria-label="Knowledge gaps" variant="compact">
          <Thead>
            <Tr>
              <Th screenReaderText="Expand" />
              <Th>Question</Th>
              <Th>Asked</Th>
              <Th modifier="wrap">Best score</Th>
              <Th>Reasons</Th>
              <Th>First asked</Th>
              <Th>Last asked</Th>
              <Th screenReaderText="Actions" />
            </Tr>
          </Thead>
          {groups.map((g, rowIndex) => {
            const key = g.ids[0];
            const isExpanded = expanded.has(key);
            return (
              <Tbody key={key} isExpanded={isExpanded}>
                <Tr>
                  <Td expand={{ rowIndex, isExpanded, onToggle: () => toggle(key), expandId: "gap-group" }} />
                  <Td dataLabel="Question">{g.question}</Td>
                  <Td dataLabel="Asked">
                    {g.count}×{g.wordings > 1 && <Content component="small"> in {g.wordings} wordings</Content>}
                  </Td>
                  <Td dataLabel="Best score">{g.best_score.toFixed(2)}</Td>
                  <Td dataLabel="Reasons">
                    <Flex spaceItems={{ default: "spaceItemsXs" }}>
                      {g.reasons.map((r) => (
                        <Label key={r} isCompact>
                          {words(r)}
                        </Label>
                      ))}
                    </Flex>
                  </Td>
                  <Td dataLabel="First asked" className="admin-timeline__when">
                    {when(g.first_seen)}
                  </Td>
                  <Td dataLabel="Last asked" className="admin-timeline__when">
                    {when(g.last_seen)}
                  </Td>
                  <Td dataLabel="Actions" isActionCell modifier="nowrap">
                    <GroupActions group={g} onChange={setChange} />
                  </Td>
                </Tr>
                <Tr isExpanded={isExpanded}>
                  <Td colSpan={8}>
                    <ExpandableRowContent>
                      <GapRows gaps={g.gaps} onChange={setChange} onRetest={setRetest} />
                    </ExpandableRowContent>
                  </Td>
                </Tr>
              </Tbody>
            );
          })}
        </Table>
      )}
      {change && <ChangeDialog change={change} onClose={() => setChange(null)} />}
      {retest && <RetestDialog gap={retest} onClose={() => setRetest(null)} />}
    </PageSection>
  );
}

function GroupActions({ group, onChange }: { group: GapGroup; onChange: (c: Change) => void }) {
  const open = group.gaps.filter((x) => x.status === "open").map((x) => x.id);
  const closed = group.gaps.filter((x) => x.status !== "open").map((x) => x.id);
  const ask = (ids: number[], status: GapStatus) => onChange({ ids, status, question: group.question });
  return (
    <Flex spaceItems={{ default: "spaceItemsSm" }} flexWrap={{ default: "nowrap" }} className="admin-nowrap">
      {open.length > 0 && (
        <>
          <Button variant="secondary" size="sm" onClick={() => ask(open, "resolved")}>
            Resolve
          </Button>
          <Button variant="tertiary" size="sm" onClick={() => ask(open, "dismissed")}>
            Dismiss
          </Button>
        </>
      )}
      {closed.length > 0 && (
        <Button variant="tertiary" size="sm" onClick={() => ask(closed, "open")}>
          Reopen
        </Button>
      )}
    </Flex>
  );
}

function GapRows({ gaps, onChange, onRetest }: { gaps: Gap[]; onChange: (c: Change) => void; onRetest: (g: Gap) => void }) {
  return (
    <Table aria-label="Questions in this group" variant="compact" borders={false}>
      <Thead>
        <Tr>
          <Th>Question</Th>
          <Th>Asked</Th>
          <Th>Score</Th>
          <Th>Reason</Th>
          <Th>Status</Th>
          <Th modifier="wrap">Conversation</Th>
          <Th screenReaderText="Actions" />
        </Tr>
      </Thead>
      <Tbody>
        {gaps.map((x) => (
          <Tr key={x.id}>
            <Td dataLabel="Question">{x.question}</Td>
            <Td dataLabel="Asked" className="admin-timeline__when">
              {when(x.created_at)}
            </Td>
            <Td dataLabel="Score">{x.top_score.toFixed(2)}</Td>
            <Td dataLabel="Reason">{words(x.reason)}</Td>
            <Td dataLabel="Status">
              <Label isCompact color={STATUS_COLORS[x.status]}>
                {x.status}
              </Label>
              {x.resolved_by && (
                <Content component="small">
                  {" "}
                  by {x.resolved_by}
                  {x.resolution_note && `: ${x.resolution_note}`}
                </Content>
              )}
            </Td>
            <Td dataLabel="Conversation">
              {x.session_id ? <Link to={`/conversations/${encodeURIComponent(x.session_id)}`}>{x.session_id.slice(0, 8)}</Link> : "-"}
            </Td>
            <Td dataLabel="Actions" isActionCell modifier="nowrap">
              <Flex spaceItems={{ default: "spaceItemsSm" }} flexWrap={{ default: "nowrap" }} className="admin-nowrap">
                <Button variant="link" size="sm" isInline onClick={() => onRetest(x)}>
                  Re-test
                </Button>
                {x.status === "open" ? (
                  <>
                    <Button variant="link" size="sm" isInline onClick={() => onChange({ ids: [x.id], status: "resolved", question: x.question })}>
                      Resolve
                    </Button>
                    <Button variant="link" size="sm" isInline onClick={() => onChange({ ids: [x.id], status: "dismissed", question: x.question })}>
                      Dismiss
                    </Button>
                  </>
                ) : (
                  <Button variant="link" size="sm" isInline onClick={() => onChange({ ids: [x.id], status: "open", question: x.question })}>
                    Reopen
                  </Button>
                )}
              </Flex>
            </Td>
          </Tr>
        ))}
      </Tbody>
    </Table>
  );
}

function ChangeDialog({ change, onClose }: { change: Change; onClose: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [verb, done] = VERBS[change.status];
  const n = change.ids.length;
  const save = useMutation({
    mutationFn: () => api.resolveGaps(change.ids, change.status, note.trim() || undefined),
    onSuccess: (r) => {
      client.invalidateQueries({ queryKey: ["gaps"] });
      client.invalidateQueries({ queryKey: ["overview"] });
      toast("success", `${r.changed} question${r.changed === 1 ? "" : "s"} ${done}.`);
      onClose();
    },
    onError: (e: Error) => toast("danger", e.message),
  });
  return (
    <Modal variant="small" isOpen onClose={onClose} aria-labelledby="gap-change-title">
      <ModalHeader title={`${verb} ${n === 1 ? "this question" : `${n} questions`}?`} labelId="gap-change-title" />
      <ModalBody>
        <Content component="p">“{change.question}”</Content>
        {change.status !== "open" && (
          <FormGroup label="Note (optional)" fieldId="gap-note">
            <TextArea
              id="gap-note"
              value={note}
              onChange={(_e, v) => setNote(v)}
              placeholder={change.status === "resolved" ? "For example the document added that answers it" : "For example: out of scope"}
              resizeOrientation="vertical"
            />
          </FormGroup>
        )}
      </ModalBody>
      <ModalFooter>
        <Button onClick={() => save.mutate()} isLoading={save.isPending} isDisabled={save.isPending}>
          {verb}
        </Button>
        <Button variant="link" onClick={onClose}>
          Cancel
        </Button>
      </ModalFooter>
    </Modal>
  );
}

function RetestDialog({ gap, onClose }: { gap: Gap; onClose: () => void }) {
  // A query, not a mutation: it changes nothing, runs once when the dialog opens, and again on demand
  const run = useQuery({
    queryKey: ["retest", gap.id],
    queryFn: () => api.retest(gap.id),
    refetchOnMount: "always",
    staleTime: Infinity,
    refetchInterval: false,
    retry: false,
  });
  const r = run.data;
  return (
    <Modal variant="medium" isOpen onClose={onClose} aria-labelledby="retest-title">
      <ModalHeader title="Re-test" labelId="retest-title" description={`“${gap.question}”`} />
      <ModalBody>
        {run.isFetching && <Spinner size="lg" aria-label="Searching the documents" />}
        {run.isError && <Alert variant="danger" isInline title={run.error.message} />}
        {r && !run.isFetching && (
          <>
            <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
              <Label color={r.answered ? "green" : "orange"}>{r.answered ? "Answered now" : "Still a gap"}</Label>
              <span>
                {`best score ${r.best_score.toFixed(2)} against the threshold ${r.threshold.toFixed(2)}`}
                {` (${r.recorded_score.toFixed(2)} when it was asked)`}
              </span>
            </Flex>
            <Content component="small">Retrieval only: the passages the assistant would answer from, without calling the language model.</Content>
            {r.hits.length === 0 ? (
              <Content component="p">No passage found.</Content>
            ) : (
              <List isPlain isBordered>
                {r.hits.map((h) => (
                  <ListItem key={h.n}>
                    <strong>{h.source}</strong>
                    {h.page ? `, page ${h.page}` : ""} <span className="admin-timeline__when">score {h.score.toFixed(2)}</span>
                    <div className="admin-timeline__note">{h.snippet}</div>
                  </ListItem>
                ))}
              </List>
            )}
          </>
        )}
      </ModalBody>
      <ModalFooter>
        <Button variant="secondary" onClick={() => run.refetch()} isDisabled={run.isFetching}>
          Test again
        </Button>
        <Button variant="link" onClick={onClose}>
          Close
        </Button>
      </ModalFooter>
    </Modal>
  );
}
