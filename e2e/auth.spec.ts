import { expect, test } from "@playwright/test";
import { signIn } from "./session";

test("a signed-out direct visit requires sign-in and sign-out clears restored access", async ({
  page,
  request,
}) => {
  await page.goto("/?claim=CLM-1003");
  const form = page.getByRole("button", { name: "Sign in", exact: true });
  await expect(form).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Main navigation" }),
  ).toHaveCount(0);
  const refused = await request.post("/api/claims/CLM-1003/runs");
  expect(refused.status()).toBe(401);
  await signIn(page, "Marcus Oyelaran");
  await expect(page.locator(".claim-heading")).toContainText("CLM-1003");
  await page.reload();
  await expect(page.locator(".claim-heading")).toContainText("CLM-1003");
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.reload();
  await expect(form).toBeVisible();
});
