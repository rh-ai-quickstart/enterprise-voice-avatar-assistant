import {
  Alert,
  Button,
  EmptyState,
  FormSelect,
  FormSelectOption,
  FormSelectOptionGroup,
  List,
  ListItem,
  PageSection,
  Spinner,
  TextInput,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api, type ActivityFilters } from "../api";
import { ActivityItem } from "../components/ActivityItem";
import { dayAfter, dayStart } from "../format";
import { EVENT_KINDS } from "../types";

const FILTERS = ["kind", "severity", "from", "to"] as const;

/** The filters in the query string as API parameters; dates are whole days ("to" included). */
export function activityFilters(params: URLSearchParams): ActivityFilters {
  const filters: ActivityFilters = {};
  const kind = params.get("kind");
  const severity = params.get("severity");
  const from = params.get("from");
  const to = params.get("to");
  if (kind) filters.kind = kind;
  if (severity) filters.severity = severity;
  if (from) filters.from = dayStart(from);
  if (to) filters.to = dayAfter(to);
  return filters;
}

export function ActivityPage() {
  const [params, setParams] = useSearchParams();
  const filters = activityFilters(params);
  // Newest first; "Show older" asks for the page before the last one shown. A stream message
  // refetches every page shown, so new events appear at the top.
  const feed = useInfiniteQuery({
    queryKey: ["activity", "feed", filters],
    queryFn: ({ pageParam }) => api.activity({ ...filters, before_id: pageParam, limit: 50 }),
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
  const events = feed.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <PageSection>
      <Title headingLevel="h1">Activity</Title>
      <Toolbar clearAllFilters={() => setParams(new URLSearchParams())}>
        <ToolbarContent>
          <ToolbarItem>
            <FormSelect aria-label="Kind" value={params.get("kind") ?? ""} onChange={(_e, v) => set("kind", v)}>
              <FormSelectOption value="" label="Kind: any" />
              {EVENT_KINDS.map(([section, kinds]) => (
                <FormSelectOptionGroup key={section} label={section}>
                  {kinds.map((k) => (
                    <FormSelectOption key={k} value={k} label={k} />
                  ))}
                </FormSelectOptionGroup>
              ))}
            </FormSelect>
          </ToolbarItem>
          <ToolbarItem>
            <FormSelect aria-label="Severity" value={params.get("severity") ?? ""} onChange={(_e, v) => set("severity", v)}>
              <FormSelectOption value="" label="Severity: any" />
              {["info", "success", "warning", "error"].map((s) => (
                <FormSelectOption key={s} value={s} label={s} />
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
      {feed.isPending && <Spinner aria-label="Loading the activity" />}
      {feed.isError && <Alert variant="danger" isInline title={feed.error.message} />}
      {feed.isSuccess && events.length === 0 && (
        <EmptyState titleText={filtered ? "No event matches" : "Nothing has happened yet"} headingLevel="h2" />
      )}
      {events.length > 0 && (
        <List isPlain isBordered aria-label="Activity">
          {events.map((e) => (
            <ListItem key={e.id}>
              <ActivityItem event={e} full />
            </ListItem>
          ))}
        </List>
      )}
      {feed.hasNextPage && (
        <Button
          variant="secondary"
          style={{ marginTop: "var(--pf-t--global--spacer--md)" }}
          onClick={() => feed.fetchNextPage()}
          isLoading={feed.isFetchingNextPage}
          isDisabled={feed.isFetchingNextPage}
        >
          Show older
        </Button>
      )}
    </PageSection>
  );
}
