import {
  Alert,
  Button,
  Content,
  DescriptionList,
  DescriptionListDescription,
  DescriptionListGroup,
  DescriptionListTerm,
  Drawer,
  DrawerActions,
  DrawerCloseButton,
  DrawerContent,
  DrawerContentBody,
  DrawerHead,
  DrawerPanelBody,
  DrawerPanelContent,
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
  MultipleFileUpload,
  MultipleFileUploadMain,
  PageSection,
  Pagination,
  Radio,
  SearchInput,
  Spinner,
  Tab,
  Tabs,
  TabTitleText,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarItem,
} from "@patternfly/react-core";
import { UploadIcon } from "@patternfly/react-icons/dist/esm/icons/upload-icon";
import { ExpandableRowContent, Table, Tbody, Td, Th, Thead, Tr } from "@patternfly/react-table";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Navigate, useNavigate, useParams, useSearchParams } from "react-router";
import { ApiError, api, type DocumentFilters } from "../api";
import { useToast } from "../components/Toasts";
import { bytes, duration, percent, when } from "../format";
import type { DocumentItem, IngestionJob } from "../types";

const TABS = [
  ["indexed", "Documents"],
  ["classified", "Classified"],
  ["jobs", "Ingestion jobs"],
  ["upload", "Upload"],
] as const;
type TabKey = (typeof TABS)[number][0];
/** The frontend proxy's limit (client_max_body_size) and the RAG API's */
export const UPLOAD_LIMIT = 25 * 1024 * 1024;
const JOB_COLORS = { queued: "grey", running: "blue", done: "green", failed: "red" } as const;

