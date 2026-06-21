const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8003";

export const API = {
  base: BASE,
  ws: BASE.replace(/^http/, "ws"),

  async request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init?.headers,
      },
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? "Request failed");
    return data as T;
  },

  // Auth
  signUp: (email: string, password: string, full_name?: string) =>
    API.request<{ access_token?: string; refresh_token?: string; user?: User; message?: string }>(
      "/auth/signup", { method: "POST", body: JSON.stringify({ email, password, full_name }) }
    ),

  signIn: (email: string, password: string) =>
    API.request<{ access_token: string; refresh_token: string; user: User }>(
      "/auth/signin", { method: "POST", body: JSON.stringify({ email, password }) }
    ),

  signOut: () => API.request("/auth/signout", { method: "POST" }),

  forgotPassword: (email: string) =>
    API.request<{ message: string }>("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) }),

  verifyOtp: (email: string, token: string, password: string) =>
    API.request<{ message: string }>("/auth/verify-otp", { method: "POST", body: JSON.stringify({ email, token, password }) }),

  refresh: (refresh_token: string) =>
    API.request<{ access_token: string; refresh_token: string }>(
      "/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token }) }
    ),

  me: () => API.request<User>("/api/me"),

  conversations: () => API.request<Conversation[]>("/api/conversations"),

  messages: (id: string) => API.request<Message[]>(`/api/conversations/${id}/messages`),
};

export interface User {
  id: string;
  email: string;
  full_name?: string;
  role: string;
}

export interface Conversation {
  id: string;
  user_id: string;
  title: string;
  created_at: string;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}
