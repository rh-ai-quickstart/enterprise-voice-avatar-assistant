import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { TicketActions } from "./TicketActions";

const ticket = { ticket_ref: "REQ-000001", title: "Laptop for the new hire", priority: "normal", category: "hardware" };
const result = (workflow_notified: boolean | null = true) => json({ ticket: { ...ticket, actions: [] }, workflow_notified });

describe("TicketActions", () => {
  it("shows only the actions the ticket allows", () => {
    renderWithProviders(<TicketActions ticket={ticket} actions={["approve", "reject"]} />);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark fulfilled" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel request" })).not.toBeInTheDocument();
  });

  it("a rejection needs a reason, which is sent with it", async () => {
    const fetchMock = mockFetch(() => result());
    renderWithProviders(<TicketActions ticket={ticket} actions={["approve", "reject"]} />);
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    const dialog = await screen.findByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Reject" });
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/reason/i), "   ");
    expect(confirm).toBeDisabled();
    await userEvent.type(within(dialog).getByLabelText(/reason/i), "use the standard model");
    expect(confirm).toBeEnabled();
    await userEvent.click(confirm);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/admin/tickets/REQ-000001/decision");
    expect(init.headers).toMatchObject({ "X-Admin-Request": "1" });
    expect(JSON.parse(init.body as string)).toEqual({ decision: "rejected", note: "use the standard model" });
    expect(await screen.findByText("REQ-000001 rejected; the requester hears your reason.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("approval says when the workflow could not be reached", async () => {
    const fetchMock = mockFetch(() => result(false));
    renderWithProviders(<TicketActions ticket={ticket} actions={["approve", "reject"]} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({ decision: "approved", note: null });
    expect(await screen.findByText(/could not be reached: mark it fulfilled/)).toBeInTheDocument();
  });

  it("a refused action is reported", async () => {
    mockFetch(() => json({ detail: "cannot move a ticket from approved to rejected" }, 409));
    renderWithProviders(<TicketActions ticket={ticket} actions={["approve"]} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(await screen.findByText(/cannot move a ticket from approved to rejected/)).toBeInTheDocument();
  });
});
