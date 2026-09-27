import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { IntegrationsPage } from "./Integrations";

const slack = {
  name: "slack",
  label: "Slack",
  state: "misconfigured",
  enabled: true,
  config: { channels: ["assistant-approvals", "assistant-tickets"] },
  missing: ["SLACK_SIGNING_SECRET"],
  last_test: null,
  last_error: {
    kind: "integration.error",
    severity: "error",
    title: "Slack: Post digest failed",
    detail: "channel_not_found",
    data: {},
    created_at: "2026-09-26T09:00:00Z",
  },
};

describe("Integrations", () => {
  it("shows the state, what is missing and the last error, and runs a test", async () => {
    const tested = {
      kind: "integration.test",
      severity: "error",
      title: "Slack test failed",
      detail: null,
      data: {
        checks: [
          { name: "signing secret", ok: false, detail: "SLACK_SIGNING_SECRET is empty: clicks on cards are refused" },
          { name: "auth.test", ok: true, detail: "app assistant in workspace Example Corp" },
        ],
      },
      created_at: "2026-09-26T10:00:00Z",
    };
    let after = false;
    const fetchMock = mockFetch((url, init) => {
      if (init.method === "POST") {
        after = true;
        return json({ name: "slack", ok: false, checks: tested.data.checks, status: { ...slack, last_test: tested } });
      }
      return json({ items: [after ? { ...slack, last_test: tested } : slack] });
    });
    renderWithProviders(<IntegrationsPage />);
    const card = await screen.findByLabelText("Slack");
    expect(within(card).getByText("misconfigured")).toBeInTheDocument();
    expect(within(card).getByText("Missing: SLACK_SIGNING_SECRET")).toBeInTheDocument();
    expect(within(card).getByText("assistant-approvals, assistant-tickets")).toBeInTheDocument();
    expect(within(card).getByText("channel_not_found")).toBeInTheDocument();
    expect(within(card).getByText("Never tested.")).toBeInTheDocument();
    await userEvent.click(within(card).getByRole("button", { name: "Test" }));
    expect(await screen.findByText("Slack test failed.")).toBeInTheDocument();
    expect(await within(card).findByText(/clicks on cards are refused/)).toBeInTheDocument();
    const post = fetchMock.mock.calls.find(([, init]) => init.method === "POST")!;
    expect(post[0]).toBe("/api/v1/admin/integrations/slack/test");
  });
});
