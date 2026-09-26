import {
  Alert,
  Button,
  Drawer,
  DrawerContent,
  DrawerContentBody,
  EmptyState,
  FormSelect,
  FormSelectOption,
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
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { api, type TicketFilters } from "../api";
import { StatusLabel } from "../components/Labels";
import { TicketPanel } from "../components/TicketPanel";
import { when, words } from "../format";
import { CATEGORIES, CHANNELS, PRIORITIES, STATUSES } from "../types";

const FILTERS = ["q", "status", "category", "priority", "channel", "requester", "from", "to"] as const;
type FilterName = (typeof FILTERS)[number];

/** The filters in the query string (so a filtered view can be bookmarked) as API parameters. */
export function apiFilters(params: URLSearchParams): TicketFilters {
  const filters: TicketFilters = {};
  for (const name of FILTERS) {
    const value = params.get(name);
    if (value) filters[name] = value;
  }
  // "to" is the last day included
  if (filters.to) {
    const next = new Date(`${filters.to}T00:00:00`);
    next.setDate(next.getDate() + 1);
    filters.to = next.toISOString();
  }
  if (filters.from) filters.from = new Date(`${filters.from}T00:00:00`).toISOString();
  filters.page = Number(params.get("page") ?? 1) || 1;
  filters.limit = Number(params.get("limit") ?? 50) || 50;
  return filters;
}

export function TicketsPage() {
  const { ref } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const filters = apiFilters(params);
  const tickets = useQuery({
    queryKey: ["tickets", filters],
    queryFn: () => api.tickets(filters),
    placeholderData: keepPreviousData,
  });

  const set = (name: FilterName | "page" | "limit", value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    if (name !== "page") next.delete("page");
    setParams(next);
  };
  const open = (r: string) => navigate({ pathname: `/tickets/${r}`, search: params.toString() });
  const close = () => navigate({ pathname: "/tickets", search: params.toString() });
  const filtered = FILTERS.some((f) => params.get(f));

  return (
    <Drawer isExpanded={Boolean(ref)} isInline>
      <DrawerContent panelContent={ref ? <TicketPanel ticketRef={ref} onClose={close} /> : undefined}>
        <DrawerContentBody>
          <PageSection>
            <Title headingLevel="h1">Tickets</Title>
            <Toolbar clearAllFilters={() => setParams(new URLSearchParams())} collapseListedFiltersBreakpoint="xl">
              <ToolbarContent>
                <ToolbarItem>
                  <TextFilter
                    name="q"
                    label="Search title, description or reference"
                    value={params.get("q") ?? ""}
                    onApply={(v) => set("q", v)}
                    search
                  />
                </ToolbarItem>
                <ToolbarGroup variant="filter-group">
                  <SelectFilter name="status" label="Status" value={params.get("status")} options={STATUSES} onChange={set} />
                  <SelectFilter name="category" label="Category" value={params.get("category")} options={CATEGORIES} onChange={set} />
                  <SelectFilter name="priority" label="Priority" value={params.get("priority")} options={PRIORITIES} onChange={set} />
                  <SelectFilter name="channel" label="Channel" value={params.get("channel")} options={CHANNELS} onChange={set} />
                </ToolbarGroup>
                <ToolbarItem>
                  <TextFilter name="requester" label="Requester" value={params.get("requester") ?? ""} onApply={(v) => set("requester", v)} />
                </ToolbarItem>
                <ToolbarItem>
                  <TextInput type="date" aria-label="Filed from" value={params.get("from") ?? ""} onChange={(_e, v) => set("from", v)} />
                </ToolbarItem>
                <ToolbarItem>
                  <TextInput type="date" aria-label="Filed until" value={params.get("to") ?? ""} onChange={(_e, v) => set("to", v)} />
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
                    itemCount={tickets.data?.total ?? 0}
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
            {tickets.isError && <Alert variant="danger" isInline title={tickets.error.message} />}
            <Table aria-label="Tickets" variant="compact">
              <Thead>
                <Tr>
                  <Th>Reference</Th>
                  <Th>Title</Th>
                  <Th>Status</Th>
                  <Th>Priority</Th>
                  <Th>Category</Th>
                  <Th>Requester</Th>
                  <Th>Channel</Th>
                  <Th>Filed</Th>
                </Tr>
              </Thead>
              <Tbody>
                {tickets.data?.items.map((t) => (
                  <Tr key={t.id} isClickable isRowSelected={t.ticket_ref === ref} onRowClick={() => open(t.ticket_ref)}>
                    <Td dataLabel="Reference">{t.ticket_ref}</Td>
                    <Td dataLabel="Title">{t.title}</Td>
                    <Td dataLabel="Status">
                      <StatusLabel status={t.status} />
                    </Td>
                    <Td dataLabel="Priority">{t.priority}</Td>
                    <Td dataLabel="Category">{words(t.category)}</Td>
                    <Td dataLabel="Requester">{t.requester ?? "-"}</Td>
                    <Td dataLabel="Channel">{t.channel ?? "-"}</Td>
                    <Td dataLabel="Filed">{when(t.created_at)}</Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
            {tickets.data && tickets.data.items.length === 0 && (
              <EmptyState titleText={filtered ? "No ticket matches these filters" : "No tickets yet"} headingLevel="h2" />
            )}
          </PageSection>
        </DrawerContentBody>
      </DrawerContent>
    </Drawer>
  );
}

function SelectFilter(props: {
  name: FilterName;
  label: string;
  value: string | null;
  options: string[];
  onChange: (name: FilterName, value: string) => void;
}) {
  return (
    <ToolbarItem>
      <FormSelect
        aria-label={props.label}
        value={props.value ?? ""}
        onChange={(_e, v) => props.onChange(props.name, v)}
      >
        <FormSelectOption value="" label={`${props.label}: any`} />
        {props.options.map((o) => (
          <FormSelectOption key={o} value={o} label={words(o)} />
        ))}
      </FormSelect>
    </ToolbarItem>
  );
}

/** A text filter applied on Enter (or when cleared), not on every keystroke. */
function TextFilter(props: { name: string; label: string; value: string; onApply: (v: string) => void; search?: boolean }) {
  const [text, setText] = useState(props.value);
  useEffect(() => setText(props.value), [props.value]);
  if (props.search) {
    return (
      <SearchInput
        aria-label={props.label}
        placeholder={props.label}
        value={text}
        onChange={(_e, v) => setText(v)}
        onSearch={(_e, v) => props.onApply(v.trim())}
        onClear={() => {
          setText("");
          props.onApply("");
        }}
      />
    );
  }
  return (
    <TextInput
      aria-label={props.label}
      placeholder={props.label}
      value={text}
      onChange={(_e, v) => setText(v)}
      onBlur={() => text.trim() !== props.value && props.onApply(text.trim())}
      onKeyDown={(e) => e.key === "Enter" && props.onApply(text.trim())}
    />
  );
}
