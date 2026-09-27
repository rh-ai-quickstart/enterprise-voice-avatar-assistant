import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const ADMIN_PASSWORD = "e2e-admin-password";
const N8N_URL = process.env.E2E_N8N_URL ?? "http://localhost:15678";

export interface N8nCall {
  path: string;
  token: boolean;
  body: Record<string, any>;
}

/** Clears what the fake n8n recorded; with slack, it answers like WF4 with Slack on. */
export async function resetN8n(request: APIRequestContext, slack = false) {
  await request.post(`${N8N_URL}/_reset`, { data: { slack } });
}

export async function n8nCalls(request: APIRequestContext): Promise<N8nCall[]> {
  return (await request.get(`${N8N_URL}/_calls`)).json();
}

/** Waits until the fake n8n has recorded a call matching `match`, and returns it. */
export async function n8nCall(request: APIRequestContext, match: (c: N8nCall) => boolean): Promise<N8nCall> {
  let found: N8nCall | undefined;
  await expect
    .poll(async () => {
      found = (await n8nCalls(request)).find(match);
      return Boolean(found);
    })
    .toBe(true);
  return found!;
}

export async function signIn(page: Page, name: string) {
  await page.goto("/admin/login");
  await page.getByLabel("Your name").fill(name);
  await page.getByLabel("Admin password").fill(ADMIN_PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

/** Opens the chat, a new conversation, talking as `name`. */
export async function openChat(page: Page, name: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Set your name" }).click();
  await page.getByLabel("Your name, used to remember facts about you").fill(name);
  await page.getByRole("button", { name: "Save name" }).click();
  await expect(page.getByText(`Hi ${name.split(" ")[0]}, welcome.`)).toBeVisible();
}

/** Sends `text` in the chat and returns the assistant's reply once it is written. */
export async function send(page: Page, text: string) {
  const replies = page.locator("article.message.assistant");
  const before = await replies.count();
  await page.getByLabel("Your question").fill(text);
  await page.keyboard.press("Enter");
  const reply = replies.nth(before);
  await expect(reply).toBeVisible();
  await expect(reply.locator("p.pending")).toHaveCount(0);
  return reply;
}

/** Files a service request in the chat and returns its reference (REQ-000001). */
export async function fileRequest(page: Page, text: string): Promise<string> {
  const reply = await send(page, text);
  await expect(reply).toContainText("I've logged your request REQ-");
  const ref = (await reply.textContent())?.match(/REQ-\d{6}/)?.[0];
  expect(ref).toBeTruthy();
  return ref!;
}

export async function chatSession(page: Page): Promise<string> {
  return page.evaluate(() => sessionStorage.getItem("assistant.session") ?? "");
}
