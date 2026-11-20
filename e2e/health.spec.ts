import { expect, test } from "./session";

test("the dashboard shows the claims queue", async ({ page }) => {
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await expect(
    page.getByRole("heading", { name: "Claims desk" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { pressed: true })).toContainText(
    "CLM-1001",
  );
  await expect(
    page
      .getByRole("complementary", { name: "Claims queue" })
      .getByRole("button"),
  ).toHaveCount(25);
  await expect(page.getByRole("heading", { name: "Maya Patel" })).toBeVisible();
});
