import { expect, test, signIn } from "./session";

test("saves business settings, preserves browser appearance, and explains handler access", async ({
  page,
}) => {
  await page.getByRole("button", { name: "Configuration" }).click();
  await expect(page.getByText("Current", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Instructions", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  const budget = page.getByRole("spinbutton", {
    name: "Spending limit per round (USD)",
  });
  const initial = await budget.inputValue();
  const next = (Number(initial) + 0.01).toFixed(2);
  await budget.fill(next);
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "Spending limit per round",
  );
  await page.getByRole("button", { name: "Confirm changes" }).click();
  await expect(page.getByRole("status")).toHaveText("Settings saved.");
  await page.getByLabel("Appearance", { exact: true }).selectOption("dark");
  await page.getByRole("button", { name: "Apply appearance" }).click();
  await page.reload();
  await expect(page.getByLabel("Appearance", { exact: true })).toHaveValue(
    "dark",
  );
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(budget).toHaveValue(next);
  await page.getByRole("button", { name: "Sign out" }).click();
  await signIn(page, "Marcus Oyelaran");
  await expect(budget).toBeDisabled();
  await expect(
    page.getByText("Only a senior handler can change platform settings."),
  ).toBeVisible();
});
