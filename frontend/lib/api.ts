const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8004";

// ── Token refresh helper (inline to avoid circular import with auth.ts) ────────
async function _tryRefreshTokens(): Promise<string | null> {
  if (typeof window === "undefined") return null;
  const rt = localStorage.getItem("refresh_token");
  if (!rt) {
    _redirectToSignIn();
    return null;
  }
  try {
    const res = await fetch(`${BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) {
      _redirectToSignIn();
      return null;
    }
    const data = await res.json();
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);
    return data.access_token as string;
  } catch {
    return null;
  }
}

function _redirectToSignIn() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
  // Only redirect if not already on an auth page
  const path = window.location.pathname;
  if (!path.startsWith("/signin") && !path.startsWith("/signup") && !path.startsWith("/auth")) {
    window.location.replace("/signin");
  }
}

export const API = {
  base: BASE,
  ws: BASE.replace(/^http/, "ws"),

  async request<T>(path: string, init?: RequestInit): Promise<T> {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const makeReq = (tok: string | null) =>
      fetch(`${BASE}${path}`, {
        ...init,
        headers: {
          "Content-Type": "application/json",
          ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
          ...init?.headers,
        },
      });

    let res = await makeReq(token);

    // Auto-refresh on 401 and retry once
    if (res.status === 401) {
      const newToken = await _tryRefreshTokens();
      if (newToken) {
        res = await makeReq(newToken);
      } else {
        throw new Error("Session expired. Please sign in again.");
      }
    }

    if (!res.ok) {
      let detail = `Request failed (${res.status})`;
      try {
        const data = await res.json();
        detail = data.detail ?? data.error ?? data.message ?? detail;
      } catch { /* not JSON */ }
      throw new Error(detail);
    }
    const data = await res.json();
    return data as T;
  },

  async upload<T>(path: string, formData: FormData): Promise<T> {
    const token = typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
    const makeReq = (tok: string | null) =>
      fetch(`${BASE}${path}`, {
        method: "POST",
        headers: tok ? { Authorization: `Bearer ${tok}` } : {},
        body: formData,
      });

    let res = await makeReq(token);

    // Auto-refresh on 401 and retry once
    if (res.status === 401) {
      const newToken = await _tryRefreshTokens();
      if (newToken) {
        res = await makeReq(newToken);
      } else {
        throw new Error("Session expired. Please sign in again.");
      }
    }

    if (!res.ok) {
      let detail = `Upload failed (${res.status})`;
      try {
        const data = await res.json();
        detail = data.detail ?? data.error ?? data.message ?? detail;
      } catch { /* not JSON */ }
      throw new Error(detail);
    }
    return res.json() as Promise<T>;
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

  guestLogin: (name: string, email: string) =>
    API.request<{ access_token: string; refresh_token: string; user: User }>(
      "/auth/guest", { method: "POST", body: JSON.stringify({ name, email }) }
    ),

  forgotPassword: (email: string) =>
    API.request<{ message: string }>("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) }),

  verifyOtp: (email: string, token: string, password: string) =>
    API.request<{ message: string }>("/auth/verify-otp", { method: "POST", body: JSON.stringify({ email, token, password }) }),

  verifySignupOtp: (email: string, token: string) =>
    API.request<{ access_token: string; refresh_token: string; user: User }>(
      "/auth/verify-signup-otp", { method: "POST", body: JSON.stringify({ email, token }) }
    ),

  refresh: (refresh_token: string) =>
    API.request<{ access_token: string; refresh_token: string }>(
      "/auth/refresh", { method: "POST", body: JSON.stringify({ refresh_token }) }
    ),

  me: () => API.request<User>("/api/me"),

  // Conversations (voice sessions)
  conversations: () => API.request<Conversation[]>("/api/conversations"),
  messages: (id: string) => API.request<Message[]>(`/api/conversations/${id}/messages`),

  // Agents
  agents: () => API.request<Agent[]>("/api/agents"),
  createAgent: (data: { name: string; description?: string; system_prompt?: string; barge_in_sensitivity?: number }) =>
    API.request<Agent>("/api/agents", { method: "POST", body: JSON.stringify(data) }),
  getAgent: (id: string) => API.request<Agent>(`/api/agents/${id}`),
  updateAgent: (id: string, data: Partial<Agent>) =>
    API.request<Agent>(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteAgent: (id: string) =>
    API.request<void>(`/api/agents/${id}`, { method: "DELETE" }),
  duplicateAgent: (id: string) =>
    API.request<Agent>(`/api/agents/${id}/duplicate`, { method: "POST" }),

  // Agent documents
  agentDocuments: (agentId: string) =>
    API.request<{ documents: AgentDocument[]; storage_used_bytes: number; storage_limit_bytes: number }>(
      `/api/agents/${agentId}/documents`
    ),
  uploadDocument: (agentId: string, file: File, scope: "personal" | "global") => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("scope", scope);
    return API.upload<AgentDocument>(`/api/agents/${agentId}/documents`, fd);
  },
  deleteDocument: (agentId: string, docId: string) =>
    API.request<void>(`/api/agents/${agentId}/documents/${docId}`, { method: "DELETE" }),

  // Agent Functions
  agentFunctions: (agentId: string) =>
    API.request<AgentFunction[]>(`/api/agents/${agentId}/functions`),
  createAgentFunction: (agentId: string, data: Omit<AgentFunction, "id" | "agent_id" | "created_at">) =>
    API.request<AgentFunction>(`/api/agents/${agentId}/functions`, { method: "POST", body: JSON.stringify(data) }),
  updateAgentFunction: (agentId: string, funcId: string, data: Partial<Omit<AgentFunction, "id" | "agent_id" | "created_at">>) =>
    API.request<AgentFunction>(`/api/agents/${agentId}/functions/${funcId}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteAgentFunction: (agentId: string, funcId: string) =>
    API.request<void>(`/api/agents/${agentId}/functions/${funcId}`, { method: "DELETE" }),

  // User Knowledge Base
  userKbDocuments: () =>
    API.request<{ documents: UserDocument[]; storage_used_bytes: number; storage_limit_bytes: number }>("/api/user-kb/documents"),
  uploadUserKbDocument: (file: File, displayName?: string) => {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("display_name", displayName ?? file.name);
    return API.upload<UserDocument>("/api/user-kb/documents", fd);
  },
  deleteUserKbDocument: (docId: string) =>
    API.request<void>(`/api/user-kb/documents/${docId}`, { method: "DELETE" }),

  // KB permissions
  myKbPermission: () => API.request<{ can_upload_global_kb: boolean }>("/api/kb/my-permission"),
  requestKbAccess: (user_email: string) =>
    API.request("/api/kb/request-access", { method: "POST", body: JSON.stringify({ user_email }) }),

  // HR Manager
  hrCandidates: () => API.request<HRCandidate[]>("/api/hr/candidates"),
  createCandidate: (data: Omit<HRCandidate, "id" | "user_id" | "created_at" | "resume_file_name">) =>
    API.request<HRCandidate>("/api/hr/candidates", { method: "POST", body: JSON.stringify(data) }),
  updateCandidate: (id: string, data: Partial<Omit<HRCandidate, "id" | "user_id" | "created_at">>) =>
    API.request<HRCandidate>(`/api/hr/candidates/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteCandidate: (id: string) =>
    API.request<void>(`/api/hr/candidates/${id}`, { method: "DELETE" }),
  uploadCandidateResume: (candidateId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return API.upload<HRCandidate>(`/api/hr/candidates/${candidateId}/resume`, fd);
  },
  hrInterviews: () => API.request<HRInterview[]>("/api/hr/interviews"),
  createInterview: (data: { candidate_id: string; agent_id: string; scheduled_at: string; call_lead_minutes: number; specific_questions?: string }) =>
    API.request<HRInterview>("/api/hr/interviews", { method: "POST", body: JSON.stringify(data) }),
  updateInterview: (id: string, data: Partial<{ scheduled_at: string; call_lead_minutes: number; specific_questions: string; agent_id: string; status: string }>) =>
    API.request<HRInterview>(`/api/hr/interviews/${id}`, { method: "PUT", body: JSON.stringify(data) }),
  deleteInterview: (id: string) =>
    API.request<void>(`/api/hr/interviews/${id}`, { method: "DELETE" }),

  // Twilio Phone Numbers
  phoneNumbers: () => API.request<PhoneNumber[]>("/api/twilio/numbers"),
  addPhoneNumber: (data: { phone_number: string; friendly_name?: string }) =>
    API.request<PhoneNumber>("/api/twilio/numbers", { method: "POST", body: JSON.stringify(data) }),
  updatePhoneNumber: (numberId: string, data: { friendly_name?: string; is_active?: boolean }) =>
    API.request<PhoneNumber>(`/api/twilio/numbers/${numberId}`, { method: "PUT", body: JSON.stringify(data) }),
  attachPhoneNumber: (numberId: string, agentId: string) =>
    API.request<PhoneNumber>(`/api/twilio/numbers/${numberId}/attach`, { method: "POST", body: JSON.stringify({ agent_id: agentId }) }),
  detachPhoneNumber: (numberId: string) =>
    API.request<PhoneNumber>(`/api/twilio/numbers/${numberId}/attach`, { method: "DELETE" }),
  deletePhoneNumber: (numberId: string) =>
    API.request<void>(`/api/twilio/numbers/${numberId}`, { method: "DELETE" }),

  // Admin
  isAdmin: () => API.request<{ is_admin: boolean }>("/api/admin/is-admin"),
  adminKbRequests: () => API.request<KbAccessRequest[]>("/api/admin/kb-requests"),
  adminApproveKb: (id: string) => API.request(`/api/admin/kb-requests/${id}/approve`, { method: "POST" }),
  adminRejectKb: (id: string) => API.request(`/api/admin/kb-requests/${id}/reject`, { method: "POST" }),
  adminRevokeKb: (userId: string) => API.request(`/api/admin/users/${userId}/revoke-kb`, { method: "POST" }),
  adminKbUsers: () => API.request<KbPermittedUser[]>("/api/admin/kb-users"),
};

// ── Types ─────────────────────────────────────────────────────────────────────

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
  agent_id?: string | null;
  sentiment?: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | null;
  dominant_emotion?: string | null;
  sentiment_score?: number | null;
}

export interface Message {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface Agent {
  id: string;
  user_id: string;
  name: string;
  description?: string | null;
  system_prompt?: string | null;
  barge_in_sensitivity: number;
  first_message_enabled: boolean;
  first_message?: string | null;
  is_active: boolean;
  kb_instructions?: string | null;
  hr_instructions?: string | null;
  selected_kb_doc_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface FunctionParameter {
  name: string;
  type: "string" | "number" | "boolean" | "object" | "array";
  description: string;
}

export interface AgentFunction {
  id: string;
  agent_id: string;
  name: string;
  description?: string | null;
  method: string;
  url: string;
  timeout_ms: number;
  headers: Record<string, string>;
  query_params: Record<string, string>;
  body_schema?: string | null;
  payload_args_only: boolean;
  parameters: FunctionParameter[];
  trigger_type: string;
  created_at: string;
}

export interface UserDocument {
  id: string;
  user_id: string;
  display_name: string;
  file_name: string;
  file_size: number;
  file_type: string;
  created_at: string;
}

export interface AgentDocument {
  id: string;
  agent_id: string;
  user_id: string;
  file_name: string;
  file_size: number;
  file_type: string;
  scope: "personal" | "global";
  created_at: string;
}

export interface KbAccessRequest {
  id: string;
  user_id: string;
  user_email: string;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  resolved_at?: string | null;
  resolved_by?: string | null;
}

export interface KbPermittedUser {
  user_id: string;
  can_upload_global_kb: boolean;
  granted_at?: string | null;
  granted_by?: string | null;
}

export interface HRCandidate {
  id: string;
  user_id: string;
  name: string;
  phone: string;
  email?: string | null;
  role?: string | null;
  resume_text?: string | null;
  resume_file_name?: string | null;
  notes?: string | null;
  created_at: string;
}

export interface HRInterview {
  id: string;
  user_id: string;
  candidate_id: string;
  agent_id: string;
  scheduled_at: string;
  call_lead_minutes: number;
  specific_questions?: string | null;
  status: "pending" | "calling" | "completed" | "failed" | "cancelled";
  called_at?: string | null;
  call_sid?: string | null;
  created_at: string;
  hr_candidates?: { name: string; phone: string; role?: string | null } | null;
}

export interface PhoneNumber {
  id: string;
  user_id: string;
  phone_number: string;
  friendly_name?: string | null;
  agent_id?: string | null;
  is_active: boolean;
  created_at: string;
}
