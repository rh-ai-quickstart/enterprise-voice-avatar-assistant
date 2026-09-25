import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { json, mockFetch, renderWithProviders } from "../test-utils";
import { Login } from "./Login";

describe("Login", () => {
  it("signs in with the display name and the shared password", async () => {
    const fetchMock = mockFetch(() => json({ name: "Dana", expires_at: "2026-09-25T20:00:00Z" }));
    const onSignedIn = vi.fn();
    renderWithProviders(<Login onSignedIn={onSignedIn} />);
    const button = screen.getByRole("button", { name: "Sign in" });
    expect(button).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/your name/i), "Dana");
    await userEvent.type(screen.getByLabelText(/admin password/i), "s3cret");
    await userEvent.click(button);

    await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith({ name: "Dana", expires_at: "2026-09-25T20:00:00Z" }));
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/admin/login");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({ "X-Admin-Request": "1", "Content-Type": "application/json" });
    expect(JSON.parse(init.body as string)).toEqual({ name: "Dana", password: "s3cret" });
  });

  it("says why sign-in failed", async () => {
    mockFetch(() => json({ detail: "wrong password" }, 401));
    const onSignedIn = vi.fn();
    renderWithProviders(<Login onSignedIn={onSignedIn} />);
    await userEvent.type(screen.getByLabelText(/your name/i), "Dana");
    await userEvent.type(screen.getByLabelText(/admin password/i), "guess");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Wrong password.")).toBeInTheDocument();
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it("passes on the server's reason, for example the rate limit", async () => {
    mockFetch(() => json({ detail: "too many failed sign-ins; try again in 42 seconds" }, 429));
    renderWithProviders(<Login onSignedIn={vi.fn()} />);
    await userEvent.type(screen.getByLabelText(/your name/i), "Dana");
    await userEvent.type(screen.getByLabelText(/admin password/i), "guess");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText(/try again in 42 seconds/)).toBeInTheDocument();
  });
});
