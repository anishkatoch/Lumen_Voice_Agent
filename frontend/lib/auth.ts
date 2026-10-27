import { API, User } from "./api";

export function getToken(): string | null {
  return localStorage.getItem("access_token");
}

export function getRefreshToken(): string | null {
  return localStorage.getItem("refresh_token");
}

export function setTokens(access: string, refresh: string) {
  localStorage.setItem("access_token", access);
  localStorage.setItem("refresh_token", refresh);
}

export function clearTokens() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  localStorage.removeItem("user");
}

export function getUser(): User | null {
  const raw = localStorage.getItem("user");
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

export function setUser(user: User) {
  localStorage.setItem("user", JSON.stringify(user));
}

export async function tryRefresh(): Promise<boolean> {
  const rt = getRefreshToken();
  if (!rt) return false;
  try {
    const data = await API.refresh(rt);
    setTokens(data.access_token, data.refresh_token);
    return true;
  } catch {
    clearTokens();
    return false;
  }
}

export function isLoggedIn(): boolean {
  return !!getToken();
}

// ── Social sign-in (Google / Microsoft / GitHub via Supabase OAuth) ────────
export type OAuthProvider = "google" | "azure" | "github";

/**
 * Redirects the browser to Supabase's hosted OAuth authorize endpoint.
 * Supabase handles the provider handshake and redirects back to `/auth`
 * with the session tokens in the URL hash (see app/auth/page.tsx).
 *
 * Requires NEXT_PUBLIC_SUPABASE_URL to be set, the provider enabled in the
 * Supabase dashboard (Authentication → Providers), and this app's /auth
 * URL added under Authentication → URL Configuration → Redirect URLs.
 */
export function signInWithProvider(provider: OAuthProvider) {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  if (!supabaseUrl) {
    throw new Error("Social sign-in is not configured (missing NEXT_PUBLIC_SUPABASE_URL).");
  }
  const redirectTo = `${window.location.origin}/auth`;
  const url = `${supabaseUrl}/auth/v1/authorize?provider=${provider}&redirect_to=${encodeURIComponent(redirectTo)}`;
  window.location.href = url;
}
