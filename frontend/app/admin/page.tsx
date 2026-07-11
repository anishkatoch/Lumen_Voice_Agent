"use client";
import { useState, useEffect, useCallback } from "react";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8004";
const ADMIN_EMAIL = "katochaneesh@gmail.com";

// ── Palette (matches main app) ────────────────────────────────────────────────
const C = {
  bg:         "#f8f9fc",
  surface:    "#ffffff",
  surfaceBdr: "#e5e7eb",
  header:     "rgba(255,255,255,0.95)",
  text:       "#111827",
  muted:      "#6b7280",
  accent:     "#7c6cfc",
  errBg:      "#fef2f2",
  errBdr:     "#fecaca",
  errText:    "#dc2626",
  green:      "#16a34a",
};

// ── Tiny API helpers ──────────────────────────────────────────────────────────
function getToken() {
  return typeof window !== "undefined" ? localStorage.getItem("access_token") : null;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try { const d = await res.json(); detail = d.detail ?? d.error ?? detail; } catch { /* noop */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ── Types ─────────────────────────────────────────────────────────────────────
interface AdminConfig {
  stt_provider:     string;
  tts_provider:     string;
  tts_openai_voice: string;
  tts_openai_model: string;
  llm_provider:     string;
  llm_model:        string;
  llm_api_key:      string;
  llm_base_url:     string;
  user_kb_max_mb:   string;
  agent_kb_max_mb:  string;
}

interface KbRequest {
  id: string; user_id: string; user_email: string;
  status: string; created_at: string;
}
interface KbUser { user_id: string; granted_at?: string | null; }

// ── Sub-components ────────────────────────────────────────────────────────────
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-6 rounded-xl border p-5" style={{ borderColor: C.surfaceBdr, background: C.surface }}>
      <h3 className="text-sm font-semibold mb-4 pb-2 border-b" style={{ color: C.text, borderColor: C.surfaceBdr }}>{title}</h3>
      {children}
    </div>
  );
}

