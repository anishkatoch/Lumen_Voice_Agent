"use client";
import { useState, useEffect, useCallback } from "react";
import {
  Settings, Plug, HardDrive, KeyRound, LogOut, Check, X, RefreshCw,
  Eye, EyeOff, Loader2,
} from "lucide-react";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8004";
const ADMIN_EMAIL = "katochaneesh@gmail.com";

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

type Tab = "providers" | "storage" | "kb";
const NAV_ITEMS: { id: Tab; label: string; icon: typeof Plug }[] = [
  { id: "providers", label: "Providers", icon: Plug },
  { id: "storage",   label: "Storage",   icon: HardDrive },
  { id: "kb",        label: "KB Access", icon: KeyRound },
];

// ── Sub-components ────────────────────────────────────────────────────────────
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card" style={{ padding: "20px 22px", marginBottom: 18 }}>
      <h3 style={{
        fontSize: 14.5, fontWeight: 700, color: "var(--ink)",
        marginBottom: 16, paddingBottom: 12, borderBottom: "1px solid var(--border)",
      }}>{title}</h3>
      {children}
    </div>
  );
}

function RadioGroup({ label, value, options, onChange }: {
  label: string; value: string; options: { value: string; label: string }[];
  onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <div className="chip-row" style={{ marginBottom: 0 }}>
        {options.map(o => (
          <button key={o.value} type="button" onClick={() => onChange(o.value)}
            className="chip-btn"
            style={value === o.value ? { background: "var(--accent)", borderColor: "var(--accent)", color: "var(--accent-ink)" } : undefined}>
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
    <div className="field">
      <label>{label}</label>
      <div style={{ position: "relative" }}>
        <input
          type={isPassword && !show ? "password" : "text"}
          value={value}
          onChange={e => onChange(e.target.value)}
          placeholder={placeholder}
          className="input"
          style={isPassword ? { paddingRight: 42 } : undefined}
        />
        {isPassword && (
          <button type="button" onClick={() => setShow(s => !s)}
            aria-label={show ? "Hide value" : "Show value"}
            style={{
              position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)",
              background: "none", border: "none", color: "var(--ink-faint)", display: "flex", padding: 2,
            }}>
            {show ? <EyeOff size={15} /> : <Eye size={15} />}
          </button>
        )}
      </div>
      {hint && <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 5, lineHeight: 1.5 }}>{hint}</p>}
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <p style={{
      fontSize: 12.5, lineHeight: 1.55, padding: "9px 12px", borderRadius: "var(--r-md)",
      background: "var(--warning-soft)", color: "var(--warning)",
    }}>{children}</p>
  );
}

function SaveButton({ saving, saved, onClick, label }: { saving: boolean; saved: boolean; onClick: () => void; label: string }) {
  return (
    <button onClick={onClick} disabled={saving} className={`btn ${saved ? "btn-success" : "btn-primary"}`} style={{ marginTop: 4 }}>
      {saving ? <Loader2 size={15} style={{ animation: "lc-spin .7s linear infinite" }} /> : saved ? <Check size={15} /> : null}
      {saving ? "Saving…" : saved ? "Saved" : label}
    </button>
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
    <div style={{ maxWidth: 640 }}>
      <Section title="Speech-to-Text (STT)">
        <RadioGroup label="STT provider" value={local.stt_provider} onChange={set("stt_provider")}
          options={[
            { value: "deepgram",       label: "Deepgram (default)" },
            { value: "openai_whisper", label: "OpenAI Whisper" },
          ]} />
        {local.stt_provider === "openai_whisper" && (
          <Notice>
            Whisper is batch-only (no streaming). Each utterance is sent to OpenAI after silence is detected.
            Uses the OpenAI API key configured under LLM.
          </Notice>
        )}
      </Section>

      <Section title="Text-to-Speech (TTS)">
        <RadioGroup label="TTS provider" value={local.tts_provider} onChange={set("tts_provider")}
          options={[
            { value: "elevenlabs", label: "ElevenLabs (default)" },
            { value: "openai_tts", label: "OpenAI TTS" },
          ]} />
        {local.tts_provider === "openai_tts" && (
          <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 4 }}>
            <RadioGroup label="Voice" value={local.tts_openai_voice} onChange={set("tts_openai_voice")}
              options={["alloy","echo","fable","onyx","nova","shimmer"].map(v => ({ value: v, label: v }))} />
            <RadioGroup label="Model" value={local.tts_openai_model} onChange={set("tts_openai_model")}
              options={[{ value: "tts-1", label: "tts-1 (fast)" }, { value: "tts-1-hd", label: "tts-1-hd (quality)" }]} />
            <Notice>OpenAI TTS outputs 24 kHz PCM; it is automatically resampled to 16 kHz.</Notice>
          </div>
        )}
      </Section>

      <Section title="Language Model (LLM)">
        <RadioGroup label="LLM provider" value={local.llm_provider} onChange={set("llm_provider")}
          options={[
            { value: "openai",    label: "OpenAI (default)" },
            { value: "groq",      label: "Groq" },
            { value: "anthropic", label: "Anthropic" },
            { value: "ollama",    label: "Ollama (local)" },
          ]} />

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
          <Field label="API key" value={local.llm_api_key} onChange={set("llm_api_key")}
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
          <Field label="API key override" value={local.llm_api_key} onChange={set("llm_api_key")}
            placeholder="sk-... (leave empty to use OPENAI_API_KEY env var)"
            type="password" />
        )}

        {local.llm_provider === "ollama" && (
          <Field label="Ollama base URL" value={local.llm_base_url} onChange={set("llm_base_url")}
            placeholder="http://localhost:11434"
            hint="Default: http://localhost:11434. Change if Ollama runs on another host/port." />
        )}
      </Section>

      {err && <p style={{ color: "var(--danger)", fontSize: 13, marginBottom: 12 }}>{err}</p>}
      <SaveButton saving={saving} saved={saved} onClick={handleSave} label="Save provider settings" />
    </div>
  );
}

