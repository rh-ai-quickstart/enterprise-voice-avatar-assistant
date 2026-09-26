import {
  Alert,
  Button,
  EmptyState,
  FormSelect,
  FormSelectOption,
  Label,
  PageSection,
  Pagination,
  SearchInput,
  TextInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarGroup,
  ToolbarItem,
} from "@patternfly/react-core";
import { Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fragment, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, type ConversationFilters } from "../api";
import { Highlighted } from "../components/Highlighted";
import { when } from "../format";

const FILTERS = ["q", "user", "channel", "from", "to", "has_ticket", "archived", "blocked"] as const;
type FilterName = (typeof FILTERS)[number];

/** The filters in the query string as API parameters; dates are whole days ("to" included). */
export function conversationFilters(params: URLSearchParams): ConversationFilters {
  const filters: ConversationFilters = {};
  for (const name of FILTERS) {
    const value = params.get(name);
    if (value) filters[name] = value;
  }
  if (filters.from) filters.from = new Date(`${filters.from}T00:00:00`).toISOString();
  if (filters.to) {
    const next = new Date(`${filters.to}T00:00:00`);
    next.setDate(next.getDate() + 1);
    filters.to = next.toISOString();
  }
  filters.page = Number(params.get("page") ?? 1) || 1;
  filters.limit = Number(params.get("limit") ?? 50) || 50;
  return filters;
}

export function ConversationsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const filters = conversationFilters(params);
  const list = useQuery({
    queryKey: ["conversations", filters],
    queryFn: () => api.conversations(filters),
    placeholderData: keepPreviousData,
  });
  const set = (name: FilterName | "page" | "limit", value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    if (name !== "page") next.delete("page");
    setParams(next);
  };
  const filtered = FILTERS.some((f) => params.get(f));
  const searching = Boolean(params.get("q"));

  return (
    <PageSection>
      <Title headingLevel="h1">Conversations</Title>
      <Toolbar clearAllFilters={() => setParams(new URLSearchParams())}>
        <ToolbarContent>
          <ToolbarItem>
            <Search value={params.get("q") ?? ""} onApply={(v) => set("q", v)} />
          </ToolbarItem>
          <ToolbarGroup variant="filter-group">
            <Choice name="channel" label="Channel" value={params.get("channel")} options={[["chat", "chat"], ["voice", "voice"]]} onChange={set} />
            <Choice name="has_ticket" label="Tickets" value={params.get("has_ticket")} options={[["true", "filed a ticket"], ["false", "no ticket"]]} onChange={set} />
            <Choice name="archived" label="Archived" value={params.get("archived")} options={[["true", "archived"], ["false", "not archived"]]} onChange={set} />
            <Choice name="blocked" label="Guardrails" value={params.get("blocked")} options={[["true", "had a blocked message"], ["false", "nothing blocked"]]} onChange={set} />
          </ToolbarGroup>
          <ToolbarItem>
            <UserFilter value={params.get("user") ?? ""} onApply={(v) => set("user", v)} />
          </ToolbarItem>
          <ToolbarItem>
            <TextInput type="date" aria-label="Started from" value={params.get("from") ?? ""} onChange={(_e, v) => set("from", v)} />
          </ToolbarItem>
          <ToolbarItem>
            <TextInput type="date" aria-label="Started until" value={params.get("to") ?? ""} onChange={(_e, v) => set("to", v)} />
          </ToolbarItem>
          {filtered && (
            <ToolbarItem>
              <Button variant="link" onClick={() => setParams(new URLSearchParams())}>
                Clear filters
              </Button>
            </ToolbarItem>
          )}
          <ToolbarItem variant="pagination" align={{ default: "alignEnd" }}>
            <Pagination
              itemCount={list.data?.total ?? 0}
              page={filters.page}
              perPage={filters.limit}
              perPageOptions={[20, 50, 100].map((n) => ({ title: String(n), value: n }))}
              onSetPage={(_e, p) => set("page", String(p))}
              onPerPageSelect={(_e, n) => set("limit", String(n))}
              isCompact
            />
          </ToolbarItem>
        </ToolbarContent>
      </Toolbar>
      {list.isError && <Alert variant="danger" isInline title={list.error.message} />}
      <Table aria-label="Conversations" variant="compact">
        <Thead>
          <Tr>
            <Th>User</Th>
            <Th>Channel</Th>
            <Th>Started</Th>
            <Th>Last activity</Th>
            <Th>Messages</Th>
            <Th>Tickets</Th>
            <Th>Archived</Th>
            <Th>Blocked</Th>
          </Tr>
        </Thead>
        <Tbody>
          {list.data?.items.map((c) => (
            <Fragment key={c.session_id}>
              <Tr isClickable onRowClick={() => navigate(`/conversations/${c.session_id}`)}>
                <Td dataLabel="User">{c.user_id ?? <em>anonymous</em>}</Td>
                <Td dataLabel="Channel">{c.channel ?? "-"}</Td>
                <Td dataLabel="Started">{when(c.started)}</Td>
                <Td dataLabel="Last activity">{when(c.last_activity)}</Td>
                <Td dataLabel="Messages">{c.messages}</Td>
                <Td dataLabel="Tickets">{c.tickets || "-"}</Td>
                <Td dataLabel="Archived">{c.archives ? <Label isCompact color="blue">{c.archives}</Label> : "-"}</Td>
                <Td dataLabel="Blocked">{c.blocked ? <Label isCompact color="red">{c.blocked}</Label> : "-"}</Td>
              </Tr>
              {searching && c.matches.length > 0 && (
                <Tr className="admin-matches">
                  <Td colSpan={8}>
                    {c.matches.map((m) => (
                      <div key={m.id}>
                        <strong>{m.role === "user" ? "User" : "Assistant"}:</strong> <Highlighted text={m.snippet} />
                      </div>
                    ))}
                  </Td>
                </Tr>
              )}
            </Fragment>
          ))}
        </Tbody>
      </Table>
      {list.data && list.data.items.length === 0 && (
        <EmptyState titleText={filtered ? "No conversation matches" : "No conversations yet"} headingLevel="h2" />
      )}
    </PageSection>
  );
}

function Choice(props: {
  name: FilterName;
  label: string;
  value: string | null;
  options: [string, string][];
  onChange: (name: FilterName, value: string) => void;
}) {
  return (
    <ToolbarItem>
      <FormSelect aria-label={props.label} value={props.value ?? ""} onChange={(_e, v) => props.onChange(props.name, v)}>
        <FormSelectOption value="" label={`${props.label}: any`} />
        {props.options.map(([value, label]) => (
          <FormSelectOption key={value} value={value} label={label} />
        ))}
      </FormSelect>
    </ToolbarItem>
  );
}

/** Full-text search: words, "a phrase", -excluded, applied on Enter. */
function Search({ value, onApply }: { value: string; onApply: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <SearchInput
      aria-label="Search every message"
      placeholder='Search every message: words, "a phrase", -without'
      value={text}
      onChange={(_e, v) => setText(v)}
      onSearch={(_e, v) => onApply(v.trim())}
      onClear={() => {
        setText("");
        onApply("");
      }}
    />
  );
}

function UserFilter({ value, onApply }: { value: string; onApply: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <TextInput
      aria-label="User"
      placeholder="User"
      value={text}
      onChange={(_e, v) => setText(v)}
      onBlur={() => text.trim() !== value && onApply(text.trim())}
      onKeyDown={(e) => e.key === "Enter" && onApply(text.trim())}
    />
  );
}