function RadioGroup({ label, value, options, onChange }: {
  label: string; value: string; options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="mb-4">
      <p className="text-xs font-medium mb-2" style={{ color: C.muted }}>{label}</p>
      <div className="flex flex-wrap gap-2">
        {options.map(o => (
          <button key={o.value} onClick={() => onChange(o.value)}
            className="px-3 py-1.5 rounded-full text-sm font-medium border transition-all"
            style={{
              background: value === o.value ? C.accent : C.surface,
              color: value === o.value ? "white" : C.muted,
              borderColor: value === o.value ? C.accent : C.surfaceBdr,
            }}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, type = "text", hint }: {
  label: string; value: string; onChange: (v: string) => void;
  placeholder?: string; type?: string; hint?: string;
}) {
  const [show, setShow] = useState(false);
  const isPassword = type === "password";
  return (
    <div className="mb-3">
      <label className="block text-xs font-medium mb-1" style={{ color: C.muted }}>{label}</label>
      <div className="relative">
        <input
          type={isPassword && !show ? "password" : "text"}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className="w-full px-3 py-2 rounded-lg text-sm outline-none border"
          style={{ borderColor: C.surfaceBdr, background: "#f9fafb", color: C.text, paddingRight: isPassword ? "2.5rem" : undefined }}
        />
        {isPassword && (
          <button type="button" onClick={() => setShow(s => !s)}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-xs"
            style={{ color: C.muted }}>
            {show ? "Hide" : "Show"}
          </button>
        )}
      </div>
      {hint && <p className="text-xs mt-1" style={{ color: C.muted }}>{hint}</p>}
    </div>
  );
}

// ── Providers Tab ─────────────────────────────────────────────────────────────
function ProvidersTab({ config, onSave }: { config: AdminConfig; onSave: (updates: Partial<AdminConfig>) => Promise<void> }) {
  const [local, setLocal] = useState<AdminConfig>(config);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { setLocal(config); }, [config]);
  const set = (k: keyof AdminConfig) => (v: string) => setLocal(p => ({ ...p, [k]: v }));

  async function handleSave() {
    setSaving(true); setErr(""); setSaved(false);
    try { await onSave(local); setSaved(true); setTimeout(() => setSaved(false), 2000); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : "Save failed"); }
    finally { setSaving(false); }
  }

  return (
    <div className="max-w-2xl">
      <Section title="Speech-to-Text (STT)">
        <RadioGroup label="STT Provider" value={local.stt_provider} onChange={set("stt_provider")}
          options={[
            { value: "deepgram",       label: "Deepgram (default)" },
            { value: "openai_whisper", label: "OpenAI Whisper" },
          ]} />
        {local.stt_provider === "openai_whisper" && (
          <p className="text-xs p-2 rounded-lg" style={{ background: "#fffbeb", color: "#92400e" }}>
            Whisper is batch-only (no streaming). Each utterance is sent to OpenAI after silence is detected.
            Uses the OpenAI API key configured under LLM.
          </p>
        )}
      </Section>

      <Section title="Text-to-Speech (TTS)">
        <RadioGroup label="TTS Provider" value={local.tts_provider} onChange={set("tts_provider")}
          options={[
            { value: "elevenlabs", label: "ElevenLabs (default)" },
            { value: "openai_tts", label: "OpenAI TTS" },
          ]} />
        {local.tts_provider === "openai_tts" && (
          <div className="mt-3 space-y-1">
            <RadioGroup label="Voice" value={local.tts_openai_voice} onChange={set("tts_openai_voice")}
              options={["alloy","echo","fable","onyx","nova","shimmer"].map(v => ({ value: v, label: v }))} />
            <RadioGroup label="Model" value={local.tts_openai_model} onChange={set("tts_openai_model")}
              options={[{ value: "tts-1", label: "tts-1 (fast)" }, { value: "tts-1-hd", label: "tts-1-hd (quality)" }]} />
            <p className="text-xs p-2 rounded-lg" style={{ background: "#fffbeb", color: "#92400e" }}>
              OpenAI TTS outputs 24 kHz PCM; it is automatically resampled to 16 kHz.
            </p>
          </div>
        )}
      </Section>

      <Section title="Language Model (LLM)">
        <RadioGroup label="LLM Provider" value={local.llm_provider} onChange={set("llm_provider")}
          options={[
            { value: "openai",    label: "OpenAI (default)" },
            { value: "groq",      label: "Groq" },
            { value: "anthropic", label: "Anthropic" },
            { value: "ollama",    label: "Ollama (local)" },
          ]} />

        <div className="mt-3 space-y-0">
          <Field label="Model name" value={local.llm_model} onChange={set("llm_model")}
            placeholder={
              local.llm_provider === "groq"      ? "llama-3.1-8b-instant" :
              local.llm_provider === "anthropic"  ? "claude-haiku-4-5" :
              local.llm_provider === "ollama"     ? "llama3.2" :
                                                    "gpt-4o-mini"
            }
            hint={
              local.llm_provider === "groq"      ? "e.g. llama-3.1-8b-instant, mixtral-8x7b-32768" :
              local.llm_provider === "anthropic"  ? "e.g. claude-haiku-4-5, claude-sonnet-4-5" :
              local.llm_provider === "ollama"     ? "Any model pulled in Ollama (ollama list)" :
                                                    "e.g. gpt-4o-mini, gpt-4o"
            }
          />

          {local.llm_provider !== "openai" && (
            <Field label="API Key" value={local.llm_api_key} onChange={set("llm_api_key")}
              placeholder={
                local.llm_provider === "groq"      ? "gsk_..." :
                local.llm_provider === "anthropic"  ? "sk-ant-..." :
                                                      "Leave empty for Ollama"
              }
              type="password"
              hint="Leave empty to use the environment variable (GROQ_API_KEY / ANTHROPIC_API_KEY)."
            />
          )}

          {local.llm_provider === "openai" && (
            <Field label="API Key override" value={local.llm_api_key} onChange={set("llm_api_key")}
              placeholder="sk-... (leave empty to use OPENAI_API_KEY env var)"
              type="password" />
          )}

          {local.llm_provider === "ollama" && (
            <Field label="Ollama base URL" value={local.llm_base_url} onChange={set("llm_base_url")}
              placeholder="http://localhost:11434"
              hint="Default: http://localhost:11434. Change if Ollama runs on another host/port." />
          )}
        </div>
      </Section>

      {err && <p className="text-sm mb-3" style={{ color: C.errText }}>{err}</p>}

      <button onClick={handleSave} disabled={saving}
        className="px-8 py-2.5 rounded-xl font-bold text-sm"
        style={{ background: saved ? C.green : "linear-gradient(135deg,#7c6cfc,#9d8dfd)", color: "white" }}>
        {saving ? "Saving…" : saved ? "✓ Saved" : "Save Provider Settings"}
      </button>
    </div>
  );
}

// ── Storage Tab ───────────────────────────────────────────────────────────────
function StorageTab({ config, onSave }: { config: AdminConfig; onSave: (u: Partial<AdminConfig>) => Promise<void> }) {
  const [userMb,  setUserMb]  = useState(config.user_kb_max_mb);
  const [agentMb, setAgentMb] = useState(config.agent_kb_max_mb);
  const [saving, setSaving] = useState(false);
  const [saved,  setSaved]  = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => { setUserMb(config.user_kb_max_mb); setAgentMb(config.agent_kb_max_mb); }, [config]);

  async function handleSave() {
    setSaving(true); setErr(""); setSaved(false);
    try {
      await onSave({ user_kb_max_mb: userMb, agent_kb_max_mb: agentMb });
      setSaved(true); setTimeout(() => setSaved(false), 2000);
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : "Save failed"); }
    finally { setSaving(false); }
  }

  return (
    <div className="max-w-lg">
      <Section title="Knowledge Base Storage Limits">
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1" style={{ color: C.text }}>
              User KB limit (MB per user)
            </label>
            <p className="text-xs mb-2" style={{ color: C.muted }}>Max total document storage each user can upload to their personal Knowledge Base.</p>
            <div className="flex items-center gap-3">
              <input type="number" min="1" max="500" value={userMb}
                onChange={e => setUserMb(e.target.value)}
                className="w-24 px-3 py-2 rounded-lg text-sm border outline-none"
                style={{ borderColor: C.surfaceBdr, background: "#f9fafb", color: C.text }} />
              <span className="text-sm" style={{ color: C.muted }}>MB</span>
              <span className="text-xs" style={{ color: C.muted }}>({(parseInt(userMb || "5") * 1024 * 1024).toLocaleString()} bytes)</span>
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1" style={{ color: C.text }}>
              Agent documents limit (MB per agent)
            </label>
            <p className="text-xs mb-2" style={{ color: C.muted }}>Max total document storage per agent (personal + global docs combined).</p>
            <div className="flex items-center gap-3">
              <input type="number" min="1" max="500" value={agentMb}
                onChange={e => setAgentMb(e.target.value)}
                className="w-24 px-3 py-2 rounded-lg text-sm border outline-none"
                style={{ borderColor: C.surfaceBdr, background: "#f9fafb", color: C.text }} />
              <span className="text-sm" style={{ color: C.muted }}>MB</span>
              <span className="text-xs" style={{ color: C.muted }}>({(parseInt(agentMb || "5") * 1024 * 1024).toLocaleString()} bytes)</span>
            </div>
          </div>
        </div>

        {err && <p className="text-sm mt-3" style={{ color: C.errText }}>{err}</p>}
        <button onClick={handleSave} disabled={saving} className="mt-4 px-8 py-2.5 rounded-xl font-bold text-sm"
          style={{ background: saved ? C.green : "linear-gradient(135deg,#7c6cfc,#9d8dfd)", color: "white" }}>
          {saving ? "Saving…" : saved ? "✓ Saved" : "Save Storage Limits"}
        </button>
      </Section>
    </div>
  );
}