export function DocumentsPage() {
  const { tab = "indexed" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  if (!TABS.some(([key]) => key === tab)) return <Navigate to="/documents/indexed" replace />;
  const open = (docId: string) => {
    const next = new URLSearchParams(params);
    next.set("doc", docId);
    setParams(next);
  };
  const doc = params.get("doc");
  const close = () => {
    const next = new URLSearchParams(params);
    next.delete("doc");
    setParams(next);
  };

  return (
    <PageSection>
      <Drawer isExpanded={Boolean(doc)}>
        <DrawerContent panelContent={doc ? <DocumentPanel id={doc} onClose={close} /> : <DrawerPanelContent />}>
          <DrawerContentBody>
            <Title headingLevel="h1">Documents</Title>
            <Tabs activeKey={tab} onSelect={(_e, key) => navigate(`/documents/${key}`)} mountOnEnter unmountOnExit aria-label="Documents">
              {TABS.map(([key, label]) => (
                <Tab key={key} eventKey={key} title={<TabTitleText>{label}</TabTitleText>}>
                  <div style={{ paddingTop: "var(--pf-t--global--spacer--md)" }}>
                    <TabContent tab={key} onOpen={open} />
                  </div>
                </Tab>
              ))}
            </Tabs>
          </DrawerContentBody>
        </DrawerContent>
      </Drawer>
    </PageSection>
  );
}

function TabContent({ tab, onOpen }: { tab: TabKey; onOpen: (docId: string) => void }) {
  if (tab === "jobs") return <JobsTab onOpen={onOpen} />;
  if (tab === "upload") return <UploadTab />;
  return <DocumentsTab kind={tab} onOpen={onOpen} />;
}

function DocumentsTab({ kind, onOpen }: { kind: "indexed" | "classified"; onOpen: (docId: string) => void }) {
  const [params, setParams] = useSearchParams();
  const filters: DocumentFilters = {
    kind,
    q: params.get("q") ?? undefined,
    bucket: params.get("bucket") ?? undefined,
    page: Number(params.get("page") ?? 1) || 1,
    limit: Number(params.get("limit") ?? 50) || 50,
  };
  const list = useQuery({
    queryKey: ["documents", filters],
    queryFn: () => api.documents(filters),
    placeholderData: keepPreviousData,
  });
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const set = (name: "q" | "bucket" | "page" | "limit", value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    if (name !== "page") next.delete("page");
    setParams(next);
  };
  const toggle = (id: string) =>
    setExpanded((all) => {
      const next = new Set(all);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const items = list.data?.items ?? [];
  const classified = kind === "classified";

  return (
    <>
      <Toolbar>
        <ToolbarContent>
          <ToolbarItem>
            <NameSearch value={params.get("q") ?? ""} onApply={(v) => set("q", v)} />
          </ToolbarItem>
          {!classified && (
            <ToolbarItem>
              <FormSelect aria-label="Bucket" value={params.get("bucket") ?? ""} onChange={(_e, v) => set("bucket", v)}>
                <FormSelectOption value="" label="Bucket: any" />
                {["documents", "transcripts"].map((b) => (
                  <FormSelectOption key={b} value={b} label={b} />
                ))}
              </FormSelect>
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
      {list.isPending && <Spinner aria-label="Loading the documents" />}
      {list.isError && <Alert variant="danger" isInline title={list.error.message} />}
      {list.isSuccess && items.length === 0 && (
        <EmptyState
          titleText={classified ? "No classified files" : "No indexed documents"}
          headingLevel="h2"
        >
          {classified ? "Files dropped into the inbox bucket are classified and listed here." : "Upload documents to index them."}
        </EmptyState>
      )}
      {items.length > 0 && (
        <Table aria-label={classified ? "Classified files" : "Indexed documents"} variant="compact">
          <Thead>
            <Tr>
              {classified && <Th screenReaderText="Expand" />}
              <Th>Source</Th>
              {classified ? (
                <>
                  <Th>Type</Th>
                  <Th modifier="wrap">Confidence</Th>
                  <Th>Summary</Th>
                  <Th>Classified</Th>
                </>
              ) : (
                <>
                  <Th>Bucket</Th>
                  <Th modifier="wrap">Pages</Th>
                  <Th modifier="wrap">Chunks</Th>
                  <Th>Ingested</Th>
                  <Th>Updated</Th>
                </>
              )}
              <Th screenReaderText="Actions" />
            </Tr>
          </Thead>
          {items.map((d, rowIndex) => {
            const isExpanded = expanded.has(d.doc_id);
            return (
              <Tbody key={d.doc_id} isExpanded={classified && isExpanded}>
                <Tr>
                  {classified && (
                    <Td expand={{ rowIndex, isExpanded, onToggle: () => toggle(d.doc_id), expandId: "fields" }} />
                  )}
                  <Td dataLabel="Source">
                    <Button variant="link" isInline onClick={() => onOpen(d.doc_id)}>
                      {d.source}
                    </Button>
                  </Td>
                  {classified ? (
                    <>
                      <Td dataLabel="Type">{d.extracted?.doc_type ?? d.doc_type ?? "-"}</Td>
                      <Td dataLabel="Confidence">{percent(d.extracted?.confidence)}</Td>
                      <Td dataLabel="Summary">{d.extracted?.summary || "-"}</Td>
                      <Td dataLabel="Classified" className="admin-timeline__when">
                        {when(d.updated_at)}
                      </Td>
                    </>
                  ) : (
                    <>
                      <Td dataLabel="Bucket">{d.bucket}</Td>
                      <Td dataLabel="Pages">{d.pages ?? "-"}</Td>
                      <Td dataLabel="Chunks">{d.chunks ?? "-"}</Td>
                      <Td dataLabel="Ingested" className="admin-timeline__when">
                        {when(d.ingested_at)}
                      </Td>
                      <Td dataLabel="Updated" className="admin-timeline__when">
                        {when(d.updated_at)}
                      </Td>
                    </>
                  )}
                  <Td dataLabel="Actions" isActionCell>
                    <DocumentActions document={d} />
                  </Td>
                </Tr>
                {classified && (
                  <Tr isExpanded={isExpanded}>
                    <Td colSpan={7}>
                      <ExpandableRowContent>
                        <Fields fields={d.extracted?.fields} />
                      </ExpandableRowContent>
                    </Td>
                  </Tr>
                )}
              </Tbody>
            );
          })}
        </Table>
      )}
    </>
  );
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  return typeof value === "string" ? value : JSON.stringify(value);
}

function Fields({ fields }: { fields: Record<string, unknown> | undefined }) {
  const entries = Object.entries(fields ?? {});
  if (entries.length === 0) return <Content component="small">No fields extracted.</Content>;
  return (
    <Table aria-label="Extracted fields" variant="compact" borders={false}>
      <Thead>
        <Tr>
          <Th>Field</Th>
          <Th>Value</Th>
        </Tr>
      </Thead>
      <Tbody>
        {entries.map(([key, value]) => (
          <Tr key={key}>
            <Td dataLabel="Field">{key}</Td>
            <Td dataLabel="Value" className="admin-pre">
              {show(value)}
            </Td>
          </Tr>
        ))}
      </Tbody>
    </Table>
  );
}

/** Re-ingest (indexed documents only: an inbox file is classified, not indexed) and delete. */
function DocumentActions({ document: d, onDeleted }: { document: DocumentItem; onDeleted?: () => void }) {
  const client = useQueryClient();
  const toast = useToast();
  const [confirm, setConfirm] = useState(false);
  const reingest = useMutation({
    mutationFn: () => api.reingest(d.doc_id),
    onSuccess: (r) => {
      client.invalidateQueries({ queryKey: ["jobs"] });
      toast("success", `Re-ingesting ${d.source} (job ${r.job_id.slice(0, 8)}).`);
    },
    onError: (e: Error) => toast("danger", e.message),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteDocument(d.doc_id),
    onSuccess: () => {
      setConfirm(false);
      client.invalidateQueries({ queryKey: ["documents"] });
      client.invalidateQueries({ queryKey: ["jobs"] });
      client.removeQueries({ queryKey: ["document", d.doc_id] });
      toast("success", `${d.source} deleted.`);
      onDeleted?.();
    },
    onError: (e: Error) => {
      setConfirm(false);
      toast("danger", e.message);
    },
  });
  return (
    <Flex spaceItems={{ default: "spaceItemsSm" }} flexWrap={{ default: "nowrap" }} className="admin-nowrap">
      {d.bucket !== "inbox" && (
        <Button variant="secondary" size="sm" onClick={() => reingest.mutate()} isLoading={reingest.isPending} isDisabled={reingest.isPending}>
          Re-ingest
        </Button>
      )}
      <Button variant="danger" size="sm" onClick={() => setConfirm(true)}>
        Delete
      </Button>
      {confirm && (
        <Modal variant="small" isOpen onClose={() => setConfirm(false)} aria-labelledby="delete-document-title">
          <ModalHeader title={`Delete ${d.source}?`} labelId="delete-document-title" titleIconVariant="danger" />
          <ModalBody>
            <List>
              {(d.chunks ?? 0) > 0 && (
                <ListItem>Its {d.chunks} passages are removed from the vector store: the assistant stops answering from it.</ListItem>
              )}
              <ListItem>Its record{d.extracted ? " and extracted fields are" : " is"} removed from the database.</ListItem>
              <ListItem>
                The file <code>{d.source_uri}</code> is deleted from the {d.bucket} bucket.
              </ListItem>
              <ListItem>Conversations that cited it keep their citations.</ListItem>
            </List>
          </ModalBody>
          <ModalFooter>
            <Button variant="danger" onClick={() => remove.mutate()} isLoading={remove.isPending} isDisabled={remove.isPending}>
              Delete
            </Button>
            <Button variant="link" onClick={() => setConfirm(false)}>
              Keep it
            </Button>
          </ModalFooter>
        </Modal>
      )}
    </Flex>
  );
}

function JobStatus({ job }: { job: IngestionJob }) {
  return (
    <Label isCompact color={JOB_COLORS[job.status] ?? "grey"}>
      {job.status}
    </Label>
  );
}

function seconds(value: number | null): string {
  if (value === null) return "-";
  return value < 60 ? `${Math.max(1, Math.round(value))} s` : duration(value / 60);
}

function JobsTab({ onOpen }: { onOpen: (docId: string) => void }) {
  const jobs = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs,
    // Jobs report no events until they finish: poll while one runs
    refetchInterval: (q) => (q.state.data?.items.some((j) => j.status === "queued" || j.status === "running") ? 3000 : false),
  });
  const items = jobs.data?.items ?? [];
  return (
    <>
      {jobs.isPending && <Spinner aria-label="Loading the ingestion jobs" />}
      {jobs.isError && <Alert variant="danger" isInline title={jobs.error.message} />}
      {jobs.isSuccess && items.length === 0 && <EmptyState titleText="No ingestion jobs yet" headingLevel="h2" />}
      {items.length > 0 && (
        <Table aria-label="Ingestion jobs" variant="compact">
          <Thead>
            <Tr>
              <Th>Source</Th>
              <Th>Status</Th>
              <Th modifier="wrap">Pages</Th>
              <Th modifier="wrap">Chunks</Th>
              <Th>Took</Th>
              <Th>Started</Th>
              <Th>Error</Th>
            </Tr>
          </Thead>
          <Tbody>
            {items.map((j) => (
              <Tr key={j.job_id}>
                <Td dataLabel="Source">
                  <Button variant="link" isInline onClick={() => onOpen(j.doc_id)}>
                    {j.source_uri.replace(/^s3:\/\//, "")}
                  </Button>
                </Td>
                <Td dataLabel="Status">
                  <JobStatus job={j} />
                </Td>
                <Td dataLabel="Pages">{j.pages ?? "-"}</Td>
                <Td dataLabel="Chunks">{j.chunks ?? "-"}</Td>
                <Td dataLabel="Took">{seconds(j.seconds)}</Td>
                <Td dataLabel="Started" className="admin-timeline__when">
                  {when(j.created_at)}
                </Td>
                <Td dataLabel="Error" className="admin-pre">
                  {j.error ?? "-"}
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      )}
    </>
  );
}

interface Upload {
  id: number;
  name: string;
  size: number;
  bucket: "documents" | "inbox";
  state: "uploading" | "done" | "failed";
  message?: string;
}

const NEXT = {
  documents: "uploaded: WF2 indexes it now (Ingestion jobs)",
  inbox: "uploaded: WF3 classifies it now (Classified)",
};

function UploadTab() {
  const client = useQueryClient();
  const [bucket, setBucket] = useState<"documents" | "inbox">("documents");
  const [uploads, setUploads] = useState<Upload[]>([]);
  const update = (id: number, change: Partial<Upload>) =>
    setUploads((all) => all.map((u) => (u.id === id ? { ...u, ...change } : u)));

  const drop = async (files: File[]) => {
    const added = files.map((f, i) => ({ file: f, id: Date.now() + i }));
    setUploads((all) => [
      ...added.map(({ file, id }): Upload => ({ id, name: file.name, size: file.size, bucket, state: "uploading" })),
      ...all,
    ]);
    // One at a time: the ingestion service and the workflows take them in order
    for (const { file, id } of added) {
      if (file.size > UPLOAD_LIMIT) {
        update(id, { state: "failed", message: "over 25 MiB, the upload limit" });
        continue;
      }
      try {
        const result = await api.upload(bucket, file);
        update(id, { state: "done", message: result.key === file.name ? NEXT[bucket] : `${NEXT[bucket]}, as ${result.key}` });
      } catch (e) {
        update(id, { state: "failed", message: e instanceof Error ? e.message : String(e) });
      }
    }
    client.invalidateQueries({ queryKey: ["documents"] });
    client.invalidateQueries({ queryKey: ["jobs"] });
  };

  return (
    <>
      <FormGroup role="radiogroup" fieldId="upload-bucket" label="Upload to" isStack>
        <Radio
          id="upload-documents"
          name="bucket"
          label="documents"
          description="Indexed: the assistant answers from it."
          isChecked={bucket === "documents"}
          onChange={() => setBucket("documents")}
        />
        <Radio
          id="upload-inbox"
          name="bucket"
          label="inbox"
          description="Classified: its type, a summary and the fields it contains (invoices, contracts, forms)."
          isChecked={bucket === "inbox"}
          onChange={() => setBucket("inbox")}
        />
      </FormGroup>
      <MultipleFileUpload onFileDrop={(_e, files) => drop(files)} style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
        <MultipleFileUploadMain
          titleIcon={<UploadIcon />}
          titleText="Drag and drop files here"
          titleTextSeparator="or"
          infoText="PDF, Word, PowerPoint, Excel, HTML, Markdown or text; up to 25 MiB each. Same path as the object store's own uploads."
        />
      </MultipleFileUpload>
      {uploads.length > 0 && (
        <List isPlain isBordered aria-label="Uploads" style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
          {uploads.map((u) => (
            <ListItem key={u.id}>
              <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
                {u.state === "uploading" ? (
                  <Spinner size="sm" aria-label={`Uploading ${u.name}`} />
                ) : (
                  <Label isCompact color={u.state === "done" ? "green" : "red"}>
                    {u.state === "done" ? "uploaded" : "failed"}
                  </Label>
                )}
                <strong>{u.name}</strong>
                <span className="admin-timeline__when">
                  {bytes(u.size)} to {u.bucket}
                </span>
                {u.message && <span>{u.state === "done" ? u.message.replace(/^uploaded: /, "") : u.message}</span>}
              </Flex>
            </ListItem>
          ))}
        </List>
      )}
    </>
  );
}

/** A document's details beside the lists, from ?doc=<doc_id> (the Activity page links here). */
function DocumentPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const detail = useQuery({ queryKey: ["document", id], queryFn: () => api.document(id), retry: false });
  const gone = detail.error instanceof ApiError && detail.error.status === 404;
  const d = detail.data;
  return (
    <DrawerPanelContent widths={{ default: "width_100", lg: "width_50" }} aria-label="Document">
      <DrawerHead>
        <Title headingLevel="h2" size="lg">
          {d?.source ?? "Document"}
        </Title>
        {d && <Content component="small">{d.source_uri}</Content>}
        <DrawerActions>
          <DrawerCloseButton onClick={onClose} />
        </DrawerActions>
      </DrawerHead>
      <DrawerPanelBody>
        {detail.isPending && <Spinner aria-label="Loading the document" />}
        {gone && <Alert variant="info" isInline title="This document is not in the database (deleted, or never ingested)." />}
        {detail.isError && !gone && <Alert variant="danger" isInline title={detail.error.message} />}
        {d && (
          <>
            <DocumentActions document={d} onDeleted={onClose} />
            <DescriptionList isHorizontal isCompact style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
              <Term label="Bucket" value={d.bucket} />
              <Term label="Type" value={d.extracted?.doc_type ?? d.doc_type} />
              {d.extracted && <Term label="Confidence" value={percent(d.extracted.confidence)} />}
              {d.extracted?.summary && <Term label="Summary" value={d.extracted.summary} />}
              <Term label="Pages" value={d.pages} />
              <Term label="Chunks" value={d.chunks} />
              <Term label="Ingested" value={when(d.ingested_at)} />
              <Term label="Updated" value={when(d.updated_at)} />
              <Term label="Document id" value={<code>{d.doc_id}</code>} />
            </DescriptionList>
            {d.extracted && (
              <>
                <Title headingLevel="h3" size="md" style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
                  Extracted fields
                </Title>
                <Fields fields={d.extracted.fields} />
              </>
            )}
            <Title headingLevel="h3" size="md" style={{ marginTop: "var(--pf-t--global--spacer--md)" }}>
              Ingestion jobs
            </Title>
            {d.jobs.length === 0 ? (
              <Content component="small">None recorded.</Content>
            ) : (
              <List isPlain>
                {d.jobs.map((j) => (
                  <ListItem key={j.job_id}>
                    <Flex spaceItems={{ default: "spaceItemsSm" }} alignItems={{ default: "alignItemsCenter" }}>
                      <JobStatus job={j} />
                      <span className="admin-timeline__when">{when(j.created_at)}</span>
                      {j.chunks !== null && <span>{j.chunks} chunks</span>}
                      <span>{seconds(j.seconds)}</span>
                    </Flex>
                    {j.error && <div className="admin-timeline__note admin-pre">{j.error}</div>}
                  </ListItem>
                ))}
              </List>
            )}
          </>
        )}
      </DrawerPanelBody>
    </DrawerPanelContent>
  );
}

function Term({ label, value }: { label: string; value: ReactNode }) {
  return (
    <DescriptionListGroup>
      <DescriptionListTerm>{label}</DescriptionListTerm>
      <DescriptionListDescription>{value === null || value === undefined || value === "" ? "-" : value}</DescriptionListDescription>
    </DescriptionListGroup>
  );
}

/** Part of the file name, applied on Enter. */
function NameSearch({ value, onApply }: { value: string; onApply: (v: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <SearchInput
      aria-label="File name"
      placeholder="File name"
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
