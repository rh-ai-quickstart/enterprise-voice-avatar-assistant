import { expect, test } from "@playwright/test";
import { fileRequest, openChat, resetN8n, signIn } from "./helpers";

// Regenerates docs/images/admin-portal-approvals.png on a fresh stack:
//   E2E_SCREENSHOT=1 npx playwright test screenshot
test.skip(!process.env.E2E_SCREENSHOT, "only when regenerating the documentation's screenshot");

test("the Approvals page, for the documentation", async ({ browser, request }) => {
  await resetN8n(request, true);
  const refs: string[] = [];
  for (const [name, text] of [
    ["Alex Kim", "I need a new laptop, mine no longer boots."],
    ["Sam Lee", "Please give me access to the finance share."],
    ["Priya Shah", "I need Visual Studio Code installed on my laptop, please."],
  ]) {
    const context = await browser.newContext();
    const page = await context.newPage();
    await openChat(page, name);
    refs.push(await fileRequest(page, text));
    await context.close();
  }
  const context = await browser.newContext({ viewport: { width: 1440, height: 820 }, colorScheme: "light" });
  const admin = await context.newPage();
  await signIn(admin, "Dana");
  await admin.goto(`/admin/approvals/${refs[1]}`);
  await expect(admin.getByRole("link", { name: "Slack card" })).toBeVisible();
  await admin.screenshot({ path: "../docs/images/admin-portal-approvals.png" });
});