// ── Storage Tab ───────────────────────────────────────────────────────────────
function StorageField({ label, hint, value, onChange }: {
  label: string; hint: string; value: string; onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <p style={{ fontSize: 12.5, color: "var(--ink-muted)", marginTop: -2, marginBottom: 10, lineHeight: 1.5 }}>{hint}</p>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <input type="number" min="1" max="500" value={value} onChange={e => onChange(e.target.value)}
          className="input" style={{ width: 96 }} />
        <span style={{ fontSize: 13.5, color: "var(--ink-muted)" }}>MB</span>
        <span style={{ fontSize: 12, color: "var(--ink-faint)", fontFamily: "var(--font-mono)" }}>
          ({(parseInt(value || "5") * 1024 * 1024).toLocaleString()} bytes)
        </span>
      </div>
    </div>
  );
}

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
    <div style={{ maxWidth: 560 }}>
      <Section title="Knowledge base storage limits">
        <StorageField label="User KB limit (MB per user)"
          hint="Max total document storage each user can upload to their personal Knowledge Base."
          value={userMb} onChange={setUserMb} />
        <StorageField label="Agent documents limit (MB per agent)"
          hint="Max total document storage per agent (personal + global docs combined)."
          value={agentMb} onChange={setAgentMb} />

        {err && <p style={{ color: "var(--danger)", fontSize: 13, marginTop: 4, marginBottom: 12 }}>{err}</p>}
        <SaveButton saving={saving} saved={saved} onClick={handleSave} label="Save storage limits" />
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
    <div style={{ maxWidth: 760 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 18 }}>
        <div className="segmented">
          {(["requests","users"] as const).map(t => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {t === "requests" ? `Access requests${pending.length ? ` (${pending.length})` : ""}` : "Approved users"}
            </button>
          ))}
        </div>
        <button className="icon-btn" aria-label="Refresh" onClick={load}>
          <RefreshCw size={15} />
        </button>
      </div>

      {loading ? (
        <p style={{ fontSize: 13.5, textAlign: "center", padding: "32px 0", color: "var(--ink-muted)" }}>Loading…</p>
      ) : tab === "requests" ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {pending.length === 0 && resolved.length === 0 && (
            <div className="empty"><p>No requests yet.</p></div>
          )}
          {pending.map(r => (
            <div key={r.id} className="card" style={{ padding: "14px 16px", borderColor: "var(--warning)", background: "var(--warning-soft)" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
                <div>
                  <p style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{r.user_email}</p>
                  <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>{new Date(r.created_at).toLocaleString()}</p>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn btn-success btn-sm"
                    onClick={async () => { await api(`/api/admin/kb-requests/${r.id}/approve`, { method: "POST" }); load(); }}>
                    <Check size={13} /> Approve
                  </button>
                  <button className="btn btn-danger btn-sm"
                    onClick={async () => { await api(`/api/admin/kb-requests/${r.id}/reject`, { method: "POST" }); load(); }}>
                    <X size={13} /> Reject
                  </button>
                </div>
              </div>
            </div>
          ))}
          {resolved.map(r => (
            <div key={r.id} className="card" style={{ padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <p style={{ fontSize: 13.5, color: "var(--ink)" }}>{r.user_email}</p>
              <span className={`pill ${r.status === "approved" ? "pill-success" : "pill-danger"}`}>
                {r.status.charAt(0).toUpperCase() + r.status.slice(1)}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {users.length === 0 && <div className="empty"><p>No users with Global KB access.</p></div>}
          {users.map(u => (
            <div key={u.user_id} className="card" style={{ padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
              <div>
                <p style={{ fontSize: 13, fontFamily: "var(--font-mono)", color: "var(--ink)" }}>{u.user_id.slice(0, 16)}…</p>
                {u.granted_at && <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>Granted: {new Date(u.granted_at).toLocaleDateString()}</p>}
              </div>
              <button className="btn btn-danger btn-sm"
                onClick={async () => { await api(`/api/admin/users/${u.user_id}/revoke-kb`, { method: "POST" }); load(); }}>
                Revoke
              </button>
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
  const [showPwd,   setShowPwd]   = useState(false);
  const [loginErr,  setLoginErr]  = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [config,    setConfig]    = useState<AdminConfig | null>(null);
  const [tab, setTab] = useState<Tab>("providers");

  // Check existing token on mount
  useEffect(() => {
    const token = getToken();
    if (!token) { setCheckDone(true); return; }
    api<AdminConfig>("/api/admin/config")
      .then(cfg => { setConfig(cfg); setLoggedIn(true); })
      .catch(() => { /* not admin or expired */ })
      .finally(() => setCheckDone(true));
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
      <div className="lumenc" style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
        <div style={{ width: "100%", maxWidth: 380 }}>
          <div style={{ textAlign: "center", marginBottom: 28 }}>
            <div className="brand-mark" style={{ margin: "0 auto 16px" }}>
              <Settings size={18} />
            </div>
            <h1 className="view-title" style={{ fontSize: 24 }}>Admin panel</h1>
            <p className="view-sub" style={{ margin: "6px auto 0", textAlign: "center" }}>Sign in with your admin account</p>
          </div>

          <form onSubmit={handleLogin} className="card" style={{ padding: 22, display: "flex", flexDirection: "column", gap: 14 }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Email</label>
              <input type="email" value={email} onChange={e => setEmail(e.target.value)}
                placeholder="you@company.com" autoFocus className="input" />
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>Password</label>
              <div style={{ position: "relative" }}>
                <input type={showPwd ? "text" : "password"} value={password} onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••" className="input" style={{ paddingRight: 42 }} />
                <button type="button" onClick={() => setShowPwd(v => !v)} aria-label={showPwd ? "Hide password" : "Show password"}
                  style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: "var(--ink-faint)", display: "flex", padding: 2 }}>
                  {showPwd ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>
            {loginErr && <p style={{ color: "var(--danger)", fontSize: 13 }}>{loginErr}</p>}
            <button type="submit" disabled={loggingIn} className="btn btn-primary btn-block">
              {loggingIn ? <Loader2 size={15} style={{ animation: "lc-spin .7s linear infinite" }} /> : null}
              {loggingIn ? "Signing in…" : "Sign in"}
            </button>
          </form>
        </div>
      </div>
    );
  }

  const tabMeta: Record<Tab, { title: string; sub: string }> = {
    providers: { title: "Provider settings", sub: "Choose which speech and language providers power every agent." },
    storage:   { title: "Storage limits",     sub: "Set how much document storage users and agents can use." },
    kb:        { title: "Knowledge base access", sub: "Review requests and manage who can use the global Knowledge Base." },
  };

  // ── Admin dashboard ─────────────────────────────────────────────────────────
  return (
    <div className="lumenc app-shell">
      <aside className="rail">
        <div className="rail-brand">
          <div className="brand-mark"><Settings size={18} /></div>
          <div>
            <div className="brand-word">Lumen</div>
            <div className="brand-tag">Admin</div>
          </div>
        </div>
        <nav className="rail-nav">
          {NAV_ITEMS.map(item => (
            <button key={item.id} className={`nav-item${tab === item.id ? " active" : ""}`} onClick={() => setTab(item.id)}>
              <item.icon className="icon" size={17} strokeWidth={1.8} />
              {item.label}
            </button>
          ))}
        </nav>
        <div className="rail-foot">
          <div className="account-row">
            <span className="av">{ADMIN_EMAIL[0]?.toUpperCase()}</span>
            <span className="meta">
              <span className="name">Admin</span>
              <span className="mail">{ADMIN_EMAIL}</span>
            </span>
          </div>
          <button className="btn btn-ghost btn-sm btn-block" onClick={handleSignOut}>
            <LogOut size={14} /> Sign out
          </button>
        </div>
      </aside>

      <nav className="tabbar">
        {NAV_ITEMS.map(item => (
          <button key={item.id} className={`tab-item${tab === item.id ? " active" : ""}`} onClick={() => setTab(item.id)}>
            <item.icon className="icon" size={19} strokeWidth={1.8} />
            {item.label}
          </button>
        ))}
      </nav>

      <div className="main">
        <div className="topbar">
          <span className="crumb">Admin / {NAV_ITEMS.find(n => n.id === tab)?.label}</span>
          <div className="topbar-spacer" />
        </div>
        <div className="view-scroll">
          <div className="view">
            <div className="view-head">
              <div>
                <h1 className="view-title" style={{ fontSize: 24 }}>{tabMeta[tab].title}</h1>
                <p className="view-sub">{tabMeta[tab].sub}</p>
              </div>
            </div>

            {config && tab === "providers" && <ProvidersTab config={config} onSave={saveConfig} />}
            {config && tab === "storage"   && <StorageTab   config={config} onSave={saveConfig} />}
            {tab === "kb" && <KbAccessTab />}
          </div>
        </div>
      </div>
    </div>
  );
}
