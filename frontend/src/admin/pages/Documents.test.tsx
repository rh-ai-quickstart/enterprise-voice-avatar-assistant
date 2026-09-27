import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { DocumentsPage, UPLOAD_LIMIT } from "./Documents";

const handbook = {
  doc_id: "doc-1",
  source: "employee-handbook.pdf",
  source_uri: "s3://documents/employee-handbook.pdf",
  bucket: "documents",
  doc_type: null,
  pages: 12,
  chunks: 48,
  metadata: {},
  extracted: null,
  ingested_at: "2026-09-25T10:00:00Z",
  updated_at: "2026-09-25T10:00:00Z",
};

function renderDocuments(path: string, respond: (url: string, init: RequestInit) => Response) {
  const fetchMock = mockFetch(respond);
  renderWithProviders(
    <Routes>
      <Route path="/documents/:tab" element={<DocumentsPage />} />
    </Routes>,
    path,
  );
  return fetchMock;
}

describe("Documents", () => {
  it("delete says what goes, then deletes through the RAG API", async () => {
    const fetchMock = renderDocuments("/documents/indexed", (url, init) =>
      init.method === "DELETE"
        ? json({ doc_id: "doc-1", source: "employee-handbook.pdf", object: "s3://documents/employee-handbook.pdf" })
        : json({ items: [handbook], total: 1, page: 1, limit: 50 }),
    );
    const row = await screen.findByRole("row", { name: /employee-handbook.pdf/ });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/documents?kind=indexed&page=1&limit=50");
    await userEvent.click(within(row).getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Its 48 passages are removed from the vector store/)).toBeInTheDocument();
    expect(within(dialog).getByText("s3://documents/employee-handbook.pdf")).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    expect(await screen.findByText("employee-handbook.pdf deleted.")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([, init]) => init.method === "DELETE")!;
    expect(call[0]).toBe("/api/v1/admin/documents/doc-1");
    expect(call[1].headers).toMatchObject({ "X-Admin-Request": "1" });
  });

  it("uploads to the chosen bucket, and refuses a file over the limit without sending it", async () => {
    const fetchMock = renderDocuments("/documents/upload", () =>
      json({ bucket: "inbox", key: "invoice-0042.pdf", doc_id: "doc-9", size: 5 }, 201),
    );
    await userEvent.click(await screen.findByLabelText("inbox"));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const small = new File(["%PDF-"], "invoice-0042.pdf", { type: "application/pdf" });
    const big = new File(["x"], "scan.pdf", { type: "application/pdf" });
    Object.defineProperty(big, "size", { value: UPLOAD_LIMIT + 1 });
    await userEvent.upload(input, [small, big]);
    const uploads = await screen.findByLabelText("Uploads");
    await waitFor(() => expect(within(uploads).getByText("WF3 classifies it now (Classified)")).toBeInTheDocument());
    expect(within(uploads).getByText("over 25 MiB, the upload limit")).toBeInTheDocument();
    const posts = fetchMock.mock.calls.filter(([, init]) => init.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe("/api/v1/admin/documents/upload");
    const form = posts[0][1].body as FormData;
    expect(form.get("bucket")).toBe("inbox");
    expect((form.get("file") as File).name).toBe("invoice-0042.pdf");
    expect(posts[0][1].headers).toMatchObject({ "X-Admin-Request": "1" });
  });

  it("opens a document's details from the query string", async () => {
    renderDocuments("/documents/jobs?doc=doc-1", (url) =>
      url.includes("/documents/doc-1")
        ? json({
            ...handbook,
            jobs: [
              {
                job_id: "job-1",
                doc_id: "doc-1",
                source_uri: handbook.source_uri,
                status: "failed",
                error: "ConversionError: empty file",
                chunks: null,
                pages: null,
                created_at: "2026-09-25T09:00:00Z",
                finished_at: "2026-09-25T09:00:04Z",
                seconds: 4,
              },
            ],
          })
        : json({ items: [] }),
    );
    const panel = await screen.findByLabelText("Document");
    expect(await within(panel).findByRole("heading", { name: "employee-handbook.pdf" })).toBeInTheDocument();
    expect(within(panel).getByText("ConversionError: empty file")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: "Re-ingest" })).toBeInTheDocument();
  });
});
