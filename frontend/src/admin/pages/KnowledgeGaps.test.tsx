import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { KnowledgeGapsPage } from "./KnowledgeGaps";

const gap = (id: number, question: string, status = "open") => ({
  id,
  session_id: `session-${id}`,
  question,
  top_score: 0.31,
  hit_count: 2,
  reason: "low_score",
  status,
  resolved_by: null,
  resolved_at: null,
  resolution_note: null,
  created_at: "2026-09-26T09:00:00Z",
});

const groups = {
  groups: [
    {
      question: "How do I reset my VPN token?",
      count: 3,
      wordings: 2,
      best_score: 0.41,
      reasons: ["low_score", "no_hits"],
      first_seen: "2026-09-24T09:00:00Z",
      last_seen: "2026-09-26T09:00:00Z",
      ids: [12, 11, 10],
      gaps: [gap(12, "How do I reset my VPN token?"), gap(11, "How do I reset my VPN token?"), gap(10, "vpn token reset")],
    },
  ],
  total: 3,
  threshold: 0.85,
  from: "2026-09-19T10:00:00Z",
};

function renderGaps(respond: (url: string, init: RequestInit) => Response) {
  const fetchMock = mockFetch(respond);
  renderWithProviders(
    <Routes>
      <Route path="/gaps" element={<KnowledgeGapsPage />} />
    </Routes>,
    "/gaps",
  );
  return fetchMock;
}

describe("Knowledge gaps", () => {
  it("resolves a whole group with a note", async () => {
    const fetchMock = renderGaps((url, init) => (init.method === "POST" ? json({ changed: 3 }) : json(groups)));
    const row = await screen.findByRole("row", { name: /How do I reset my VPN token\?/ });
    expect(within(row).getByText(/in 2 wordings/)).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toMatch(/^\/api\/v1\/admin\/knowledge-gaps\?status=open&from=.+&group=true$/);
    await userEvent.click(within(row).getByRole("button", { name: "Resolve" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByRole("textbox"), "Added vpn-guide.pdf");
    await userEvent.click(within(dialog).getByRole("button", { name: "Resolve" }));
    expect(await screen.findByText("3 questions resolved.")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([, init]) => init.method === "POST")!;
    expect(call[0]).toBe("/api/v1/admin/knowledge-gaps/resolve");
    expect(JSON.parse(call[1].body as string)).toEqual({ ids: [12, 11, 10], status: "resolved", note: "Added vpn-guide.pdf" });
  });

  it("re-tests one question and shows whether the documents answer it now", async () => {
    const fetchMock = renderGaps((url) =>
      url.endsWith("/retest")
        ? json({
            id: 10,
            question: "vpn token reset",
            recorded_score: 0.31,
            best_score: 0.72,
            threshold: 0.5,
            answered: true,
            hits: [{ n: 1, used: false, doc_id: "d", source: "vpn-guide.pdf", page: 3, snippet: "Reset the token from the portal", score: 0.72 }],
          })
        : json(groups),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Details" }));
    const nested = await screen.findByLabelText("Questions in this group");
    await userEvent.click(within(within(nested).getByRole("row", { name: /vpn token reset/ })).getByRole("button", { name: "Re-test" }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Answered now")).toBeInTheDocument();
    expect(within(dialog).getByText("vpn-guide.pdf")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock.mock.calls.some(([u]) => u === "/api/v1/admin/knowledge-gaps/10/retest")).toBe(true));
  });
});
