import { expect, test, type Browser } from "@playwright/test";
import { chatSession, fileRequest, n8nCall, openChat, resetN8n, send, signIn } from "./helpers";

// The chat and the portal in separate browser contexts, as a requester and an admin would be
async function portal(browser: Browser, name: string) {
  const page = await (await browser.newContext()).newPage();
  await signIn(page, name);
  return page;
}

test("sign-in refuses a wrong password, then signs in and out", async ({ page }) => {
  await page.goto("/admin/");
  await expect(page).toHaveURL(/\/admin\/login$/);
  await page.getByLabel("Your name").fill("Dana");
  await page.getByLabel("Admin password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Wrong password.")).toBeVisible();

  await signIn(page, "Dana");
  await expect(page.getByText("Dana", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  await page.goto("/admin/tickets");
  await expect(page).toHaveURL(/\/admin\/login$/);
});

test("a request filed in the chat is approved in the portal, and the chat says who approved it", async ({ page, browser, request }) => {
  await resetN8n(request);
  await openChat(page, "Priya Shah");
  const ref = await fileRequest(page, "I need Visual Studio Code installed on my laptop, please.");
  await expect(page.locator("article.message.assistant").last()).toContainText("needs approval");
  const intake = await n8nCall(request, (c) => c.path === "/webhook/request-intake");
  expect(intake.token).toBe(true);
  expect(intake.body.ticket.ticket_ref).toBe(ref);

  const admin = await portal(browser, "Dana");
  await admin.getByRole("link", { name: /^Approvals/ }).click();
  await admin.getByRole("row", { name: new RegExp(ref) }).getByRole("button", { name: "Approve" }).click();
  // The fake n8n fulfils it, as WF4 does after a portal decision
  const decided = await n8nCall(request, (c) => c.path === "/webhook/ticket-decided");
  expect(decided.body).toMatchObject({ via: "portal", decision: "approved", actor: "Dana" });
  await admin.goto(`/admin/tickets/${ref}`);
  await expect(admin.getByRole("row", { name: new RegExp(ref) })).toContainText("fulfilled");

  await expect(page.locator("article.message.assistant").last()).toContainText(
    `Good news: your request ${ref}, Install Visual Studio Code, was approved by Dana and has been fulfilled.`,
  );
});

test("with Slack on, the approval card shows the decision taken in the portal", async ({ page, browser, request }) => {
  await resetN8n(request, true);
  await openChat(page, "Sam Lee");
  const ref = await fileRequest(page, "Please give me access to the finance share.");

  const admin = await portal(browser, "Dana");
  await admin.goto(`/admin/approvals/${ref}`);
  // WF4 posted the card and kept its reference on the ticket
  await expect(admin.getByRole("link", { name: "Slack card" })).toHaveAttribute("href", /example-corp\.slack\.com/);
  // With the request open, its panel has the decision buttons
  await admin.getByRole("button", { name: "Approve" }).click();

  const update = await n8nCall(request, (c) => c.path === "slack:chat.update");
  expect(update.body).toEqual({ ticket_ref: ref, text: "approved by Dana in the admin portal" });
  await expect(page.locator("article.message.assistant").last()).toContainText(`was approved by Dana and has been fulfilled`);
});

test("a conversation is archived with Google Docs off, and its transcript downloads", async ({ page, browser, request }) => {
  await resetN8n(request);
  await openChat(page, "Kim Park");
  const reply = await send(page, "How often must passwords be rotated?");
  await expect(reply).toContainText("I could not find that in the company documents");
  const session = await chatSession(page);

  const admin = await portal(browser, "Dana");
  await admin.goto(`/admin/conversations/${session}`);
  await expect(admin.getByText("How often must passwords be rotated?")).toBeVisible();
  await admin.getByRole("button", { name: "Archive" }).click();
  await expect(admin.getByText("Archived; re-ingestion runs now.")).toBeVisible();
  const handed = await n8nCall(request, (c) => c.path === "/webhook/archive-transcript");
  expect(handed.token).toBe(true);
  expect(handed.body.session_id).toBe(session);
  expect(handed.body.doc_url || null).toBeNull();

  const [download] = await Promise.all([admin.waitForEvent("download"), admin.getByRole("link", { name: "Download" }).click()]);
  const text = await (await download.createReadStream()).toArray().then((chunks) => Buffer.concat(chunks).toString());
  expect(text).toContain("How often must passwords be rotated?");
  expect(text).toContain("I could not find that in the company documents");

  // The question found no answer: it waits on the Knowledge gaps page
  await admin.goto("/admin/gaps");
  await expect(admin.getByRole("row", { name: /How often must passwords be rotated\?/ })).toBeVisible();
});

test("the public proxy refuses the internal API", async ({ request }) => {
  expect((await request.patch("/api/v1/tickets/REQ-000001", { data: { status: "approved", actor: "mallory" } })).status()).toBe(404);
  expect((await request.get("/api/v1/tickets")).status()).toBe(404);
  expect((await request.get("/api/v1/sessions/x/transcript")).status()).toBe(404);
  // The portal's API is there, behind its sign-in
  expect((await request.get("/api/v1/admin/overview")).status()).toBe(401);
});
