import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { ActivityPage } from "./Activity";
import { Changes } from "./Audit";

const event = (id: number, extra: Record<string, unknown> = {}) => ({
  id,
  kind: "ticket.approved",
  severity: "success",
  title: `REQ-00000${id} approved by Dana: Laptop`,
  detail: null,
  ref_type: "ticket",
  ref_id: `REQ-00000${id}`,
  actor: "Dana",
  source: "portal",
  data: {},
  created_at: "2026-09-26T10:00:00Z",
  ...extra,
});

function renderActivity(path: string, respond: (url: string) => Response) {
  const fetchMock = mockFetch(respond);
  renderWithProviders(
    <Routes>
      <Route path="/activity" element={<ActivityPage />} />
    </Routes>,
    path,
  );
  return fetchMock;
}

describe("Activity", () => {
  it("links each event to what it is about", async () => {
    renderActivity("/activity", () =>
      json({
        items: [
          event(2),
          event(1, {
            kind: "document.ingest_failed",
            severity: "error",
            title: "handbook.pdf could not be indexed",
            detail: "ConversionError: empty file",
            ref_type: "document",
            ref_id: "doc 1",
          }),
          event(3, { kind: "integration.error", title: "Slack: Post digest failed", ref_type: "integration", ref_id: "slack" }),
        ],
        next_before_id: null,
      }),
    );
    expect(await screen.findByRole("link", { name: "REQ-000002 approved by Dana: Laptop" })).toHaveAttribute("href", "/tickets/REQ-000002");
    expect(screen.getByRole("link", { name: "handbook.pdf could not be indexed" })).toHaveAttribute("href", "/documents/indexed?doc=doc%201");
    expect(screen.getByRole("link", { name: "Slack: Post digest failed" })).toHaveAttribute("href", "/integrations");
    expect(screen.getByText("ConversionError: empty file")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show older" })).toBeNull();
  });

  it("filters go into the query string and whole days into the API call", async () => {
    const fetchMock = renderActivity("/activity?to=2026-09-26", () => json({ items: [event(1)], next_before_id: null }));
    await screen.findByText(/REQ-000001/);
    const until = new Date("2026-09-27T00:00:00").toISOString();
    expect(fetchMock.mock.calls[0][0]).toBe(`/api/v1/admin/activity?to=${encodeURIComponent(until)}&limit=50`);
    await userEvent.selectOptions(screen.getByLabelText("Kind"), "ticket.sla_reminder");
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent("/activity?to=2026-09-26&kind=ticket.sla_reminder"));
    await waitFor(() => expect(fetchMock.mock.calls.at(-1)?.[0]).toMatch(/^\/api\/v1\/admin\/activity\?kind=ticket.sla_reminder&to=/));
  });

  it("shows older events after the last one shown", async () => {
    const fetchMock = renderActivity("/activity", (url) =>
      url.includes("before_id=2") ? json({ items: [event(1)], next_before_id: null }) : json({ items: [event(3), event(2)], next_before_id: 2 }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Show older" }));
    expect(await screen.findByText(/REQ-000001/)).toBeInTheDocument();
    expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("/api/v1/admin/activity?before_id=2&limit=50");
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
});

describe("Audit changes", () => {
  it("shows only what changed, before and after", () => {
    const { container } = render(
      <Changes
        before={{ status: "pending_approval", priority: "normal", approver: null }}
        after={{ status: "approved", priority: "normal", approver: "Dana", note: "ok" }}
      />,
    );
    const lines = [...container.querySelectorAll(".admin-diff > div")].map((d) => d.textContent);
    expect(lines).toEqual(["status: pending_approval → approved", "approver: - → Dana", "note: ok"]);
    expect(container.querySelector("del")?.textContent).toBe("pending_approval");
  });
});
