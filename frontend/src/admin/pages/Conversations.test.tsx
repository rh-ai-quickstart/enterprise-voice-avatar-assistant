import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { Highlighted } from "../components/Highlighted";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { ConversationPage } from "./Conversation";
import { ConversationsPage } from "./Conversations";

const summary = {
  session_id: "s-alex",
  user_id: "alex",
  channel: "chat",
  started: "2026-09-25T10:00:00Z",
  last_activity: "2026-09-25T10:05:00Z",
  messages: 4,
  blocked: 0,
  tickets: 1,
  archives: 0,
  matches: [{ id: 1, role: "user", snippet: "How often do admin \u0001passwords\u0002 \u0001rotate\u0002?" }],
};

describe("Highlighted", () => {
  it("marks the matched words and keeps markup as text", () => {
    const { container } = render(<Highlighted text={"<b>x</b> \u0001rotate\u0002 and \u0001passwords\u0002."} />);
    expect([...container.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["rotate", "passwords"]);
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toBe("<b>x</b> rotate and passwords.");
  });
});

describe("Conversations", () => {
  it("searches every message and shows the matching lines", async () => {
    const fetchMock = mockFetch(() => json({ items: [summary], total: 1, page: 1, limit: 50 }));
    renderWithProviders(
      <Routes>
        <Route path="/conversations" element={<ConversationsPage />} />
      </Routes>,
      "/conversations?q=rotating%20passwords&channel=chat",
    );
    const row = await screen.findByRole("row", { name: /alex/ });
    expect(within(row).getByText("chat")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/conversations?q=rotating+passwords&channel=chat&page=1&limit=50");
    const marks = [...document.querySelectorAll(".admin-matches mark")].map((m) => m.textContent);
    expect(marks).toEqual(["passwords", "rotate"]);
  });

  it("a filter goes into the query string", async () => {
    const fetchMock = mockFetch(() => json({ items: [summary], total: 1, page: 1, limit: 50 }));
    renderWithProviders(
      <Routes>
        <Route path="/conversations" element={<ConversationsPage />} />
      </Routes>,
      "/conversations",
    );
    await screen.findByRole("row", { name: /alex/ });
    await userEvent.selectOptions(screen.getByLabelText("Archived"), "true");
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/conversations?archived=true"));
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("/api/v1/admin/conversations?archived=true&page=1&limit=50"));
  });
});

const detail = {
  session_id: "s-priya",
  user_id: "priya",
  channel: "voice",
  started: "2026-09-25T10:00:00Z",
  updated_at: "2026-09-25T10:05:00Z",
  messages: [
    { id: 1, role: "user", content: "Can I get a laptop?", citations: [], blocked: false, created_at: "2026-09-25T10:00:00Z" },
    {
      id: 2,
      role: "assistant",
      content: "I've logged your request REQ-000001.",
      citations: [{ n: 1, used: true, doc_id: "d", source: "it-equipment-procedure.docx", page: 2, snippet: "Laptops", score: 0.7 }],
      blocked: false,
      created_at: "2026-09-25T10:00:05Z",
    },
  ],
  notices: [],
  tickets: [{ ticket_ref: "REQ-000001", title: "Laptop", status: "pending_approval", priority: "normal", category: "hardware", created_at: "2026-09-25T10:00:05Z" }],
  archives: [
    {
      id: 3,
      session_id: "s-priya",
      title: "Assistant transcript s-priya",
      doc_url: "https://docs.google.com/document/d/x",
      object_key: "transcript-s-priya.md",
      status: "indexed",
      error: null,
      requested_by: "Dana",
      created_at: "2026-09-25T10:06:00Z",
      updated_at: "2026-09-25T10:06:30Z",
    },
  ],
};

describe("Conversation", () => {
  it("delete names what goes and what stays, then deletes", async () => {
    const fetchMock = mockFetch((url, init) =>
      init.method === "DELETE"
        ? json({ session_id: "s-priya", messages: 2, notices: 0, archives: 1, tickets_kept: ["REQ-000001"], google_docs_kept: [] })
        : json(detail),
    );
    renderWithProviders(
      <Routes>
        <Route path="/conversations/:id" element={<ConversationPage />} />
        <Route path="/conversations" element={<p>list</p>} />
      </Routes>,
      "/conversations/s-priya",
    );
    expect(await screen.findByText("I've logged your request REQ-000001.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export Markdown" })).toHaveAttribute("href", "/api/v1/admin/conversations/s-priya/export?format=md");
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute("href", "/api/v1/admin/conversations/s-priya/archives/3/download");
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Tickets filed from it are kept \(REQ-000001\)/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Google Docs are not deleted/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: /Assistant transcript s-priya/ })).toHaveAttribute("href", "https://docs.google.com/document/d/x");
    await userEvent.click(within(dialog).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/conversations$/));
    const call = fetchMock.mock.calls.find(([, init]) => init.method === "DELETE")!;
    expect(call[0]).toBe("/api/v1/admin/conversations/s-priya");
    expect(call[1].headers).toMatchObject({ "X-Admin-Request": "1" });
  });
});
