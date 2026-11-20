import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { people } from "../test-data";
import { api } from "./api";
import { HandlerSession } from "./HandlerSession";

interface TestSession {
  access_token: string;
  user: { id: string };
}
const auth = vi.hoisted(() => {
  vi.stubEnv("VITE_SUPABASE_URL", "http://localhost:9999");
  vi.stubEnv("VITE_SUPABASE_PUBLISHABLE_KEY", "test-key");
  return {
    onAuthStateChange: vi.fn(),
    signInWithPassword: vi.fn(),
    signOut: vi.fn(),
  };
});
vi.mock("@supabase/supabase-js", () => ({ createClient: () => ({ auth }) }));

const dana = people[0];
const session: TestSession = {
  access_token: "test-access-token",
  user: { id: "test-user" },
};
let savedSession: TestSession | null;
let emit: (event: string, value: TestSession | null) => void;
const fetch =
  vi.fn<(url: string, options?: RequestInit) => Promise<Response>>();

beforeEach(() => {
  savedSession = null;
  auth.onAuthStateChange.mockImplementation((listener: typeof emit) => {
    emit = listener;
    listener("INITIAL_SESSION", savedSession);
    return { data: { subscription: { unsubscribe: vi.fn() } } };
  });
  auth.signInWithPassword.mockImplementation(() => {
    emit("SIGNED_IN", session);
    return Promise.resolve({ error: null });
  });
  auth.signOut.mockImplementation(() => {
    emit("SIGNED_OUT", null);
    return Promise.resolve({ error: null });
  });
  fetch.mockImplementation((path) =>
    Promise.resolve(Response.json(path === "/api/me" ? dana : [])),
  );
  vi.stubGlobal("fetch", fetch);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

it("gates a direct link, retries invalid credentials, and signs out", async () => {
  window.history.replaceState(null, "", "?page=scoreboard");
  render(<HandlerSession />);
  expect(
    await screen.findByRole("heading", { name: "Handler sign-in" }),
  ).toBeDefined();
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Email"), {
    target: { value: dana.email },
  });
  fireEvent.change(screen.getByLabelText("Password"), {
    target: { value: "test-password" },
  });
  auth.signInWithPassword.mockResolvedValueOnce({
    error: new Error("Invalid credentials"),
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "Invalid credentials",
  );
  fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
  expect(
    await screen.findByRole("heading", { name: "Scoreboard" }),
  ).toBeDefined();
  expect(screen.getByText(dana.name)).toBeDefined();
  expect(auth.signInWithPassword).toHaveBeenCalledWith({
    email: dana.email,
    password: "test-password",
  });
  expect(fetch).toHaveBeenCalledWith("/api/me", {
    headers: { Authorization: "Bearer test-access-token" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
  expect(
    await screen.findByRole("heading", { name: "Handler sign-in" }),
  ).toBeDefined();
  expect(screen.queryByRole("navigation")).toBeNull();
});

it("restores a session and uses refreshed access tokens for actions", async () => {
  savedSession = session;
  render(<HandlerSession />);
  expect(await screen.findByText(dana.name)).toBeDefined();
  act(() => {
    emit("TOKEN_REFRESHED", { ...session, access_token: "fresh-token" });
  });
  await api("/dispatch", { agents_at_once: 3 });
  expect(fetch).toHaveBeenCalledWith("/api/dispatch", {
    method: "POST",
    headers: {
      Authorization: "Bearer fresh-token",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ agents_at_once: 3 }),
  });
});

it("withholds the dashboard when the signed-in account has no handler", async () => {
  savedSession = session;
  fetch.mockResolvedValueOnce(
    Response.json({ detail: "No handler for this email" }, { status: 403 }),
  );
  render(<HandlerSession />);
  expect(await screen.findByRole("alert")).toHaveProperty(
    "textContent",
    "No handler for this email",
  );
  expect(screen.queryByRole("navigation")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText(dana.name)).toBeDefined();
});