// ── KB Access Tab ─────────────────────────────────────────────────────────────
function KbAccessTab() {
  const [requests, setRequests] = useState<KbRequest[]>([]);
  const [users,    setUsers]    = useState<KbUser[]>([]);
  const [loading,  setLoading]  = useState(true);
  const [tab, setTab] = useState<"requests"|"users">("requests");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [reqs, us] = await Promise.all([
        api<KbRequest[]>("/api/admin/kb-requests"),
        api<KbUser[]>("/api/admin/kb-users"),
      ]);
      setRequests(reqs); setUsers(us);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const pending  = requests.filter(r => r.status === "pending");
  const resolved = requests.filter(r => r.status !== "pending");

  return (
    <div className="max-w-2xl">
      <div className="flex border-b mb-4 gap-1" style={{ borderColor: C.surfaceBdr }}>
        {(["requests","users"] as const).map(t => (
          <button key={t} onClick={() => setTab(t)}
            className="px-4 py-2.5 text-sm font-medium"
            style={{ color: tab === t ? C.accent : C.muted, borderBottom: tab === t ? `2px solid ${C.accent}` : "2px solid transparent" }}>
            {t === "requests" ? `Access Requests${pending.length ? ` (${pending.length})` : ""}` : "Approved Users"}
          </button>
        ))}
        <button onClick={load} className="ml-auto text-xs px-3 py-2" style={{ color: C.muted }}>↻ Refresh</button>
      </div>

      {loading ? (
        <p className="text-sm text-center py-8" style={{ color: C.muted }}>Loading…</p>
      ) : tab === "requests" ? (
        <div className="space-y-3">
          {pending.length === 0 && resolved.length === 0 && (
            <p className="text-sm text-center py-8" style={{ color: C.muted }}>No requests yet.</p>
          )}
          {pending.map(r => (
            <div key={r.id} className="p-4 rounded-xl border" style={{ borderColor: "#fde68a", background: "#fffbeb" }}>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="font-medium text-sm" style={{ color: C.text }}>{r.user_email}</p>
                  <p className="text-xs" style={{ color: C.muted }}>{new Date(r.created_at).toLocaleString()}</p>
                </div>
                <div className="flex gap-2">
                  <button onClick={async () => { await api(`/api/admin/kb-requests/${r.id}/approve`, { method: "POST" }); load(); }}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: "#dcfce7", color: "#15803d" }}>✓ Approve</button>
                  <button onClick={async () => { await api(`/api/admin/kb-requests/${r.id}/reject`, { method: "POST" }); load(); }}
                    className="px-3 py-1.5 rounded-lg text-xs font-semibold"
                    style={{ background: "#fee2e2", color: "#dc2626" }}>✕ Reject</button>
                </div>
              </div>
            </div>
          ))}
          {resolved.map(r => (
            <div key={r.id} className="p-3 rounded-lg border flex items-center justify-between" style={{ borderColor: C.surfaceBdr }}>
              <div>
                <p className="text-sm" style={{ color: C.text }}>{r.user_email}</p>
                <span className={`text-xs font-medium ${r.status === "approved" ? "text-green-600" : "text-red-500"}`}>
                  {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-2">
          {users.length === 0 && <p className="text-sm text-center py-8" style={{ color: C.muted }}>No users with Global KB access.</p>}
          {users.map(u => (
            <div key={u.user_id} className="p-3 rounded-lg border flex items-center justify-between" style={{ borderColor: C.surfaceBdr }}>
              <div>
                <p className="text-sm font-mono" style={{ color: C.text }}>{u.user_id.slice(0, 16)}…</p>
                {u.granted_at && <p className="text-xs" style={{ color: C.muted }}>Granted: {new Date(u.granted_at).toLocaleDateString()}</p>}
              </div>
              <button onClick={async () => { await api(`/api/admin/users/${u.user_id}/revoke-kb`, { method: "POST" }); load(); }}
                className="px-3 py-1.5 rounded-lg text-xs font-semibold"
                style={{ background: "#fee2e2", color: "#dc2626" }}>Revoke</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Admin Page ───────────────────────────────────────────────────────────
export default function AdminPage() {
  const [loggedIn,  setLoggedIn]  = useState(false);
  const [checkDone, setCheckDone] = useState(false);
  const [email,     setEmail]     = useState("");
  const [password,  setPassword]  = useState("");
  const [loginErr,  setLoginErr]  = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [config,    setConfig]    = useState<AdminConfig | null>(null);
  const [tab, setTab] = useState<"providers"|"storage"|"kb">("providers");

  // Check existing token on mount
  useEffect(() => {
    const token = getToken();
    if (!token) { setCheckDone(true); return; }
    api<AdminConfig>("/api/admin/config")
      .then(cfg => { setConfig(cfg); setLoggedIn(true); })
      .catch(() => { /* not admin or expired */ })
      .finally(() => setCheckDone(false));
    setCheckDone(true);
  }, []);

  async function handleLogin(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) return;
    setLoggingIn(true); setLoginErr("");
    try {
      const res = await api<{ access_token: string; user: { email: string } }>(
        "/auth/signin",
        { method: "POST", body: JSON.stringify({ email: email.trim(), password }) }
      );
      if (res.user.email !== ADMIN_EMAIL) {
        setLoginErr("Access denied — not the admin account.");
        return;
      }
      localStorage.setItem("access_token", res.access_token);
      const cfg = await api<AdminConfig>("/api/admin/config");
      setConfig(cfg);
      setLoggedIn(true);
    } catch (e: unknown) {
      setLoginErr(e instanceof Error ? e.message : "Login failed");
    } finally {
      setLoggingIn(false);
    }
  }

  function handleSignOut() {
    localStorage.removeItem("access_token");
    setLoggedIn(false);
    setConfig(null);
  }

  async function saveConfig(updates: Partial<AdminConfig>) {
    const updated = await api<AdminConfig>("/api/admin/config", {
      method: "PUT",
      body: JSON.stringify(updates),
    });
    setConfig(updated);
  }

  if (!checkDone) return null;

  // ── Sign-in screen ──────────────────────────────────────────────────────────
  if (!loggedIn) {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: C.bg }}>
        <div className="w-full max-w-sm">
          <div className="text-center mb-8">
            <div className="w-14 h-14 rounded-2xl flex items-center justify-center mx-auto mb-4"
              style={{ background: "linear-gradient(135deg,#7c6cfc,#9d8dfd)", boxShadow: "0 4px 20px rgba(124,108,252,0.4)" }}>
              <span className="text-white text-2xl">⚙</span>
            </div>
            <h1 className="text-2xl font-bold" style={{ color: C.text }}>Admin Panel</h1>
            <p className="text-sm mt-1" style={{ color: C.muted }}>Sign in with your admin account</p>
          </div>

          <form onSubmit={handleLogin} className="rounded-2xl p-6 space-y-4 shadow-sm"
            style={{ background: C.surface, border: `1px solid ${C.surfaceBdr}` }}>
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: C.text }}>Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="katochaneesh@gmail.com" autoFocus
                className="w-full px-4 py-2.5 rounded-xl text-sm outline-none border"
                style={{ borderColor: C.surfaceBdr, color: C.text }} />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1" style={{ color: C.text }}>Password</label>
              <input type="password" value={password} onChange={e => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full px-4 py-2.5 rounded-xl text-sm outline-none border"
                style={{ borderColor: C.surfaceBdr, color: C.text }} />
            </div>
            {loginErr && <p className="text-sm" style={{ color: C.errText }}>{loginErr}</p>}
            <button type="submit" disabled={loggingIn}
              className="w-full py-2.5 rounded-xl font-bold text-sm"
              style={{ background: "linear-gradient(135deg,#7c6cfc,#9d8dfd)", color: "white" }}>
              {loggingIn ? "Signing in…" : "Sign In"}
            </button>
          </form>
        </div>
      </div>
    );
  }

  // ── Admin dashboard ─────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen" style={{ background: C.bg }}>
      {/* Header */}
      <header className="px-6 py-4 border-b flex items-center justify-between"
        style={{ background: C.header, borderColor: C.surfaceBdr }}>
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg flex items-center justify-center"
            style={{ background: "linear-gradient(135deg,#7c6cfc,#9d8dfd)" }}>
            <span className="text-white text-sm">⚙</span>
          </div>
          <div>
            <span className="font-bold" style={{ color: C.text }}>Lumen Admin</span>
            <span className="ml-2 text-xs" style={{ color: C.muted }}>{ADMIN_EMAIL}</span>
          </div>
        </div>
        <button onClick={handleSignOut} className="text-sm px-4 py-1.5 rounded-lg border"
          style={{ borderColor: C.surfaceBdr, color: C.muted }}>
          Sign Out
        </button>
      </header>

      <div className="flex">
        {/* Sidebar */}
        <nav className="w-52 shrink-0 border-r min-h-screen py-4"
          style={{ background: C.surface, borderColor: C.surfaceBdr }}>
          {([
            { id: "providers", label: "Providers",   icon: "🔌" },
            { id: "storage",   label: "Storage",     icon: "💾" },
            { id: "kb",        label: "KB Access",   icon: "🔑" },
          ] as const).map(item => (
            <button key={item.id} onClick={() => setTab(item.id)}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-sm font-medium transition-all"
              style={{
                background: tab === item.id ? "rgba(124,108,252,0.10)" : "transparent",
                borderLeft: tab === item.id ? `2px solid ${C.accent}` : "2px solid transparent",
                color: tab === item.id ? C.accent : C.text,
              }}>
              <span>{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>

        {/* Main content */}
        <main className="flex-1 p-8">
          <h2 className="text-xl font-bold mb-6" style={{ color: C.text }}>
            {tab === "providers" ? "Provider Settings"
             : tab === "storage"  ? "Storage Limits"
             : "Knowledge Base Access"}
          </h2>

          {config && tab === "providers" && <ProvidersTab config={config} onSave={saveConfig} />}
          {config && tab === "storage"   && <StorageTab   config={config} onSave={saveConfig} />}
          {tab === "kb" && <KbAccessTab />}
        </main>
      </div>
    </div>
  );
}
