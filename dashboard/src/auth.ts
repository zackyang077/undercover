import { createClient } from "@supabase/supabase-js";

const url: string | undefined = import.meta.env.VITE_SUPABASE_URL;
const publishableKey: string | undefined = import.meta.env
  .VITE_SUPABASE_PUBLISHABLE_KEY;
const auth =
  url && publishableKey ? createClient(url, publishableKey).auth : null;

export const authConfigured = !!auth;
let accessToken: string | undefined;

export function accessHeaders(): Record<string, string> {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

export function watchSession(onChange: (userId: string | null) => void) {
  const subscription = auth?.onAuthStateChange((_event, session) => {
    accessToken = session?.access_token;
    onChange(session?.user.id ?? null);
  }).data.subscription;
  return () => subscription?.unsubscribe();
}

export async function signIn(email: string, password: string) {
  if (!auth) throw new Error("Handler sign-in is not configured.");
  const { error } = await auth.signInWithPassword({ email, password });
  if (error) throw error;
}

export async function signOut() {
  if (!auth) return;
  const { error } = await auth.signOut({ scope: "local" });
  if (error) throw error;
}
