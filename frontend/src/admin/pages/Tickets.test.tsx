import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { TicketsPage, apiFilters } from "./Tickets";

const ticket = {
  id: 7,
  ticket_ref: "REQ-000007",
  title: "Install Visual Studio Code",
  description: null,
  category: "software",
  priority: "normal",
  status: "approved",
  requester: "Alex",
  session_id: "s1",
  payload: {},
  approver: "Dana",
  decision_note: null,
  created_at: "2026-09-25T10:00:00Z",
  updated_at: "2026-09-25T10:05:00Z",
  events: [],
  channel: "voice",
  pending_since: null,
  pending_minutes: null,
};

function renderTickets(path: string) {
  const fetchMock = mockFetch(() => json({ items: [ticket], total: 1, page: 1, limit: 50 }));
  renderWithProviders(
    <Routes>
      <Route path="/tickets" element={<TicketsPage />} />
    </Routes>,
    path,
  );
  return fetchMock;
}

describe("Tickets", () => {
  it("fetches with the filters in the query string", async () => {
    const fetchMock = renderTickets("/tickets?status=approved&q=code");
    const row = await screen.findByRole("row", { name: /REQ-000007/ });
    expect(within(row).getByText("Install Visual Studio Code")).toBeInTheDocument();
    expect(within(row).getByText("voice")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/admin/tickets?q=code&status=approved&page=1&limit=50");
  });

  it("a new filter goes into the query string, back to the first page", async () => {
    const fetchMock = renderTickets("/tickets?status=approved&page=3");
    await screen.findByRole("row", { name: /REQ-000007/ });
    await userEvent.selectOptions(screen.getByLabelText("Priority"), "high");
    await waitFor(() =>
      expect(screen.getByTestId("location")).toHaveTextContent("/tickets?status=approved&priority=high"),
    );
    await waitFor(() =>
      expect(fetchMock.mock.calls.at(-1)?.[0]).toBe("/api/v1/admin/tickets?status=approved&priority=high&page=1&limit=50"),
    );
  });

  it("clearing the filters empties the query string", async () => {
    renderTickets("/tickets?status=approved&channel=voice");
    await screen.findByRole("row", { name: /REQ-000007/ });
    await userEvent.click(screen.getAllByRole("button", { name: /clear/i })[0]);
    await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(/^\/tickets$/));
  });

  it("dates are whole days: 'to' includes that day", () => {
    const filters = apiFilters(new URLSearchParams("from=2026-09-01&to=2026-09-30"));
    expect(new Date(filters.from!).getDate()).toBe(1);
    const until = new Date(filters.to!);
    expect([until.getMonth(), until.getDate()]).toEqual([9, 1]);
  });
});
