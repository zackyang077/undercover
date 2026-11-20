import { expect, test as base, type Page } from "@playwright/test";
export {
  expect,
  type Page,
  type APIRequestContext,
  type Locator,
} from "@playwright/test";
import { fixturePassword } from "./servers";

export async function signIn(
  page: Page,
  name: "Dana Whitfield" | "Marcus Oyelaran",
) {
  const email = name.toLowerCase().replace(" ", ".") + "@undercover.example";
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fixturePassword);
  const session = page.waitForResponse(
    (response) =>
      response.url().includes("/auth/v1/token?") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  const response = await session;
  expect(response.status()).toBe(200);
  const { access_token } = (await response.json()) as { access_token: string };
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
  return { Authorization: `Bearer ${access_token}` };
}

export const test = base.extend<{ handlerHeaders: Record<string, string> }>({
  handlerHeaders: [
    async ({ page }, use) => {
      await page.goto("/");
      await use(await signIn(page, "Dana Whitfield"));
    },
    { auto: true },
  ],
});
