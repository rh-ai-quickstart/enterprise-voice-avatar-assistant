import {
  Alert,
  Button,
  EmptyState,
  FormSelect,
  FormSelectOption,
  PageSection,
  Spinner,
  TextInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type AuditFilters } from "../api";
import { refPath } from "../components/ActivityItem";
import { dayAfter, dayStart, when } from "../format";
import { AUDIT_ACTIONS, type AuditEntry } from "../types";

const FILTERS = ["actor", "action", "from", "to"] as const;

export function auditFilters(params: URLSearchParams): AuditFilters {
  const filters: AuditFilters = {};
  const [actor, action, from, to] = FILTERS.map((f) => params.get(f));
  if (actor) filters.actor = actor;
  if (action) filters.action = action;
  if (from) filters.from = dayStart(from);
  if (to) filters.to = dayAfter(to);
  return filters;
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 160 ? `${text.slice(0, 160)}…` : text;
}

/** What changed: each key whose value differs, before struck through and after. */
export function Changes({ before, after }: { before: AuditEntry["before"]; after: AuditEntry["after"] }) {
  const keys = [...new Set([...Object.keys(before ?? {}), ...Object.keys(after ?? {})])];
  const changed = keys.filter((k) => JSON.stringify(before?.[k]) !== JSON.stringify(after?.[k]));
  if (changed.length === 0) return <span>-</span>;
  return (
    <div className="admin-diff">
      {changed.map((k) => (
        <div key={k}>
          {k}:{" "}
          {before && k in before && (
            <>
              <del>{show(before[k])}</del>
              {after && k in after && " → "}
            </>
          )}
          {after && k in after && <ins>{show(after[k])}</ins>}
        </div>
      ))}
    </div>
  );
}

export function AuditPage() {
  const [params, setParams] = useSearchParams();
  const filters = auditFilters(params);
  const log = useInfiniteQuery({
    queryKey: ["audit", filters],
    queryFn: ({ pageParam }) => api.audit({ ...filters, before_id: pageParam, limit: 50 }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => last.next_before_id ?? undefined,
  });
  const set = (name: (typeof FILTERS)[number], value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next);
  };
  const filtered = FILTERS.some((f) => params.get(f));
  const entries = log.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1">Audit</Title>
      <Toolbar clearAllFilters={() => setParams(new URLSearchParams())}>
        <ToolbarContent>
          <ToolbarItem>
            <ActorFilter value={params.get("actor") ?? ""} onApply={(v) => set("actor", v)} />
          </ToolbarItem>
          <ToolbarItem>
            <FormSelect aria-label="Action" value={params.get("action") ?? ""} onChange={(_e, v) => set("action", v)}>
              <FormSelectOption value="" label="Action: any" />
              {AUDIT_ACTIONS.map((a) => (
                <FormSelectOption key={a} value={a} label={a} />
              ))}
            </FormSelect>
          </ToolbarItem>
          <ToolbarItem>
            <TextInput type="date" aria-label="From" value={params.get("from") ?? ""} onChange={(_e, v) => set("from", v)} />
          </ToolbarItem>
          <ToolbarItem>
            <TextInput type="date" aria-label="Until" value={params.get("to") ?? ""} onChange={(_e, v) => set("to", v)} />
          </ToolbarItem>
          {filtered && (
            <ToolbarItem>
              <Button variant="link" onClick={() => setParams(new URLSearchParams())}>
                Clear filters
              </Button>
            </ToolbarItem>
          )}
        </ToolbarContent>
      </Toolbar>
      {log.isPending && <Spinner aria-label="Loading the audit log" />}
      {log.isError && <Alert variant="danger" isInline title={log.error.message} />}
      {log.isSuccess && entries.length === 0 && (
        <EmptyState titleText={filtered ? "No entry matches" : "Nothing recorded yet"} headingLevel="h2" />
      )}
      {entries.length > 0 && (
        <Table aria-label="Audit log" variant="compact">
          <Thead>
            <Tr>
              <Th>When</Th>
              <Th>Actor</Th>
              <Th>Action</Th>
              <Th>Target</Th>
              <Th>Changes</Th>
              <Th>Client</Th>
            </Tr>
          </Thead>
          <Tbody>
            {entries.map((e) => {
              const to = refPath(e.target_type, e.target_id);
              const target = e.target_type ? `${e.target_type} ${e.target_id ?? ""}`.trim() : "-";
              return (
                <Tr key={e.id}>
                  <Td dataLabel="When" className="admin-timeline__when">
                    {when(e.created_at)}
                  </Td>
                  <Td dataLabel="Actor">{e.actor}</Td>
                  <Td dataLabel="Action">{e.action}</Td>
                  <Td dataLabel="Target">{to ? <Link to={to}>{target}</Link> : target}</Td>
                  <Td dataLabel="Changes">
                    <Changes before={e.before} after={e.after} />
                  </Td>
                  <Td dataLabel="Client" title={e.user_agent ?? undefined}>
                    {e.client_ip ?? "-"}
                  </Td>
                </Tr>
              );
            })}
          </Tbody>
        </Table>
      )}
      {log.hasNextPage && (
        <Button
          variant="secondary"
          style={{ marginTop: "var(--pf-t--global--spacer--md)" }}
          onClick={() => log.fetchNextPage()}
          isLoading={log.isFetchingNextPage}
          isDisabled={log.isFetchingNextPage}
        >
          Show older
        </Button>
      )}
    </PageSection>
  );
}

function ActorFilter({ value, onApply }: { value: string; onApply: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <TextInput
      aria-label="Actor"
      placeholder="Actor"
      value={text}
      onChange={(_e, v) => setText(v)}
      onBlur={() => text.trim() !== value && onApply(text.trim())}
      onKeyDown={(e) => e.key === "Enter" && onApply(text.trim())}
    />
  );
}
