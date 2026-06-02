import { expect, test } from "@playwright/test";

/**
 * Replicates the manual register -> verify -> login flow.
 *
 * Requires:
 *   - frontend dev server on :5173
 *   - backend on :8000
 *   - MailHog on :8025 (its REST API at /api/v2/search)
 *
 * The verification token is extracted from the most recent MailHog message
 * sent to the test email.
 */

const FRONTEND = process.env.E2E_BASE_URL ?? "http://localhost:5173";
const MAILHOG = process.env.E2E_MAILHOG_URL ?? "http://localhost:8025";
const TEST_PASSWORD = "TestPass1234!";

function uniqueEmail(): string {
  return `e2e-${Date.now()}-${Math.floor(Math.random() * 1e4)}@example.com`;
}

async function fetchVerifyToken(email: string): Promise<string> {
  // MailHog v2 search API
  const url = `${MAILHOG}/api/v2/search?kind=to&query=${encodeURIComponent(email)}`;
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`MailHog search failed: ${res.status}`);
  }
  const data = (await res.json()) as { items?: Array<{ Content?: { Body?: string } }> };
  const items = data.items ?? [];
  if (items.length === 0) {
    throw new Error(`No mail found for ${email}`);
  }
  const body = items[0].Content?.Body ?? "";
  const match = body.match(/verify-email\?token=([A-Za-z0-9._\-]+)/);
  if (!match) {
    throw new Error("No verify-email token in MailHog body");
  }
  return match[1];
}

test("register -> verify-email -> login renders home", async ({ page }) => {
  const email = uniqueEmail();

  await page.goto(`${FRONTEND}/register`);
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/display name/i).fill("E2E User");
  await page.getByLabel(/organization/i).fill("Test");
  await page.getByLabel(/^password$/i).fill(TEST_PASSWORD);
  await page.getByLabel(/confirm/i).fill(TEST_PASSWORD);
  await page.getByRole("button", { name: /sign up|register/i }).click();

  await expect(page).toHaveURL(/verify|check-email|login/i, { timeout: 10_000 });

  // Poll MailHog briefly until the verification email arrives.
  let token = "";
  for (let i = 0; i < 20; i++) {
    try {
      token = await fetchVerifyToken(email);
      break;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  expect(token, "verification token not received").not.toEqual("");

  await page.goto(`${FRONTEND}/verify-email?token=${token}`);
  // Verify-email page should redirect or show a success state.
  await expect(page.locator("body")).toContainText(/verified|login|signed in|success/i, {
    timeout: 10_000,
  });

  await page.goto(`${FRONTEND}/login`);
  await page.getByLabel(/email/i).fill(email);
  await page.getByLabel(/password/i).fill(TEST_PASSWORD);
  await page.getByRole("button", { name: /log in|sign in|login/i }).click();

  await expect(page).toHaveURL(/\/$|home|apps|catalog/i, { timeout: 10_000 });
  await expect(page.locator("body")).not.toContainText(/error|exception/i);
});
