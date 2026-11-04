"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { API, Agent, AgentFunction, FunctionParameter, PhoneNumber, TwilioAccount, PlivoPhoneNumber, PlivoAccount, HRCandidate, HRInterview } from "@/lib/api";
import { getUser, clearTokens } from "@/lib/auth";
import { useVoiceAgent } from "@/hooks/useVoiceAgent";

/* ---------------------------------------------------------------------------
   Helpers
--------------------------------------------------------------------------- */

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function formatBytes(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function isToday(dateStr: string): boolean {
  const d = new Date(dateStr);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
}

const AGENT_HUES = ["indigo", "teal", "rose"] as const;
type AgentHue = typeof AGENT_HUES[number];

function agentHue(id: string): AgentHue {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return AGENT_HUES[hash % AGENT_HUES.length];
}

function agentInitials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "A";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

function hrStatusPill(status: string): { cls: string; label: string } {
  if (status === "completed") return { cls: "pill-success", label: status };
  if (status === "failed") return { cls: "pill-danger", label: status };
  if (status === "cancelled") return { cls: "pill-neutral", label: status };
  if (status === "calling") return { cls: "pill-accent", label: status };
  return { cls: "pill-warning", label: status };
}

/* ---------------------------------------------------------------------------
   Icon sprite (inline, mirrors the Lumen Console design)
--------------------------------------------------------------------------- */

function IconSprite() {
  return (
    <svg style={{ display: "none" }} aria-hidden="true">
      <defs>
        <symbol id="i-grid" viewBox="0 0 24 24"><rect x="3" y="3" width="7.5" height="7.5" rx="1.8" /><rect x="13.5" y="3" width="7.5" height="7.5" rx="1.8" /><rect x="3" y="13.5" width="7.5" height="7.5" rx="1.8" /><rect x="13.5" y="13.5" width="7.5" height="7.5" rx="1.8" /></symbol>
        <symbol id="i-book" viewBox="0 0 24 24"><path d="M4 5.2C4 3.99 4.99 3 6.2 3H12v17H6.2A2.2 2.2 0 0 0 4 22.2" /><path d="M20 5.2C20 3.99 19.01 3 17.8 3H12v17h5.8a2.2 2.2 0 0 1 2.2 2.2" /></symbol>
        <symbol id="i-activity" viewBox="0 0 24 24"><path d="M3 12h4l2.3-7L13.6 19 16 12h5" /></symbol>
        <symbol id="i-phone" viewBox="0 0 24 24"><path d="M6.6 10.8c1.4 2.8 3.8 5.2 6.6 6.6l2.2-2.2c.3-.3.7-.4 1.1-.3 1.2.4 2.5.6 3.8.6.6 0 1 .4 1 1V20.3c0 .6-.4 1-1 1C10.6 21.3 2.7 13.4 2.7 3.7c0-.6.4-1 1-1H7.2c.6 0 1 .4 1 1 0 1.3.2 2.6.6 3.8.1.4 0 .8-.3 1.1L6.6 10.8Z" /></symbol>
        <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3" /></symbol>
        <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14" /></symbol>
        <symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4.3" /><path d="M12 2.5v2.4M12 19.1v2.4M4.6 4.6l1.7 1.7M17.7 17.7l1.7 1.7M2.5 12h2.4M19.1 12h2.4M4.6 19.4l1.7-1.7M17.7 6.3l1.7-1.7" /></symbol>
        <symbol id="i-moon" viewBox="0 0 24 24"><path d="M20 14.2A8.5 8.5 0 1 1 9.8 4a7 7 0 0 0 10.2 10.2Z" /></symbol>
        <symbol id="i-chevron" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6" /></symbol>
        <symbol id="i-arrow-left" viewBox="0 0 24 24"><path d="M19 12H5M11 5l-6 7 6 7" /></symbol>
        <symbol id="i-trash" viewBox="0 0 24 24"><path d="M4 7h16" /><path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /><path d="M6.2 7l.9 13a1 1 0 0 0 1 .9h7.8a1 1 0 0 0 1-.9l.9-13" /><path d="M10 11v6M14 11v6" /></symbol>
        <symbol id="i-upload" viewBox="0 0 24 24"><path d="M7 18a4.5 4.5 0 0 1-.6-8.96A5.5 5.5 0 0 1 17.4 8.8 4 4 0 0 1 17 17" /><path d="M12 11v7M9 14l3-3 3 3" /></symbol>
        <symbol id="i-mic" viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0" /><path d="M12 18v3M9 21h6" /></symbol>
        <symbol id="i-copy" viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2.2" /><path d="M15 9V6.2A2.2 2.2 0 0 0 12.8 4H6.2A2.2 2.2 0 0 0 4 6.2v6.6A2.2 2.2 0 0 0 6.2 15H9" /></symbol>
        <symbol id="i-check-circle" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M8.2 12.3l2.5 2.4 5-5.3" /></symbol>
        <symbol id="i-x" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" /></symbol>
        <symbol id="i-logout" viewBox="0 0 24 24"><path d="M9 4H6.2A2.2 2.2 0 0 0 4 6.2v11.6A2.2 2.2 0 0 0 6.2 20H9" /><path d="M15 16l4-4-4-4" /><path d="M19 12H9" /></symbol>
        <symbol id="i-zap" viewBox="0 0 24 24"><path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" /></symbol>
        <symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" /><path d="M12 7.2v5l3.4 2" /></symbol>
        <symbol id="i-headphones" viewBox="0 0 24 24"><path d="M4 13.5v-1.8a8 8 0 0 1 16 0v1.8" /><rect x="2.6" y="13.2" width="5.1" height="7.2" rx="2.1" /><rect x="16.3" y="13.2" width="5.1" height="7.2" rx="2.1" /></symbol>
        <symbol id="i-file" viewBox="0 0 24 24"><path d="M7 3h7l5 5v13H7Z" /><path d="M14 3v5h5" /><path d="M9.3 13h5.4M9.3 16.6h5.4" /></symbol>
        <symbol id="i-more" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.7" /><circle cx="12" cy="12" r="1.7" /><circle cx="19" cy="12" r="1.7" /></symbol>
        <symbol id="i-edit" viewBox="0 0 24 24"><path d="M4 20h4L18.4 9.6a2.1 2.1 0 0 0-3-3L5 17v3Z" /><path d="M13.8 6.8l3.4 3.4" /></symbol>
        <symbol id="i-calendar" viewBox="0 0 24 24"><rect x="3.5" y="5" width="17" height="16" rx="2.2" /><path d="M8 3v4M16 3v4M3.5 10h17" /></symbol>
      </defs>
    </svg>
  );
}

function Icon({ name, className, size, style }: { name: string; className?: string; size?: number; style?: React.CSSProperties }) {
  return (
    <svg className={className ? `icon ${className}` : "icon"} style={{ ...(size ? { width: size, height: size } : null), ...style }}>
      <use href={`#i-${name}`} />
    </svg>
  );
}

function Spinner() {
  return (
    <span style={{
      width: 15, height: 15, borderRadius: "50%", display: "inline-block",
      border: "2px solid currentColor", borderTopColor: "transparent",
      animation: "lc-spin .7s linear infinite",
    }} />
  );
}

/* ---------------------------------------------------------------------------
   Toast notifications
--------------------------------------------------------------------------- */

type ToastKind = "success" | "error";
interface ToastMsg { id: number; text: string; kind: ToastKind; }
let toastSeq = 0;
let toastListeners: Array<(t: ToastMsg) => void> = [];
function pushToast(text: string, kind: ToastKind = "success") {
  const msg: ToastMsg = { id: ++toastSeq, text, kind };
  toastListeners.forEach(fn => fn(msg));
}
function ToastHost() {
  const [toasts, setToasts] = useState<ToastMsg[]>([]);
  useEffect(() => {
    const fn = (t: ToastMsg) => {
      setToasts(ts => [...ts, t]);
      setTimeout(() => setToasts(ts => ts.filter(x => x.id !== t.id)), 2600);
    };
    toastListeners.push(fn);
    return () => { toastListeners = toastListeners.filter(l => l !== fn); };
  }, []);
  if (toasts.length === 0) return null;
  return (
    <div className="toast-stack">
      {toasts.map(t => (
        <div key={t.id} className={`toast${t.kind === "error" ? " is-error" : ""}`}>
          <Icon name={t.kind === "error" ? "x" : "check-circle"} />
          <span>{t.text}</span>
        </div>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Theme (light / dark), persisted, scoped to this console only
--------------------------------------------------------------------------- */

function useConsoleMode() {
  const [mode, setMode] = useState<"" | "light" | "dark">("");
  useEffect(() => {
    try {
      const saved = localStorage.getItem("lumen_console_mode");
      if (saved === "light" || saved === "dark") setMode(saved);
    } catch { /* ignore */ }
  }, []);
  const isDark = mode === "dark" || (mode === "" && typeof window !== "undefined" && !!window.matchMedia?.("(prefers-color-scheme: dark)").matches);
  const toggle = useCallback(() => {
    const next = isDark ? "light" : "dark";
    try { localStorage.setItem("lumen_console_mode", next); } catch { /* ignore */ }
    setMode(next);
  }, [isDark]);
  return { mode, isDark, toggle };
}

/* ---------------------------------------------------------------------------
   Navigation shell: Rail (desktop), TabBar (mobile), Topbar, Account modal
--------------------------------------------------------------------------- */

type View = "agents" | "knowledge-base" | "sessions" | "phone-numbers";

const NAV_ITEMS: { id: View; label: string; shortLabel: string; icon: string }[] = [
  { id: "agents", label: "Agents", shortLabel: "Agents", icon: "grid" },
  { id: "knowledge-base", label: "Knowledge Base", shortLabel: "Docs", icon: "book" },
  { id: "sessions", label: "Call Analytics", shortLabel: "Calls", icon: "activity" },
  { id: "phone-numbers", label: "Phone Numbers", shortLabel: "Numbers", icon: "phone" },
];

function Rail({ view, onView, agentCount, userEmail, userName, onOpenAccount }: {
  view: View; onView: (v: View) => void; agentCount: number;
  userEmail: string; userName: string; onOpenAccount: () => void;
}) {
  return (
    <aside className="rail">
      <div className="rail-brand">
        <div className="brand-mark"><Icon name="mic" /></div>
        <div>
          <div className="brand-word">Lumen</div>
          <div className="brand-tag">Voice console</div>
        </div>
      </div>
      <nav className="rail-nav">
        {NAV_ITEMS.map(item => (
          <button key={item.id} className={`nav-item${view === item.id ? " active" : ""}`} onClick={() => onView(item.id)}>
            <Icon name={item.icon} />
            {item.label}
            {item.id === "agents" && <span className="nav-count">{agentCount}</span>}
          </button>
        ))}
      </nav>
      <div className="rail-foot">
        <button className="trial-chip" onClick={() => pushToast("You're on the free trial")}>
          <Icon name="zap" />
          <span>Free trial<span className="sub">Upgrade anytime</span></span>
        </button>
        <button className="account-row" onClick={onOpenAccount}>
          <span className="av">{userEmail[0]?.toUpperCase() ?? "U"}</span>
          <span className="meta">
            <span className="name">{userName || "Account"}</span>
            <span className="mail">{userEmail}</span>
          </span>
        </button>
      </div>
    </aside>
  );
}

function TabBar({ view, onView, onMore }: { view: View; onView: (v: View) => void; onMore: () => void }) {
  return (
    <nav className="tabbar">
      {NAV_ITEMS.map(item => (
        <button key={item.id} className={`tab-item${view === item.id ? " active" : ""}`} onClick={() => onView(item.id)}>
          <Icon name={item.icon} />
          {item.shortLabel}
        </button>
      ))}
      <button className="tab-item" onClick={onMore}>
        <Icon name="more" />
        More
      </button>
    </nav>
  );
}

function Topbar({ crumb, search, isDark, onToggleTheme, userEmail, onOpenAccount }: {
  crumb: string;
  search?: { value: string; onChange: (v: string) => void; placeholder?: string };
  isDark: boolean; onToggleTheme: () => void; userEmail: string; onOpenAccount: () => void;
}) {
  return (
    <div className="topbar">
      <span className="crumb">{crumb}</span>
      {search && (
        <div className="search">
          <Icon name="search" />
          <input value={search.value} onChange={e => search.onChange(e.target.value)} placeholder={search.placeholder ?? "Search"} />
        </div>
      )}
      <div className="topbar-spacer" />
      <div className="topbar-actions">
        <button className="icon-btn" aria-label="Toggle theme" onClick={onToggleTheme}>
          <Icon name={isDark ? "sun" : "moon"} />
        </button>
        <button className="topbar-av" onClick={onOpenAccount}>{userEmail[0]?.toUpperCase() ?? "U"}</button>
      </div>
    </div>
  );
}

function AccountModal({ onClose, userName, userEmail, onSignOut }: {
  onClose: () => void; userName: string; userEmail: string; onSignOut: () => void;
}) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" style={{ width: 300 }} onClick={e => e.stopPropagation()}>
        <div className="modal-body" style={{ paddingTop: 20 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 11, marginBottom: 14 }}>
            <span className="avatar avatar-sm hue-indigo" style={{ width: 38, height: 38, borderRadius: "50%" }}>
              {userEmail[0]?.toUpperCase() ?? "U"}
            </span>
            <div>
              <div style={{ fontWeight: 700, fontSize: 13.5 }}>{userName || "Account"}</div>
              <div style={{ fontSize: 11.5, color: "var(--ink-muted)" }}>{userEmail}</div>
            </div>
          </div>
          <div className="pill pill-neutral" style={{ marginBottom: 12 }}>
            <Icon name="zap" size={11} /> Free trial
          </div>
          <button className="btn btn-outline btn-block" style={{ marginBottom: 8 }}
            onClick={() => {
              if (typeof navigator !== "undefined" && navigator.clipboard) {
                navigator.clipboard.writeText(userEmail).then(() => pushToast("Email copied")).catch(() => pushToast("Could not copy email", "error"));
              }
            }}>
            <Icon name="copy" /> Copy email
          </button>
          <button className="btn btn-ghost btn-block" onClick={onSignOut}>
            <Icon name="logout" /> Sign out
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Agent card
--------------------------------------------------------------------------- */

function AgentCard({ agent, onClick, onToggle }: {
  agent: Agent; onClick: () => void; onToggle: (active: boolean) => Promise<void>;
}) {
  const [toggling, setToggling] = useState(false);
  const hue = agentHue(agent.id);
  const hueVar = hue === "indigo" ? "accent" : hue;

  return (
    <div className="agent-card" onClick={onClick}>
      <div className="agent-card-top">
        <div className={`avatar hue-${hue}`}>{agentInitials(agent.name)}</div>
        <label className="switch" onClick={e => e.stopPropagation()}>
          <input type="checkbox" checked={agent.is_active} disabled={toggling}
            onChange={async e => { setToggling(true); try { await onToggle(e.target.checked); } finally { setToggling(false); } }} />
          <span className="track"><span className="thumb" /></span>
        </label>
      </div>
      <div>
        <div className="agent-name">{agent.name}</div>
        <div className="agent-type">{agent.description || "Voice agent"}</div>
      </div>
      <div className="agent-meta">
        <div>
          <div className="agent-edited">Edited {timeAgo(agent.updated_at)}</div>
          {agent.is_active
            ? <span className="pill pill-success" style={{ marginTop: 4 }}><span className="dot live" />Live</span>
            : <span className="pill pill-neutral" style={{ marginTop: 4 }}>Paused</span>}
        </div>
        <div className="agent-bars">
          {[6, 11, 8, 14, 5].map((h, i) => (
            <span key={i} style={{ height: h, background: agent.is_active ? `var(--${hueVar})` : "var(--border-strong)" }} />
          ))}
        </div>
      </div>
    </div>
  );
}

function CreateCard({ onClick }: { onClick: () => void }) {
  return (
    <button className="new-agent-card" onClick={onClick}>
      <div className="plus-ring"><Icon name="plus" /></div>
      Create a new agent
    </button>
  );
}

/* ---------------------------------------------------------------------------
   Default system prompt - every new agent starts from this (any role),
   and it's shown instead of a blank textarea for existing agents that
   don't have a system_prompt yet. Mirrors app/llm_service.py's
   _DEFAULT_SYSTEM on the backend, so what's shown here is also what
   actually runs if an agent is ever left without a saved prompt.
--------------------------------------------------------------------------- */

const DEFAULT_SYSTEM_PROMPT = `# Identity & Role
You are a helpful AI voice agent. You assist callers with their questions, tasks, and requests. Be empathetic, professional, and concise. Confirm important details before finalizing anything. Respond naturally for a voice conversation - no bullet points or markdown.

# Current Date & Time
Today is {{current_time}}. Use this as the only source of truth for "today" - never your training data.

## Voice & Persona

### Personality
**Friendly & Patient:** Sound warm, patient, and knowledgeable.
**Concise & Direct:** **This is a strict rule.** Always get straight to the point. Avoid filler words and long explanations. Your goal is to resolve the query in as few turns as possible.
**Natural & Conversational:** Use a human-like, conversational tone (e.g., "I'm," "don't," "it's").
**Confident:** Speak with confidence, but if you don't know something, say "Hmm, let me check on that for you."

### Speech Characteristics
Use contractions (I'm, we'll, don't).
**Keep responses short,** ideally under 30 words.
Ask only one question at a time.

### Tone
Friendly
Professional
Clear and concise
Confident, not pushy

# Purpose

# Knowledge Base`;

/* ---------------------------------------------------------------------------
   Create Agent modal
--------------------------------------------------------------------------- */

function CreateAgentModal({ onClose, onCreate }: { onClose: () => void; onCreate: (agent: Agent) => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    if (!name.trim()) { setErr("Name is required"); return; }
    setLoading(true); setErr("");
    try {
      const agent = await API.createAgent({
        name: name.trim(), description: desc.trim() || undefined,
        system_prompt: DEFAULT_SYSTEM_PROMPT,
      });
      pushToast(`${agent.name} created`);
      onCreate(agent);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to create agent");
    } finally { setLoading(false); }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-head"><h3>New agent</h3><button className="icon-btn" onClick={onClose}><Icon name="x" /></button></div>
        <div className="modal-body">
          <div className="field">
            <label>Agent name</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)}
              placeholder="e.g. Front Desk" onKeyDown={e => e.key === "Enter" && submit()} />
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Description (optional)</label>
            <input className="input" value={desc} onChange={e => setDesc(e.target.value)} placeholder="Brief description" />
          </div>
          {err && <div style={{ color: "var(--danger)", fontSize: 13, marginTop: 10 }}>{err}</div>}
        </div>
        <div className="modal-foot">
          <button className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={submit} disabled={loading}>
            <Icon name="plus" />{loading ? "Creating..." : "Create agent"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Function form (inline, used inside Agent Detail)
--------------------------------------------------------------------------- */

function FunctionForm({ agentId, initial, onSave, onCancel }: {
  agentId: string; initial?: AgentFunction; onSave: (fn: AgentFunction) => void; onCancel: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [trigger, setTrigger] = useState(initial?.trigger_type ?? "llm_tool_call");
  const [desc, setDesc] = useState(initial?.description ?? "");
  const [method, setMethod] = useState(initial?.method ?? "POST");
  const [url, setUrl] = useState(initial?.url ?? "");
  const [params, setParams] = useState<FunctionParameter[]>(initial?.parameters ?? []);
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>(
    Object.entries(initial?.headers ?? {}).map(([key, value]) => ({ key, value }))
  );
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const addParam = () => setParams(p => [...p, { name: "", type: "string", description: "" }]);
  const removeParam = (i: number) => setParams(p => p.filter((_, idx) => idx !== i));
  const updateParam = (i: number, field: keyof FunctionParameter, val: string) =>
    setParams(p => p.map((x, idx) => idx === i ? { ...x, [field]: val } : x));

  const addHeader = () => setHeaders(h => [...h, { key: "", value: "" }]);
  const removeHeader = (i: number) => setHeaders(h => h.filter((_, idx) => idx !== i));
  const updateHeader = (i: number, field: "key" | "value", val: string) =>
    setHeaders(h => h.map((x, idx) => idx === i ? { ...x, [field]: val } : x));

  const save = async () => {
    if (!name.trim()) { setErr("Function name is required"); return; }
    setLoading(true); setErr("");
    try {
      const headersObj = Object.fromEntries(headers.filter(h => h.key.trim()).map(h => [h.key.trim(), h.value]));
      const payload = {
        name: name.trim(), description: desc.trim() || undefined,
        method, url: url.trim(), timeout_ms: 120000,
        headers: headersObj, query_params: {} as Record<string, string>,
        body_schema: undefined as string | undefined, payload_args_only: false,
        parameters: params.filter(p => p.name.trim()),
        trigger_type: trigger,
      };
      const fn = initial
        ? await API.updateAgentFunction(agentId, initial.id, payload)
        : await API.createAgentFunction(agentId, payload);
      pushToast(initial ? "Function updated" : "Function added");
      onSave(fn);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally { setLoading(false); }
  };

  return (
    <div className="card" style={{ padding: 16 }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Function name *</label>
          <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="checkAvailability" />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>Trigger</label>
          <select className="select" value={trigger} onChange={e => setTrigger(e.target.value)}>
            <option value="llm_tool_call">Agent decides (LLM tool call)</option>
            <option value="always">Always run</option>
            <option value="on_start">On conversation start</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label>Description</label>
        <textarea className="textarea" style={{ minHeight: 56 }} value={desc} onChange={e => setDesc(e.target.value)}
          placeholder="Describe what this function does and when to call it" rows={2} />
      </div>
      <div className="field">
        <label>Webhook URL</label>
        <div style={{ display: "flex", gap: 8 }}>
          <select className="select" style={{ width: 104, flexShrink: 0, fontWeight: 600 }} value={method} onChange={e => setMethod(e.target.value)}>
            <option value="GET">GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
            <option value="DELETE">DELETE</option>
          </select>
          <input className="input" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://your-api.com/endpoint" />
        </div>
        <div style={{ fontSize: 11, color: "var(--ink-faint)", marginTop: 6, lineHeight: 1.5 }}>
          A webhook is a URL your agent calls automatically when it needs external data, such as checking a calendar, looking up a customer record, or submitting a booking.
          {method === "GET" && " GET fetches data, no body sent."}
          {method === "POST" && " POST sends parameters in the request body."}
          {method === "PUT" && " PUT replaces a resource with the sent data."}
          {method === "PATCH" && " PATCH partially updates a resource."}
          {method === "DELETE" && " DELETE removes a resource."}
        </div>
      </div>
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <label style={{ fontSize: 11, color: "var(--ink-faint)" }}>Parameters</label>
          <button className="link-btn" onClick={addParam}>+ Add parameter</button>
        </div>
        {params.map((p, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 110px 1fr 28px", gap: 6, marginBottom: 6, alignItems: "center" }}>
            <input className="input" style={{ fontSize: 12, padding: "7px 10px" }} value={p.name} onChange={e => updateParam(i, "name", e.target.value)} placeholder="name" />
            <select className="select" style={{ fontSize: 12, padding: "7px 10px" }} value={p.type} onChange={e => updateParam(i, "type", e.target.value)}>
              {(["string", "number", "boolean", "object", "array"] as const).map(t => <option key={t}>{t}</option>)}
            </select>
            <input className="input" style={{ fontSize: 12, padding: "7px 10px" }} value={p.description} onChange={e => updateParam(i, "description", e.target.value)} placeholder="description" />
            <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={() => removeParam(i)}><Icon name="x" size={14} /></button>
          </div>
        ))}
      </div>
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <div>
            <label style={{ fontSize: 11, color: "var(--ink-faint)" }}>Headers</label>
            <span style={{ fontSize: 10, color: "var(--ink-faint)", marginLeft: 6 }}>optional, e.g. Authorization, API-Key</span>
          </div>
          <button className="link-btn" onClick={addHeader}>+ Add header</button>
        </div>
        {headers.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--ink-faint)", padding: "8px 0" }}>
            No headers added. Most APIs require at least an Authorization header.
          </div>
        )}
        {headers.map((h, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 28px", gap: 6, marginBottom: 6, alignItems: "center" }}>
            <input className="input" style={{ fontSize: 12, padding: "7px 10px" }} value={h.key} onChange={e => updateHeader(i, "key", e.target.value)} placeholder="Header name (e.g. Authorization)" />
            <input className="input" style={{ fontSize: 12, padding: "7px 10px" }} value={h.value} onChange={e => updateHeader(i, "value", e.target.value)} placeholder="Value (e.g. Bearer sk-...)" />
            <button className="icon-btn" style={{ width: 28, height: 28 }} onClick={() => removeHeader(i)}><Icon name="x" size={14} /></button>
          </div>
        ))}
      </div>
      {err && <div style={{ color: "var(--danger)", fontSize: 12, marginBottom: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={save} disabled={loading}>{loading ? "Saving..." : "Save function"}</button>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Collapsible section card
--------------------------------------------------------------------------- */

function Section({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div className={`section${collapsed ? " collapsed" : ""}`}>
      <div className="section-head" role="button" tabIndex={0} onClick={() => setCollapsed(c => !c)}>
        <h3>{title}</h3>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {extra}
          <Icon name="chevron" />
        </div>
      </div>
      {!collapsed && <div className="section-body">{children}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Live voice test dock
--------------------------------------------------------------------------- */

function VoiceDock({ agentId, agentName, docIds }: { agentId: string; agentName: string; docIds: string[] }) {
  const { state, connected, chat, error, connect, disconnect, startMic, stopMic, isMicActive } = useVoiceAgent();
  const orbRef = useRef<HTMLDivElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);

  const isIdle = !connected;
  const isPaused = connected && !isMicActive;

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chat]);

  useEffect(() => {
    const wrap = orbRef.current;
    if (!wrap) return;
    const n = 28, cx = 84, cy = 84, rIn = 32;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 168 168");
    svg.setAttribute("class", "orb-bars");
    svg.setAttribute("width", "168");
    svg.setAttribute("height", "168");
    for (let i = 0; i < n; i++) {
      const ang = (i / n) * Math.PI * 2;
      const len = 10 + (Math.sin(i * 1.9) + 1.2) * 9;
      const x1 = cx + Math.cos(ang) * rIn, y1 = cy + Math.sin(ang) * rIn;
      const x2 = cx + Math.cos(ang) * (rIn + len), y2 = cy + Math.sin(ang) * (rIn + len);
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(x1)); line.setAttribute("y1", String(y1));
      line.setAttribute("x2", String(x2)); line.setAttribute("y2", String(y2));
      line.setAttribute("stroke", "var(--rail-accent)");
      line.setAttribute("stroke-width", "3.4");
      line.setAttribute("stroke-linecap", "round");
      line.setAttribute("opacity", (0.35 + (i % 5) * 0.12).toFixed(2));
      line.style.transformOrigin = `${x1.toFixed(1)}px ${y1.toFixed(1)}px`;
      line.style.animation = `lc-orbPulse ${(1.1 + (i % 6) * 0.12).toFixed(2)}s ease-in-out ${(i * 0.04).toFixed(2)}s infinite`;
      line.style.animationPlayState = "paused";
      svg.appendChild(line);
    }
    wrap.innerHTML = "";
    wrap.appendChild(svg);
  }, []);

  useEffect(() => {
    const wrap = orbRef.current;
    if (!wrap) return;
    const active = state === "LISTENING" || state === "SPEAKING";
    wrap.querySelectorAll("line").forEach(l => { (l as SVGLineElement).style.animationPlayState = active ? "running" : "paused"; });
  }, [state]);

  const handleStart = useCallback(() => connect({ agentId, docIds: docIds.length > 0 ? docIds : undefined }), [connect, agentId, docIds]);

  const statusLabel = () => {
    if (state === "LISTENING") return "Listening...";
    if (state === "PROCESSING") return "Thinking...";
    if (state === "SPEAKING") return "Speaking...";
    if (isPaused) return "Paused";
    return "Connecting...";
  };

  return (
    <div className="dock">
      <div className="dock-top">
        <span className="dock-label">Audio <span className="test-tag">TEST</span></span>
        <span className="conn-status"><span className={`conn-dot${connected ? " on" : ""}`} />{connected ? "Connected" : "Not connected"}</span>
      </div>
      <div className="orb-area">
        <div className="orb-wrap" ref={orbRef}>
          <div className="orb-center"><Icon name="mic" /></div>
        </div>
        {isIdle ? (
          <>
            <div className="dock-talk">Talk to {agentName}</div>
            <div className="dock-sub">Test your prompt, functions, and knowledge live.</div>
            <span className="mic-chip"><Icon name="mic" size={13} />Mic ready</span>
          </>
        ) : (
          <div className="mic-chip" style={{ marginTop: 4 }}>{statusLabel()}</div>
        )}
      </div>

      {!isIdle && (
        <div className="dock-chat">
          {chat.length === 0 ? (
            <div style={{ textAlign: "center", color: "var(--rail-ink-muted)", fontSize: 12, marginTop: 20 }}>Waiting for conversation...</div>
          ) : (
            chat.map(entry => (
              <div key={entry.id} className={`bubble-row ${entry.role === "assistant" ? "agent" : "user"}`}>
                <span className="who">{entry.role === "assistant" ? agentName.toUpperCase() : "YOU"}</span>
                <div className="bubble">{entry.text}</div>
              </div>
            ))
          )}
          <div ref={chatEndRef} />
        </div>
      )}

      {error && <div className="dock-error">{error}</div>}

      {isIdle ? (
        <button className="talk-btn" onClick={handleStart}><Icon name="mic" />Start talking</button>
      ) : (
        <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
          {isPaused ? (
            <button className="talk-btn" style={{ marginTop: 0, flex: 1 }} onClick={startMic}><Icon name="mic" />Resume</button>
          ) : (
            <button className="talk-btn" style={{ marginTop: 0, flex: 1 }} onClick={stopMic}><Icon name="x" />Stop</button>
          )}
          <button className="talk-btn is-danger" style={{ marginTop: 0, flex: "none", width: "auto", padding: "13px 18px" }} onClick={disconnect}>End</button>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   HR section inside Agent Detail (candidates + scheduled calls)
--------------------------------------------------------------------------- */

function AgentHRSection({ agentId, hrInstructions, onHrInstructionsChange }: {
  agentId: string;
  hrInstructions: string;
  onHrInstructionsChange: (v: string) => void;
}) {
  const [candidates, setCandidates] = useState<HRCandidate[]>([]);
  const [interviews, setInterviews] = useState<HRInterview[]>([]);
  const [loading, setLoading] = useState(true);

  const [showCand, setShowCand] = useState(false);
  const [editCand, setEditCand] = useState<HRCandidate | null>(null);
  const [cForm, setCForm] = useState({ name: "", phone: "", email: "", role: "", notes: "", scheduled_at: "", call_lead_minutes: 1, specific_questions: "" });
  const [cResumeFile, setCResumeFile] = useState<File | null>(null);
  const [cSaving, setCSaving] = useState(false);
  const [cErr, setCErr] = useState("");

  const [showInt, setShowInt] = useState(false);
  const [editInt, setEditInt] = useState<HRInterview | null>(null);
  const [iForm, setIForm] = useState({ candidate_id: "", scheduled_at: "", call_lead_minutes: 1, specific_questions: "" });
  const [iSaving, setISaving] = useState(false);
  const [iErr, setIErr] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const [c, all] = await Promise.all([API.hrCandidates(), API.hrInterviews()]);
      setCandidates(c);
      setInterviews(all.filter((i: HRInterview) => i.agent_id === agentId));
    } catch { /* ignore */ }
    setLoading(false);
  };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [agentId]);

  const openAddCand = () => { setCForm({ name: "", phone: "", email: "", role: "", notes: "", scheduled_at: "", call_lead_minutes: 1, specific_questions: "" }); setCResumeFile(null); setCErr(""); setEditCand(null); setShowCand(true); };
  const openEditCand = (c: HRCandidate) => { setCForm({ name: c.name, phone: c.phone, email: c.email ?? "", role: c.role ?? "", notes: c.notes ?? "", scheduled_at: "", call_lead_minutes: 1, specific_questions: "" }); setCResumeFile(null); setCErr(""); setEditCand(c); setShowCand(true); };
  const saveCand = async () => {
    if (!cForm.name.trim() || !cForm.phone.trim()) { setCErr("Name and phone are required"); return; }
    setCSaving(true); setCErr("");
    try {
      const { scheduled_at, call_lead_minutes, specific_questions, ...candidateFields } = cForm;
      let saved: HRCandidate;
      if (editCand) {
        saved = await API.updateCandidate(editCand.id, candidateFields);
        setCandidates(cs => cs.map(c => c.id === saved.id ? saved : c));
      } else {
        saved = await API.createCandidate(candidateFields);
        setCandidates(cs => [...cs, saved]);
      }
      if (cResumeFile) {
        const withResume = await API.uploadCandidateResume(saved.id, cResumeFile);
        setCandidates(cs => cs.map(c => c.id === withResume.id ? withResume : c));
      }
      if (scheduled_at) {
        const interview = await API.createInterview({
          candidate_id: saved.id,
          agent_id: agentId,
          scheduled_at: new Date(scheduled_at).toISOString(),
          call_lead_minutes,
          specific_questions: specific_questions || undefined,
        });
        setInterviews(is => [...is, interview]);
      }
      setShowCand(false);
      pushToast(editCand ? "Candidate updated" : "Candidate added");
    } catch (e: unknown) { setCErr(e instanceof Error ? e.message : "Failed"); }
    setCSaving(false);
  };
  const deleteCand = async (id: string) => {
    if (!confirm("Delete this candidate?")) return;
    await API.deleteCandidate(id);
    setCandidates(cs => cs.filter(c => c.id !== id));
    pushToast("Candidate removed");
  };

  const openAddInt = (candidateId: string) => {
    const now = new Date(); now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    setIForm({ candidate_id: candidateId, scheduled_at: now.toISOString().slice(0, 16), call_lead_minutes: 1, specific_questions: "" });
    setIErr(""); setEditInt(null); setShowInt(true);
  };
  const openEditInt = (i: HRInterview) => {
    const local = new Date(i.scheduled_at); local.setMinutes(local.getMinutes() - local.getTimezoneOffset());
    setIForm({ candidate_id: i.candidate_id, scheduled_at: local.toISOString().slice(0, 16), call_lead_minutes: i.call_lead_minutes, specific_questions: i.specific_questions ?? "" });
    setIErr(""); setEditInt(i); setShowInt(true);
  };
  const saveInt = async () => {
    if (!iForm.scheduled_at) { setIErr("Interview date and time is required"); return; }
    setISaving(true); setIErr("");
    try {
      const payload = { ...iForm, agent_id: agentId, scheduled_at: new Date(iForm.scheduled_at).toISOString() };
      if (editInt) {
        const u = await API.updateInterview(editInt.id, payload);
        setInterviews(is => is.map(i => i.id === u.id ? u : i));
      } else {
        const created = await API.createInterview(payload);
        setInterviews(is => [...is, created]);
      }
      setShowInt(false);
      pushToast(editInt ? "Call updated" : "Call scheduled");
    } catch (e: unknown) { setIErr(e instanceof Error ? e.message : "Failed"); }
    setISaving(false);
  };
  const deleteInt = async (id: string) => {
    if (!confirm("Delete this scheduled call?")) return;
    setInterviews(is => is.filter(i => i.id !== id));
    try {
      await API.deleteInterview(id);
      pushToast("Scheduled call removed");
    } catch (e: unknown) {
      pushToast(e instanceof Error ? e.message : "Failed to delete scheduled call");
    } finally {
      const all = await API.hrInterviews().catch(() => null);
      if (all) setInterviews(all.filter((i: HRInterview) => i.agent_id === agentId));
    }
  };
  const cancelInt = async (id: string) => {
    const u = await API.updateInterview(id, { status: "cancelled" });
    setInterviews(is => is.map(i => i.id === u.id ? u : i));
  };

  return (
    <>
      <Section title="HR Instructions">
        <div style={{ fontSize: 12, color: "var(--ink-muted)", marginBottom: 8 }}>
          Default questions and instructions for every call this agent makes.
          The agent will always follow these regardless of which candidate it calls.
        </div>
        <textarea
          className="textarea"
          value={hrInstructions}
          onChange={e => onHrInstructionsChange(e.target.value)}
          rows={6}
          placeholder={"Always greet the candidate warmly by name.\nAsk these questions in order:\n1. Tell me about yourself.\n2. Why are you interested in this role?\n3. What is your expected CTC?\n\nIf the candidate asks about next steps, tell them HR will follow up within 3 business days."}
          style={{ fontFamily: "var(--font-mono)" }}
        />
        <div className="char-count">{hrInstructions.length} chars, saved with the agent</div>
      </Section>

      <Section title="HR Call Manager">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <span style={{ fontSize: 12, color: "var(--ink-muted)" }}>{candidates.length} candidate{candidates.length !== 1 ? "s" : ""}</span>
          <button className="btn btn-primary btn-sm" onClick={openAddCand}><Icon name="plus" size={14} />Add candidate</button>
        </div>

        {loading ? <div style={{ color: "var(--ink-faint)", fontSize: 13 }}>Loading...</div> : candidates.length === 0 ? (
          <div className="empty"><p>No candidates yet. Add a candidate to get started.</p></div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {candidates.map(c => {
              const candInterviews = interviews.filter(iv => iv.candidate_id === c.id);
              return (
                <div key={c.id} className="card" style={{ overflow: "hidden" }}>
                  <div style={{ padding: "12px 14px", background: "var(--surface-2)", display: "flex", alignItems: "center", gap: 12 }}>
                    <span className="avatar avatar-sm hue-indigo" style={{ borderRadius: "50%" }}>{c.name[0]?.toUpperCase()}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
                        <span style={{ fontWeight: 600, fontSize: 13, color: "var(--ink)" }}>{c.name}</span>
                        {c.role && <span className="pill pill-accent">{c.role}</span>}
                        {c.resume_file_name && <span className="pill pill-success">CV attached</span>}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>{c.phone}{c.email ? ` · ${c.email}` : ""}</div>
                    </div>
                    <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                      <button className="btn btn-outline btn-sm" onClick={() => openAddInt(c.id)}>Schedule call</button>
                      <button className="btn btn-ghost btn-sm" onClick={() => openEditCand(c)}>Edit</button>
                      <button className="btn btn-danger btn-sm" onClick={() => deleteCand(c.id)}>Delete</button>
                    </div>
                  </div>
                  {candInterviews.length === 0 ? (
                    <div style={{ padding: "10px 14px", fontSize: 11.5, color: "var(--ink-faint)", borderTop: "1px solid var(--border)" }}>
                      No calls scheduled. Use &quot;Schedule call&quot; to set one up.
                    </div>
                  ) : (
                    candInterviews.map(iv => {
                      const s = hrStatusPill(iv.status);
                      return (
                        <div key={iv.id} style={{ borderTop: "1px solid var(--border)", padding: "10px 14px", display: "flex", alignItems: "flex-start", gap: 10 }}>
                          <div style={{ flex: 1 }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3, flexWrap: "wrap" }}>
                              <span className={`pill ${s.cls}`}>{s.label}</span>
                              <span style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 500 }}>{new Date(iv.scheduled_at).toLocaleString()}</span>
                              <span style={{ fontSize: 11, color: "var(--ink-muted)" }}>calls {iv.call_lead_minutes} min before</span>
                            </div>
                            {iv.specific_questions && (
                              <div style={{ fontSize: 11, color: "var(--ink-muted)", marginTop: 2 }}>
                                <span style={{ fontWeight: 600 }}>Q: </span>{iv.specific_questions.slice(0, 120)}{iv.specific_questions.length > 120 ? "…" : ""}
                              </div>
                            )}
                          </div>
                          <div style={{ display: "flex", gap: 5, flexShrink: 0 }}>
                            {iv.status === "pending" && <>
                              <button className="btn btn-ghost btn-sm" onClick={() => openEditInt(iv)}>Edit</button>
                              <button className="btn btn-outline btn-sm" onClick={() => cancelInt(iv.id)}>Cancel</button>
                            </>}
                            <button className="btn btn-danger btn-sm" onClick={() => deleteInt(iv.id)}>Delete</button>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {showCand && (
        <div className="modal-backdrop" onClick={() => setShowCand(false)}>
          <div className="modal modal-lg" onClick={e => e.stopPropagation()}>
            <div className="modal-head"><h3>{editCand ? "Edit candidate" : "Add candidate"}</h3><button className="icon-btn" onClick={() => setShowCand(false)}><Icon name="x" /></button></div>
            <div className="modal-body">
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="field"><label>Full name *</label><input className="input" value={cForm.name} onChange={e => setCForm(f => ({ ...f, name: e.target.value }))} placeholder="Priya Sharma" /></div>
                <div className="field"><label>Phone (E.164) *</label><input className="input" value={cForm.phone} onChange={e => setCForm(f => ({ ...f, phone: e.target.value }))} placeholder="+919876543210" /></div>
                <div className="field"><label>Email</label><input className="input" value={cForm.email} onChange={e => setCForm(f => ({ ...f, email: e.target.value }))} placeholder="priya@email.com" /></div>
                <div className="field"><label>Role applied for</label><input className="input" value={cForm.role} onChange={e => setCForm(f => ({ ...f, role: e.target.value }))} placeholder="Senior Engineer" /></div>
              </div>
              <div className="field"><label>HR notes (visible to agent)</label><textarea className="textarea" style={{ minHeight: 60 }} value={cForm.notes} onChange={e => setCForm(f => ({ ...f, notes: e.target.value }))} placeholder="5 YOE, strong backend, referred by..." /></div>
              <div className="field">
                <label>CV / resume (PDF or DOCX)</label>
                <div className="dropzone" style={{ padding: "16px 20px", marginBottom: 0 }}
                  onClick={() => document.getElementById("cv-upload-input")?.click()}
                  onDragOver={e => e.preventDefault()}
                  onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) setCResumeFile(f); }}>
                  <input id="cv-upload-input" type="file" accept=".pdf,.docx,.txt" style={{ display: "none" }}
                    onChange={e => { const f = e.target.files?.[0]; if (f) setCResumeFile(f); }} />
                  {cResumeFile ? (
                    <p style={{ color: "var(--success)" }}>{cResumeFile.name}<span className="hint" style={{ display: "block" }}>Click to change</span></p>
                  ) : editCand?.resume_file_name ? (
                    <p style={{ color: "var(--success)" }}>{editCand.resume_file_name}<span className="hint" style={{ display: "block" }}>Click or drag to replace</span></p>
                  ) : (
                    <p>Click or drag and drop CV here<span className="hint" style={{ display: "block" }}>PDF, DOCX, or TXT</span></p>
                  )}
                </div>
              </div>
              <div style={{ margin: "16px 0 12px", borderTop: "1px solid var(--border)", paddingTop: 16 }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: "var(--accent)", marginBottom: 10 }}>
                  Schedule interview call <span style={{ fontWeight: 400, color: "var(--ink-faint)" }}>(optional)</span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <div className="field"><label>Interview date and time</label><input className="input" type="datetime-local" value={cForm.scheduled_at} onChange={e => setCForm(f => ({ ...f, scheduled_at: e.target.value }))} /></div>
                  <div className="field"><label>Call X min before interview</label><input className="input" type="number" min={1} max={120} value={cForm.call_lead_minutes} onChange={e => setCForm(f => ({ ...f, call_lead_minutes: e.target.value === "" ? ("" as unknown as number) : parseInt(e.target.value) }))} onBlur={e => { if (e.target.value === "" || isNaN(parseInt(e.target.value))) setCForm(f => ({ ...f, call_lead_minutes: 1 })); }} /></div>
                </div>
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Specific questions for this candidate</label>
                  <textarea className="textarea" style={{ minHeight: 80 }} value={cForm.specific_questions} onChange={e => setCForm(f => ({ ...f, specific_questions: e.target.value }))}
                    placeholder={"1. Walk me through your last project.\n2. What is your expected CTC?"} />
                </div>
              </div>
              {cErr && <div style={{ fontSize: 12, color: "var(--danger)" }}>{cErr}</div>}
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={() => setShowCand(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={saveCand} disabled={cSaving}>{cSaving ? "Saving..." : editCand ? "Save" : "Add candidate"}</button>
            </div>
          </div>
        </div>
      )}

      {showInt && (
        <div className="modal-backdrop" onClick={() => setShowInt(false)}>
          <div className="modal modal-lg" onClick={e => e.stopPropagation()}>
            <div className="modal-head"><h3>{editInt ? "Edit scheduled call" : "Schedule call"}</h3><button className="icon-btn" onClick={() => setShowInt(false)}><Icon name="x" /></button></div>
            <div className="modal-body">
              {(() => { const c = candidates.find(x => x.id === iForm.candidate_id); return c ? (
                <div className="pill pill-accent" style={{ marginBottom: 14 }}>{c.name}{c.role ? ` · ${c.role}` : ""}</div>
              ) : null; })()}
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                <div className="field"><label>Interview date and time *</label><input className="input" type="datetime-local" value={iForm.scheduled_at} onChange={e => setIForm(f => ({ ...f, scheduled_at: e.target.value }))} /></div>
                <div className="field">
                  <label>Call X min before interview</label>
                  <input className="input" type="number" min={1} max={120} value={iForm.call_lead_minutes} onChange={e => setIForm(f => ({ ...f, call_lead_minutes: e.target.value === "" ? ("" as unknown as number) : parseInt(e.target.value) }))} onBlur={e => { if (e.target.value === "" || isNaN(parseInt(e.target.value))) setIForm(f => ({ ...f, call_lead_minutes: 1 })); }} />
                  <div style={{ fontSize: 10, color: "var(--ink-faint)", marginTop: 4 }}>Agent dials this many minutes before the interview</div>
                </div>
              </div>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Specific questions for this candidate</label>
                <textarea className="textarea" value={iForm.specific_questions} onChange={e => setIForm(f => ({ ...f, specific_questions: e.target.value }))}
                  placeholder={"1. Walk me through your last project.\n2. How do you handle conflicts in a team?\n3. What is your expected joining date?"} />
                <div style={{ fontSize: 11, color: "var(--ink-faint)", marginTop: 4 }}>These are in addition to the agent&apos;s HR instructions above.</div>
              </div>
              {iErr && <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 10 }}>{iErr}</div>}
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={() => setShowInt(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={saveInt} disabled={iSaving}>{iSaving ? "Saving..." : editInt ? "Save" : "Schedule call"}</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/* ---------------------------------------------------------------------------
   Agent Detail
--------------------------------------------------------------------------- */

function AgentDetail({ agent, onBack, onUpdate, onDuplicate, onGoToKb }: {
  agent: Agent;
  onBack: () => void;
  onUpdate: (a: Agent) => void;
  onDuplicate: (a: Agent) => void;
  onGoToKb: () => void;
}) {
  const [name, setName] = useState(agent.name);
  const [prompt, setPrompt] = useState(agent.system_prompt ?? DEFAULT_SYSTEM_PROMPT);
  const [kbInstructions, setKbInstructions] = useState(agent.kb_instructions ?? "");
  const [hrInstructions, setHrInstructions] = useState(agent.hr_instructions ?? "");
  const [fillerPhrases, setFillerPhrases] = useState(agent.filler_phrases ?? "");
  const [firstMsgEnabled, setFirstMsgEnabled] = useState(agent.first_message_enabled);
  const [firstMsg, setFirstMsg] = useState(agent.first_message ?? "");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  const [selectedDocIds, setSelectedDocIds] = useState<string[]>(agent.selected_kb_doc_ids ?? []);
  const [userKbDocs, setUserKbDocs] = useState<import("@/lib/api").UserDocument[]>([]);
  const [kbLoading, setKbLoading] = useState(false);
  const [kbOpen, setKbOpen] = useState(false);
  const kbDropRef = useRef<HTMLDivElement>(null);

  const [functions, setFunctions] = useState<AgentFunction[]>([]);
  const [funcsLoading, setFuncsLoading] = useState(false);
  const [showFuncForm, setShowFuncForm] = useState(false);
  const [editingFunc, setEditingFunc] = useState<AgentFunction | null>(null);
  const [expandedFuncs, setExpandedFuncs] = useState<Set<string>>(new Set());
  const [showDelete, setShowDelete] = useState(false);

  useEffect(() => {
    setName(agent.name); setPrompt(agent.system_prompt ?? DEFAULT_SYSTEM_PROMPT);
    setKbInstructions(agent.kb_instructions ?? "");
    setHrInstructions(agent.hr_instructions ?? "");
    setFillerPhrases(agent.filler_phrases ?? "");
    setFirstMsgEnabled(agent.first_message_enabled); setFirstMsg(agent.first_message ?? "");
    setSelectedDocIds(agent.selected_kb_doc_ids ?? []);
    loadUserKb(); loadFuncs();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id]);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (kbDropRef.current && !kbDropRef.current.contains(e.target as Node)) setKbOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const loadUserKb = async () => {
    setKbLoading(true);
    try { setUserKbDocs((await API.userKbDocuments()).documents); } catch { /* ignore */ }
    setKbLoading(false);
  };

  const loadFuncs = async () => {
    setFuncsLoading(true);
    try { setFunctions(await API.agentFunctions(agent.id)); } catch { /* ignore */ }
    setFuncsLoading(false);
  };

  const saveAgent = async () => {
    setSaving(true); setSaveMsg("");
    try {
      const updated = await API.updateAgent(agent.id, {
        name: name.trim(), system_prompt: prompt,
        kb_instructions: kbInstructions || null,
        hr_instructions: hrInstructions || null,
        filler_phrases: fillerPhrases || null,
        first_message_enabled: firstMsgEnabled,
        first_message: firstMsg || null,
        selected_kb_doc_ids: selectedDocIds,
      });
      onUpdate(updated); setSaveMsg("Saved"); pushToast("Agent saved");
      setTimeout(() => setSaveMsg(""), 2000);
    } catch (e: unknown) {
      setSaveMsg(e instanceof Error ? e.message : "Save failed");
      pushToast("Could not save agent", "error");
    } finally { setSaving(false); }
  };

  const handleDuplicate = async () => {
    try { const dup = await API.duplicateAgent(agent.id); onDuplicate(dup); pushToast("Agent duplicated"); }
    catch { pushToast("Could not duplicate agent", "error"); }
  };

  const handleDeleteAgent = async () => {
    try { await API.deleteAgent(agent.id); pushToast("Agent deleted"); onBack(); }
    catch { pushToast("Could not delete agent", "error"); }
  };

  const templatePrompts: Record<string, string> = {
    Receptionist: `# Identity & Role
You are a friendly front desk receptionist. You greet callers warmly, answer questions about business hours, location, and services, and help schedule or confirm appointments. Be welcoming, professional, and efficient. For scheduling, collect: caller name, contact details, reason for the visit, and preferred date and time. Confirm everything before finalizing. Respond naturally for a voice conversation - no bullet points or markdown.

# Current Date & Time
Today is {{current_time}}. Use this as the only source of truth for "today" - never your training data.

## Voice & Persona

### Personality
**Warm & Welcoming:** Sound friendly, patient, and genuinely glad to help.
**Concise & Direct:** **This is a strict rule.** Always get straight to the point. Avoid filler words and long explanations. Your goal is to resolve the query in as few turns as possible.
**Natural & Conversational:** Use a human-like, conversational tone (e.g., "I'm," "don't," "it's").
**Confident:** Speak with confidence, but if you don't know something, say "Hmm, let me check on that for you."

### Speech Characteristics
Use contractions (I'm, we'll, don't).
**Keep responses short,** ideally under 30 words.
Ask only one question at a time.

### Tone
Friendly
Professional
Clear and concise
Confident, not pushy

# Purpose
Handle incoming calls, answer general business questions, and schedule or confirm appointments.

# Knowledge Base`,
    Support: `# Identity & Role
You are a helpful customer support agent. You listen carefully to issues, empathize with the caller, and provide clear step by step solutions. When you cannot resolve something, collect: caller name, contact details, account or order reference, and a summary of the issue, then confirm the next steps. Respond naturally for a voice conversation - no bullet points or markdown.

# Current Date & Time
Today is {{current_time}}. Use this as the only source of truth for "today" - never your training data.

## Voice & Persona

### Personality
**Empathetic & Patient:** Sound warm, patient, and genuinely invested in solving the issue.
**Concise & Direct:** **This is a strict rule.** Always get straight to the point. Avoid filler words and long explanations. Your goal is to resolve the query in as few turns as possible.
**Natural & Conversational:** Use a human-like, conversational tone (e.g., "I'm," "don't," "it's").
**Confident:** Speak with confidence, but if you don't know something, say "Hmm, let me check on that for you."

### Speech Characteristics
Use contractions (I'm, we'll, don't).
**Keep responses short,** ideally under 30 words.
Ask only one question at a time.

### Tone
Friendly
Professional
Clear and concise
Confident, not pushy

# Purpose
Resolve customer issues quickly, or capture the details needed to escalate them.

# Knowledge Base`,
    Sales: `# Identity & Role
You are a professional sales agent. You understand the prospect's needs, highlight relevant product benefits, and guide them toward a purchasing decision without being pushy. For qualified leads, collect: contact name, contact details, product interest, and preferred follow up time. Confirm everything before finalizing. Respond naturally for a voice conversation - no bullet points or markdown.

# Current Date & Time
Today is {{current_time}}. Use this as the only source of truth for "today" - never your training data.

## Voice & Persona

### Personality
**Friendly & Persuasive:** Sound warm, confident, and genuinely helpful, never pushy.
**Concise & Direct:** **This is a strict rule.** Always get straight to the point. Avoid filler words and long explanations. Your goal is to resolve the query in as few turns as possible.
**Natural & Conversational:** Use a human-like, conversational tone (e.g., "I'm," "don't," "it's").
**Confident:** Speak with confidence, but if you don't know something, say "Hmm, let me check on that for you."

### Speech Characteristics
Use contractions (I'm, we'll, don't).
**Keep responses short,** ideally under 30 words.
Ask only one question at a time.

### Tone
Friendly
Professional
Clear and concise
Confident, not pushy

# Purpose
Qualify prospects, answer product questions, and move interested callers toward a purchase or follow up.

# Knowledge Base`,
    Assistant: `# Identity & Role
You are a helpful personal assistant. You complete tasks efficiently, provide accurate information, and proactively offer useful suggestions. Confirm details with the caller before taking any action on their behalf. Respond naturally for a voice conversation - no bullet points or markdown.

# Current Date & Time
Today is {{current_time}}. Use this as the only source of truth for "today" - never your training data.

## Voice & Persona

### Personality
**Helpful & Proactive:** Sound warm, capable, and ready to take initiative.
**Concise & Direct:** **This is a strict rule.** Always get straight to the point. Avoid filler words and long explanations. Your goal is to resolve the query in as few turns as possible.
**Natural & Conversational:** Use a human-like, conversational tone (e.g., "I'm," "don't," "it's").
**Confident:** Speak with confidence, but if you don't know something, say "Hmm, let me check on that for you."

### Speech Characteristics
Use contractions (I'm, we'll, don't).
**Keep responses short,** ideally under 30 words.
Ask only one question at a time.

### Tone
Friendly
Professional
Clear and concise
Confident, not pushy

# Purpose
Help the caller get tasks done and answer questions accurately.

# Knowledge Base`,
    HR: `# Identity & Role
You are a helpful HR support agent. You assist employees and candidates with HR-related questions including: interview scheduling, onboarding steps, leave policies, benefits, and general HR inquiries. Be empathetic, professional, and concise. For interview scheduling, collect: candidate name, contact details, role applied for, and preferred interview slot. Confirm everything before finalizing. Respond naturally for a voice conversation - no bullet points or markdown.

# Current Date & Time
Today is {{current_time}}. Use this as the only source of truth for "today" - never your training data.

## Voice & Persona

### Personality
**Friendly & Patient:** Sound warm, patient, and knowledgeable.
**Concise & Direct:** **This is a strict rule.** Always get straight to the point. Avoid filler words and long explanations. Your goal is to resolve the query in as few turns as possible.
**Natural & Conversational:** Use a human-like, conversational tone (e.g., "I'm," "don't," "it's").
**Confident:** Speak with confidence, but if you don't know something, say "Hmm, let me check on that for you."

### Speech Characteristics
Use contractions (I'm, we'll, don't).
**Keep responses short,** ideally under 30 words.
Ask only one question at a time.

### Tone
Friendly
Professional
Clear and concise
Confident, not pushy

# Purpose

#Knowledge Base`,
  };

  const hue = agentHue(agent.id);
  const initials = agentInitials(agent.name);

  return (
    <div className="view" style={{ maxWidth: "none" }}>
      <button className="back-link" onClick={onBack}><Icon name="arrow-left" />Agents</button>
      <div className="detail-head">
        <div className="detail-title-row">
          <div className={`avatar hue-${hue}`} style={{ width: 44, height: 44, borderRadius: 13, fontSize: 16 }}>{initials}</div>
          <div>
            <input className="agent-title-input" value={name} onChange={e => setName(e.target.value)} />
            <div className="detail-edited">Voice agent, edited {timeAgo(agent.updated_at)}</div>
          </div>
        </div>
        <div className="view-actions">
          <button className="btn btn-ghost" onClick={handleDuplicate}><Icon name="copy" />Duplicate</button>
          <button className="btn btn-primary" onClick={saveAgent} disabled={saving}>
            <Icon name="plus" />{saving ? "Saving..." : saveMsg || "Save agent"}
          </button>
        </div>
      </div>

      <div className="detail-wrap">
        <div>
          <Section title="System prompt">
            <div className="chip-row">
              {Object.keys(templatePrompts).map(t => (
                <button key={t} className="chip-btn" onClick={() => setPrompt(templatePrompts[t])}>
                  <Icon name="plus" size={13} />{t}
                </button>
              ))}
            </div>
            <textarea className="textarea" value={prompt} onChange={e => setPrompt(e.target.value)}
              placeholder="Describe how this agent should behave, its personality, and what tasks it handles..."
              rows={8} style={{ fontFamily: "var(--font-mono)" }} />
            <div className="char-count">{prompt.length} chars</div>
            {selectedDocIds.length > 0 && (
              <div className="kb-note">
                <Icon name="book" />
                <div>
                  <b>Attached KB documents ({selectedDocIds.length})</b>
                  <p>{selectedDocIds.map(id => userKbDocs.find(d => d.id === id)?.display_name ?? "").filter(Boolean).join(" · ")}</p>
                  <p>Reference these in your prompt, e.g. &quot;When asked about [topic], refer to the attached documents.&quot;</p>
                </div>
              </div>
            )}
          </Section>

          <Section title="Custom functions">
            {funcsLoading ? (
              <div style={{ color: "var(--ink-faint)", fontSize: 13 }}>Loading...</div>
            ) : (
              <>
                {functions.map(fn => (
                  <div key={fn.id} style={{ marginBottom: 8 }}>
                    {editingFunc?.id === fn.id ? (
                      <FunctionForm agentId={agent.id} initial={fn}
                        onSave={updated => { setFunctions(fns => fns.map(f => f.id === fn.id ? updated : f)); setEditingFunc(null); }}
                        onCancel={() => setEditingFunc(null)} />
                    ) : (
                      <div className="card" style={{ padding: "12px 14px" }}>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <button onClick={() => setExpandedFuncs(s => { const n = new Set(s); if (n.has(fn.id)) n.delete(fn.id); else n.add(fn.id); return n; })}
                              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--ink-faint)", padding: 0, display: "flex" }}>
                              <Icon name="chevron" size={14} style={{ transform: expandedFuncs.has(fn.id) ? "none" : "rotate(-90deg)" }} />
                            </button>
                            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--ink)" }}>{fn.name}</span>
                            <span className={`pill ${fn.url ? "pill-success" : "pill-neutral"}`}>{fn.url ? "Active" : "Draft"}</span>
                          </div>
                          <div style={{ display: "flex", gap: 10 }}>
                            <button className="link-btn" onClick={() => setEditingFunc(fn)}>Edit</button>
                            <button className="link-btn" style={{ color: "var(--danger)" }} onClick={async () => {
                              if (!confirm("Delete this function?")) return;
                              await API.deleteAgentFunction(agent.id, fn.id);
                              setFunctions(fns => fns.filter(f => f.id !== fn.id));
                            }}>Delete</button>
                          </div>
                        </div>
                        {expandedFuncs.has(fn.id) && (
                          <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
                            {fn.description && <p style={{ fontSize: 12, color: "var(--ink-muted)", margin: "0 0 6px" }}>{fn.description}</p>}
                            {fn.url && <p style={{ fontSize: 11, color: "var(--ink-faint)", margin: "0 0 6px", fontFamily: "var(--font-mono)" }}>{fn.method} {fn.url}</p>}
                            {fn.parameters.length > 0 && (
                              <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>{fn.parameters.map(p => `${p.name}: ${p.type}`).join(" · ")}</div>
                            )}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ))}

                {showFuncForm && !editingFunc ? (
                  <FunctionForm agentId={agent.id}
                    onSave={fn => { setFunctions(fns => [...fns, fn]); setShowFuncForm(false); }}
                    onCancel={() => setShowFuncForm(false)} />
                ) : !editingFunc && (
                  <button className="add-func-btn" onClick={() => setShowFuncForm(true)}>
                    <Icon name="plus" size={14} style={{ display: "inline", verticalAlign: "-3px" }} /> Add function
                  </button>
                )}
              </>
            )}
          </Section>

          <Section title="Knowledge base">
            <div style={{ marginBottom: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink-2)" }}>Select documents from your knowledge base</label>
                <button className="link-btn" onClick={onGoToKb}>+ Add documents</button>
              </div>

              <div ref={kbDropRef} style={{ position: "relative" }}>
                <div className="input" onClick={() => setKbOpen(o => !o)}
                  style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ color: selectedDocIds.length === 0 ? "var(--ink-faint)" : "var(--ink)" }}>
                    {selectedDocIds.length === 0 ? "Choose documents..." : `${selectedDocIds.length} document${selectedDocIds.length > 1 ? "s" : ""} selected`}
                  </span>
                  <Icon name="chevron" size={12} style={{ transform: kbOpen ? "rotate(180deg)" : "none" }} />
                </div>

                {kbOpen && (
                  <div className="card" style={{ position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 100, maxHeight: 260, overflowY: "auto" }}>
                    {kbLoading ? (
                      <div style={{ padding: 14, color: "var(--ink-faint)", fontSize: 13 }}>Loading your documents...</div>
                    ) : userKbDocs.length === 0 ? (
                      <div style={{ padding: 14 }}>
                        <div style={{ color: "var(--ink-faint)", fontSize: 13, marginBottom: 8 }}>No documents in your knowledge base yet.</div>
                        <button className="link-btn" onClick={() => { setKbOpen(false); onGoToKb(); }}>Go to Knowledge Base to upload files</button>
                      </div>
                    ) : (
                      userKbDocs.map(doc => {
                        const ext = doc.file_name.split(".").pop()?.toLowerCase() ?? "txt";
                        const label = ext === "pdf" ? "PDF" : ext === "docx" ? "DOC" : "TXT";
                        const checked = selectedDocIds.includes(doc.id);
                        return (
                          <div key={doc.id} onClick={() => setSelectedDocIds(ids => checked ? ids.filter(x => x !== doc.id) : [...ids, doc.id])}
                            style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 14px", cursor: "pointer", background: checked ? "var(--accent-soft)" : "transparent", borderBottom: "1px solid var(--border)" }}>
                            <div style={{
                              width: 16, height: 16, borderRadius: 4, border: `2px solid ${checked ? "var(--accent)" : "var(--border-strong)"}`,
                              background: checked ? "var(--accent)" : "var(--surface)", flexShrink: 0,
                              display: "flex", alignItems: "center", justifyContent: "center",
                            }}>
                              {checked && <span style={{ color: "var(--accent-ink)", fontSize: 10, lineHeight: 1 }}>✓</span>}
                            </div>
                            <span className="filetype" style={{ width: "auto", height: "auto", padding: "2px 6px", fontSize: 10 }}>{label}</span>
                            <div style={{ minWidth: 0 }}>
                              <div style={{ fontSize: 13, fontWeight: 500, color: "var(--ink)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {doc.display_name || doc.file_name}
                              </div>
                              <div style={{ fontSize: 11, color: "var(--ink-faint)" }}>{formatBytes(doc.file_size)}</div>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>
                )}
              </div>

              {selectedDocIds.length > 0 && (
                <div style={{ marginTop: 10 }}>
                  {selectedDocIds.map(id => {
                    const doc = userKbDocs.find(d => d.id === id);
                    if (!doc) return null;
                    return (
                      <span key={id} className="doc-chip">
                        {doc.display_name || doc.file_name}
                        <button onClick={() => setSelectedDocIds(ids => ids.filter(x => x !== id))}>×</button>
                      </span>
                    );
                  })}
                </div>
              )}
            </div>

            <div className="field" style={{ marginBottom: 0 }}>
              <label>KB instructions</label>
              <textarea className="textarea" style={{ minHeight: 70 }} value={kbInstructions} onChange={e => setKbInstructions(e.target.value)}
                placeholder="Instructions for how the agent should use knowledge base results..." />
            </div>
          </Section>

          <Section title="First words">
            <div style={{ display: "grid", gridTemplateColumns: firstMsgEnabled ? "1fr 2fr" : "1fr", gap: 14 }}>
              <div className="field" style={{ marginBottom: 0 }}>
                <label>Behavior</label>
                <select className="select" value={firstMsgEnabled ? "agent" : "user"} onChange={e => setFirstMsgEnabled(e.target.value === "agent")}>
                  <option value="agent">Agent speaks first</option>
                  <option value="user">Caller speaks first</option>
                </select>
              </div>
              {firstMsgEnabled && (
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Opening message</label>
                  <input className="input" value={firstMsg} onChange={e => setFirstMsg(e.target.value)} placeholder="Hello! How can I help you today?" />
                </div>
              )}
            </div>
          </Section>

          <Section title="Filler phrases">
            <div style={{ fontSize: 12, color: "var(--ink-muted)", marginBottom: 8 }}>
              Spoken while the agent is looking something up, so the caller doesn&apos;t hear dead air.
              One phrase per line. Leave blank to use the default generic phrases.
            </div>
            <textarea
              className="textarea"
              value={fillerPhrases}
              onChange={e => setFillerPhrases(e.target.value)}
              rows={4}
              placeholder={"Let me check that for you.\nOne moment, I'll look that up.\nGive me just a second."}
              style={{ fontFamily: "var(--font-mono)" }}
            />
          </Section>

          {agent.name === "HR Support Agent" && (
            <AgentHRSection agentId={agent.id} hrInstructions={hrInstructions} onHrInstructionsChange={setHrInstructions} />
          )}

          <div className="section">
            <div className="danger-zone">
              <div>
                <div style={{ fontWeight: 700, fontSize: 13.5, color: "var(--danger)" }}>Delete this agent</div>
                <p>This disconnects any phone numbers and removes it permanently.</p>
              </div>
              {!showDelete ? (
                <button className="btn btn-danger" onClick={() => setShowDelete(true)}><Icon name="trash" />Delete</button>
              ) : (
                <div style={{ display: "flex", gap: 8 }}>
                  <button className="btn btn-ghost" onClick={() => setShowDelete(false)}>Cancel</button>
                  <button className="btn btn-danger" onClick={handleDeleteAgent}>Yes, delete</button>
                </div>
              )}
            </div>
          </div>
        </div>

        <VoiceDock agentId={agent.id} agentName={agent.name} docIds={selectedDocIds} />
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Knowledge Base view
--------------------------------------------------------------------------- */

const KB_TOTAL_MB = 10;
const KB_FILE_MB = 5;

function KnowledgeBaseView() {
  const [docs, setDocs] = useState<import("@/lib/api").UserDocument[]>([]);
  const [usedBytes, setUsedBytes] = useState(0);
  const [limitBytes, setLimitBytes] = useState(KB_TOTAL_MB * 1024 * 1024);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try {
      const res = await API.userKbDocuments();
      setDocs(res.documents);
      setUsedBytes(res.storage_used_bytes);
      setLimitBytes(res.storage_limit_bytes);
    } catch { /* ignore */ }
    setLoading(false);
  };

  const handleUpload = async (file: File) => {
    setErr("");
    if (file.size > KB_FILE_MB * 1024 * 1024) { setErr(`File too large. Maximum ${KB_FILE_MB} MB per file.`); return; }
    if (usedBytes + file.size > limitBytes) { setErr(`Not enough space. You have ${formatBytes(limitBytes - usedBytes)} remaining.`); return; }
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!["pdf", "docx", "txt"].includes(ext)) { setErr("Only PDF, DOCX, and TXT files are supported."); return; }
    setUploading(true);
    try {
      const doc = await API.uploadUserKbDocument(file);
      setDocs(d => [...d, doc]);
      setUsedBytes(b => b + file.size);
      pushToast(`${doc.display_name || doc.file_name} uploaded`);
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : "Upload failed"); }
    setUploading(false);
  };

  const handleDelete = async (docId: string, fileSize: number) => {
    try {
      await API.deleteUserKbDocument(docId);
      setDocs(d => d.filter(x => x.id !== docId));
      setUsedBytes(b => b - fileSize);
      pushToast("Document removed");
    } catch { /* ignore */ }
  };

  const usedPct = Math.min(100, (usedBytes / limitBytes) * 100);
  const barColor = usedPct > 85 ? "var(--danger)" : usedPct > 65 ? "var(--warning)" : "var(--accent)";

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1 className="view-title">Knowledge Base</h1>
          <p className="view-sub">Your personal document library. Attach documents to agents from their Knowledge Base section.</p>
        </div>
      </div>

      <div className="stat-grid" style={{ gridTemplateColumns: "repeat(3,1fr)" }}>
        <StatCard label="Documents" value={docs.length} icon="file" tone="accent" />
        <StatCard label="Indexed" value={docs.length} icon="check-circle" tone="success" />
        <StatCard label="Storage used" value={formatBytes(usedBytes)} icon="book" tone="teal" />
      </div>

      <div className="card storage-card">
        <div className="storage-top">
          <b>Storage</b>
          <span>{formatBytes(usedBytes)} / {formatBytes(limitBytes)}<span style={{ color: "var(--ink-faint)", marginLeft: 6 }}>· max {KB_FILE_MB} MB per file</span></span>
        </div>
        <div className="progress"><div className="progress-fill" style={{ width: `${usedPct}%`, background: barColor }} /></div>
      </div>

      <div className={`dropzone${dragActive ? " drag" : ""}`}
        onClick={() => fileRef.current?.click()}
        onDragOver={e => { e.preventDefault(); setDragActive(true); }}
        onDragLeave={() => setDragActive(false)}
        onDrop={async e => { e.preventDefault(); setDragActive(false); const file = e.dataTransfer.files[0]; if (file) await handleUpload(file); }}>
        <div className="up-ic">{uploading ? <Spinner /> : <Icon name="upload" />}</div>
        <p>{uploading ? "Uploading..." : <>Drop files here or <span style={{ color: "var(--accent)" }}>browse</span></>}</p>
        <div className="hint">PDF, DOCX, TXT · max {KB_FILE_MB} MB per file · {KB_TOTAL_MB} MB total</div>
      </div>
      <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" style={{ display: "none" }}
        onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); e.target.value = ""; }} />

      {err && (
        <div style={{ padding: "10px 14px", borderRadius: "var(--r-md)", background: "var(--danger-soft)", color: "var(--danger)", fontSize: 13, marginBottom: 16 }}>
          {err}
        </div>
      )}

      {loading ? (
        <div style={{ color: "var(--ink-faint)", fontSize: 14 }}>Loading...</div>
      ) : docs.length === 0 ? (
        <div className="empty"><div className="ic"><Icon name="book" /></div><p>No documents yet. Upload your first file above.</p></div>
      ) : (
        <div className="doc-list">
          {docs.map(doc => {
            const ext = doc.file_name.split(".").pop()?.toLowerCase() ?? "txt";
            const label = ext === "pdf" ? "PDF" : ext === "docx" ? "DOC" : "TXT";
            return (
              <div key={doc.id} className="doc-row">
                <div className="filetype">{label}</div>
                <div className="doc-info">
                  <div className="nm">{doc.display_name || doc.file_name}</div>
                  <div className="sub">
                    <span>{formatBytes(doc.file_size)}</span>·<span style={{ color: "var(--success)" }}>indexed</span>·<span>{timeAgo(doc.created_at)}</span>
                  </div>
                </div>
                <button className="icon-btn" onClick={() => handleDelete(doc.id, doc.file_size)}><Icon name="trash" /></button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Call Analytics
--------------------------------------------------------------------------- */

interface ConvDetail {
  id: string;
  title: string;
  created_at: string;
  agent_id?: string | null;
  sentiment?: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | null;
  dominant_emotion?: string | null;
  sentiment_score?: number | null;
  call_status?: "ringing" | "in_progress" | "completed" | "failed" | "busy" | "no_answer" | null;
  failure_reason?: string | null;
  summary?: string | null;
}

function sentimentPill(s: string | null | undefined): { cls: string; label: string } {
  if (s === "POSITIVE") return { cls: "pill-success", label: "Positive" };
  if (s === "NEGATIVE") return { cls: "pill-danger", label: "Negative" };
  return { cls: "pill-neutral", label: "Neutral" };
}

function callStatusPill(s: string | null | undefined): { cls: string; label: string } {
  if (s === "failed") return { cls: "pill-danger", label: "Failed" };
  if (s === "busy") return { cls: "pill-warning", label: "Busy" };
  if (s === "no_answer") return { cls: "pill-warning", label: "No answer" };
  if (s === "in_progress") return { cls: "pill-accent", label: "In progress" };
  if (s === "ringing") return { cls: "pill-accent", label: "Ringing" };
  return { cls: "pill-success", label: "Completed" };
}

const ACTIVE_CALL_STATUSES = new Set(["in_progress", "ringing"]);
const FAILED_CALL_STATUSES = new Set(["failed", "busy", "no_answer"]);

function durationLabel(msgs: import("@/lib/api").Message[]): string {
  if (msgs.length < 2) return "< 1 min";
  const first = new Date(msgs[0].created_at).getTime();
  const last = new Date(msgs[msgs.length - 1].created_at).getTime();
  const secs = Math.floor((last - first) / 1000);
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60), s = secs % 60;
  return s > 0 ? `${m}m ${s}s` : `${m} min`;
}

function downloadTranscript(conv: ConvDetail, msgs: import("@/lib/api").Message[]) {
  const lines = [
    `Session: ${conv.title}`,
    `Date: ${new Date(conv.created_at).toLocaleString()}`,
    `Duration: ${durationLabel(msgs)}`,
    `Call status: ${callStatusPill(conv.call_status).label}`,
    ...(conv.failure_reason ? [`Failure reason: ${conv.failure_reason}`] : []),
    `Sentiment: ${conv.sentiment ?? "N/A"}`,
    `Emotion: ${conv.dominant_emotion ?? "N/A"}`,
    "",
    "Transcript",
    "",
    ...msgs.map(m => `[${m.role === "user" ? "User" : "Agent"}] ${m.content}`),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `session-${conv.id.slice(0, 8)}.txt`;
  a.click(); URL.revokeObjectURL(url);
}

function SuccessRing({ pct }: { pct: number }) {
  const r = 18, c = 2 * Math.PI * r;
  const offset = c * (1 - Math.min(100, Math.max(0, pct)) / 100);
  return (
    <svg width="44" height="44" viewBox="0 0 44 44">
      <circle cx="22" cy="22" r={r} fill="none" stroke="var(--surface-2)" strokeWidth="5" />
      <circle cx="22" cy="22" r={r} fill="none" stroke="var(--success)" strokeWidth="5" strokeLinecap="round"
        transform="rotate(-90 22 22)" strokeDasharray={c} strokeDashoffset={offset} style={{ transition: "stroke-dashoffset .5s ease" }} />
    </svg>
  );
}

function SessionDetail({ conv, agents, onBack }: {
  conv: ConvDetail;
  agents: Agent[];
  onBack: () => void;
}) {
  const [msgs, setMsgs] = useState<import("@/lib/api").Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [qa, setQa] = useState<import("@/lib/api").ConversationQA | null>(null);
  const agentName = agents.find(a => a.id === conv.agent_id)?.name ?? "Voice Agent";
  const sPill = sentimentPill(conv.sentiment);
  const cPill = callStatusPill(conv.call_status);
  const failed = FAILED_CALL_STATUSES.has(conv.call_status ?? "");

  useEffect(() => {
    API.messages(conv.id).then(m => { setMsgs(m); setLoading(false); }).catch(() => setLoading(false));
    API.conversationQA(conv.id).then(setQa).catch(() => setQa(null));
  }, [conv.id]);

  return (
    <div className="view">
      <button className="back-link" onClick={onBack}><Icon name="arrow-left" />Call Analytics</button>
      <div className="view-head">
        <div><h1 className="view-title" style={{ fontSize: 22 }}>{conv.title}</h1></div>
        <div className="view-actions">
          <button className="btn btn-ghost" onClick={() => downloadTranscript(conv, msgs)}><Icon name="copy" />Download transcript</button>
        </div>
      </div>

      {failed && (
        <div className="fail-banner">
          <Icon name="x" />
          <div>
            <b>Call {cPill.label.toLowerCase()}</b>
            <p>{conv.failure_reason || "No further details were recorded for this call."}</p>
          </div>
        </div>
      )}

      <div className="session-layout">
        <div className="session-main">
          {conv.summary && (
            <div className="conv-card">
              <div className="conv-card-head"><h3>Call summary</h3></div>
              <div className="conv-card-body">
                <p style={{ fontSize: 14, lineHeight: 1.5 }}>{conv.summary}</p>
              </div>
            </div>
          )}

          {qa && qa.questions.length > 0 && (
            <div className="conv-card">
              <div className="conv-card-head">
                <h3>Questions asked</h3>
                <span className="conv-card-meta">
                  {qa.questions.filter(q => q.answered).length}/{qa.questions.length} answered
                </span>
              </div>
              <div className="conv-card-body">
                <div className="detail-rows">
                  {qa.questions.map((q, i) => (
                    <div key={i} className="detail-row" style={{ alignItems: "flex-start" }}>
                      <Icon name={q.answered ? "check-circle" : "x"} />
                      <div>
                        <div className="detail-label">{q.question}</div>
                        <div className="detail-value">
                          {q.answered ? q.answer : <em style={{ color: "var(--ink-faint)" }}>Not answered</em>}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="conv-card accent">
            <div className="conv-card-head">
              <h3>Conversation</h3>
              {!loading && <span className="conv-card-meta">{msgs.length} message{msgs.length !== 1 ? "s" : ""}</span>}
            </div>
            <div className="conv-card-body">
              {loading ? (
                <div style={{ color: "var(--ink-faint)", fontSize: 14 }}>Loading transcript...</div>
              ) : msgs.length === 0 ? (
                <div className="empty"><p>No messages in this session.</p></div>
              ) : (
                <div className="transcript">
                  {msgs.map((m, i) => (
                    <div key={i} className={`bubble-row ${m.role === "user" ? "user" : "agent"}`}>
                      <div className="bubble-col">
                        <div className="msg-meta">
                          {m.role === "user" ? (
                            <>
                              <span className="msg-name">Caller</span>
                              <span>{new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                              <span className="avatar avatar-sm hue-indigo">C</span>
                            </>
                          ) : (
                            <>
                              <span className="avatar avatar-sm hue-teal">{agentInitials(agentName)}</span>
                              <span className="msg-name">{agentName}</span>
                              <span>{new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                            </>
                          )}
                        </div>
                        <div className="bubble">{m.content}</div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="session-sidebar">
          <div className="conv-card">
            <div className="conv-card-head"><h3>Details</h3></div>
            <div className="detail-rows">
              <div className="detail-row">
                <Icon name="headphones" />
                <div><div className="detail-label">Agent</div><div className="detail-value">{agentName}</div></div>
              </div>
              <div className="detail-row">
                <Icon name="calendar" />
                <div><div className="detail-label">Time</div><div className="detail-value">{new Date(conv.created_at).toLocaleString()}</div></div>
              </div>
              {!loading && msgs.length > 0 && (
                <div className="detail-row">
                  <Icon name="clock" />
                  <div><div className="detail-label">Duration</div><div className="detail-value">{durationLabel(msgs)}</div></div>
                </div>
              )}
              <div className="detail-row">
                <Icon name="check-circle" />
                <div><div className="detail-label">Status</div><div><span className={`pill ${cPill.cls}`}>{cPill.label}</span></div></div>
              </div>
              {conv.sentiment && (
                <div className="detail-row">
                  <Icon name="activity" />
                  <div><div className="detail-label">Sentiment</div><div><span className={`pill ${sPill.cls}`}>{sPill.label}{conv.dominant_emotion ? ` · ${conv.dominant_emotion}` : ""}</span></div></div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

type CallFilter = "all" | "completed" | "failed";

function SessionsView({ agents }: { agents: Agent[] }) {
  const [convs, setConvs] = useState<ConvDetail[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<ConvDetail | null>(null);
  const [filter, setFilter] = useState<CallFilter>("all");
  const [agentFilter, setAgentFilter] = useState<string>("all");

  useEffect(() => {
    API.conversations().then(data => { setConvs(data as ConvDetail[]); setLoading(false); }).catch(() => setLoading(false));
  }, []);

  if (selected) {
    return <SessionDetail conv={selected} agents={agents} onBack={() => setSelected(null)} />;
  }

  const total = convs.length;
  const failedCount = convs.filter(c => FAILED_CALL_STATUSES.has(c.call_status ?? "")).length;
  const completedCount = convs.filter(c => (c.call_status ?? "completed") === "completed").length;
  const inProgressCount = convs.filter(c => ACTIVE_CALL_STATUSES.has(c.call_status ?? "")).length;
  const successRate = total > 0 ? Math.round((completedCount / total) * 100) : 0;

  const filtered = convs.filter(c => {
    if (agentFilter !== "all" && c.agent_id !== agentFilter) return false;
    if (filter === "failed") return FAILED_CALL_STATUSES.has(c.call_status ?? "");
    if (filter === "completed") return (c.call_status ?? "completed") === "completed";
    return true;
  });

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1 className="view-title">Call Analytics</h1>
          <p className="view-sub">Every voice conversation: outcomes, sentiment, and why calls fail.</p>
        </div>
      </div>

      {!loading && total > 0 && (
        <div className="stat-grid">
          <StatCard label="Total calls" value={total} icon="phone" tone="accent" />
          <StatCard label="Completed" value={completedCount} icon="check-circle" tone="success" />
          <StatCard label="Failed / busy / no answer" value={failedCount} icon="x" tone="danger" />
          <div className="stat-card">
            <div className="stat-top"><span className="stat-label">Success rate</span></div>
            <div className="stat-ring-wrap"><SuccessRing pct={successRate} /><div className="stat-value">{successRate}%</div></div>
          </div>
        </div>
      )}

      {!loading && total > 0 && (
        <div className="filter-row">
          <div className="segmented">
            {([["all", "All"], ["completed", "Completed"], ["failed", "Failed"]] as [CallFilter, string][]).map(([key, label]) => (
              <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>
            ))}
          </div>
          {agents.length > 0 && (
            <select className="select" style={{ width: "auto" }} value={agentFilter} onChange={e => setAgentFilter(e.target.value)}>
              <option value="all">All agents</option>
              {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
          {inProgressCount > 0 && <span className="pill pill-accent">{inProgressCount} in progress</span>}
        </div>
      )}

      {loading ? (
        <div style={{ color: "var(--ink-faint)", fontSize: 14 }}>Loading call analytics...</div>
      ) : convs.length === 0 ? (
        <div className="empty"><div className="ic"><Icon name="activity" /></div><p>No calls yet. Start a voice conversation with an agent.</p></div>
      ) : filtered.length === 0 ? (
        <div className="empty"><div className="ic"><Icon name="activity" /></div><p>No calls match this filter.</p></div>
      ) : (
        <div className="call-grid">
          {filtered.map(conv => {
            const sPill = sentimentPill(conv.sentiment);
            const cPill = callStatusPill(conv.call_status);
            const isFailed = FAILED_CALL_STATUSES.has(conv.call_status ?? "");
            const agentName = agents.find(a => a.id === conv.agent_id)?.name;
            return (
              <div key={conv.id} className={`call-card ${isFailed ? "bad" : "ok"}`} onClick={() => setSelected(conv)}>
                <div className="call-card-top">
                  <div className="call-ic"><Icon name={isFailed ? "x" : "headphones"} /></div>
                  <span className={`pill ${cPill.cls}`}>{cPill.label}</span>
                </div>
                <div className="call-title">{conv.title}</div>
                {agentName && <div className="call-agent">{agentName}</div>}
                {conv.sentiment && (
                  <span className={`pill ${sPill.cls}`} style={{ marginTop: 6, display: "inline-flex" }}>
                    {sPill.label}{conv.dominant_emotion ? ` · ${conv.dominant_emotion}` : ""}
                  </span>
                )}
                {isFailed && <div className="call-fail-reason">{conv.failure_reason || "No further details recorded."}</div>}
                <div className="call-foot"><span className="call-time">{timeAgo(conv.created_at)}</span></div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Stat card
--------------------------------------------------------------------------- */

function toneVars(tone: string): { fg: string; bg: string } {
  if (tone === "ink") return { fg: "var(--ink)", bg: "var(--surface-2)" };
  return { fg: `var(--${tone})`, bg: `var(--${tone}-soft)` };
}

function StatCard({ label, value, icon, tone = "accent" }: { label: string; value: React.ReactNode; icon: string; tone?: string }) {
  const { fg, bg } = toneVars(tone);
  return (
    <div className="stat-card">
      <div className="stat-top">
        <span className="stat-label">{label}</span>
        <div className="stat-icon" style={{ background: bg, color: fg }}><Icon name={icon} /></div>
      </div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Agents grid
--------------------------------------------------------------------------- */

function AgentsGrid({ agents, query, onSelect, onToggle, onCreateClick }: {
  agents: Agent[];
  query: string;
  onSelect: (a: Agent) => void;
  onToggle: (id: string, active: boolean) => Promise<void>;
  onCreateClick: () => void;
}) {
  const filtered = query.trim()
    ? agents.filter(a => a.name.toLowerCase().includes(query.trim().toLowerCase()))
    : agents;

  const liveCount = agents.filter(a => a.is_active).length;
  const updatedTodayCount = agents.filter(a => isToday(a.updated_at)).length;

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1 className="view-title">Agents</h1>
          <p className="view-sub">Build and manage the voice agents answering your calls.</p>
        </div>
        <div className="view-actions">
          <button className="btn btn-primary" onClick={onCreateClick}><Icon name="plus" />New agent</button>
        </div>
      </div>

      <div className="stat-grid" style={{ gridTemplateColumns: "repeat(3,1fr)" }}>
        <StatCard label="Total agents" value={agents.length} icon="grid" tone="accent" />
        <StatCard label="Live now" value={liveCount} icon="activity" tone="success" />
        <StatCard label="Updated today" value={updatedTodayCount} icon="clock" tone="teal" />
      </div>

      {filtered.length === 0 && query.trim() ? (
        <div className="empty"><p>No agents match &quot;{query}&quot;</p></div>
      ) : (
        <div className="agent-grid">
          {filtered.map(agent => (
            <AgentCard key={agent.id} agent={agent}
              onClick={() => onSelect(agent)}
              onToggle={active => onToggle(agent.id, active)}
            />
          ))}
          {!query.trim() && <CreateCard onClick={onCreateClick} />}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Phone Numbers view
--------------------------------------------------------------------------- */

interface TwilioAccountDraft { label: string; account_sid: string; auth_token: string; webhook_base_url: string; }

const emptyAccountDraft = (): TwilioAccountDraft => ({ label: "", account_sid: "", auth_token: "", webhook_base_url: "" });

function PhoneNumbersView({ agents }: { agents: Agent[] }) {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [addPhone, setAddPhone] = useState("");
  const [addName, setAddName] = useState("");
  const [addAccountId, setAddAccountId] = useState("");
  const [addError, setAddError] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  const [accounts, setAccounts] = useState<TwilioAccount[]>([]);
  const [openAccountId, setOpenAccountId] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, TwilioAccountDraft>>({});
  const [savingAccountId, setSavingAccountId] = useState<string | null>(null);
  const [savedAccountId, setSavedAccountId] = useState<string | null>(null);
  const [newAccount, setNewAccount] = useState<TwilioAccountDraft | null>(null);

  // Plivo - same shape as the Twilio state above, kept separate so the
  // proven Twilio code path above is never touched by this addition.
  const [plivoNumbers, setPlivoNumbers] = useState<PlivoPhoneNumber[]>([]);
  const [showAddPlivo, setShowAddPlivo] = useState(false);
  const [addPlivoPhone, setAddPlivoPhone] = useState("");
  const [addPlivoName, setAddPlivoName] = useState("");
  const [addPlivoAccountId, setAddPlivoAccountId] = useState("");
  const [addPlivoError, setAddPlivoError] = useState("");
  const [savingPlivo, setSavingPlivo] = useState(false);
  const [editingPlivoId, setEditingPlivoId] = useState<string | null>(null);
  const [editPlivoName, setEditPlivoName] = useState("");

  const [plivoAccounts, setPlivoAccounts] = useState<PlivoAccount[]>([]);
  const [openPlivoAccountId, setOpenPlivoAccountId] = useState<string | null>(null);
  const [plivoDrafts, setPlivoDrafts] = useState<Record<string, TwilioAccountDraft>>({});
  const [savingPlivoAccountId, setSavingPlivoAccountId] = useState<string | null>(null);
  const [savedPlivoAccountId, setSavedPlivoAccountId] = useState<string | null>(null);
  const [newPlivoAccount, setNewPlivoAccount] = useState<TwilioAccountDraft | null>(null);

  const draftFromAccount = (a: TwilioAccount): TwilioAccountDraft =>
    ({ label: a.label, account_sid: a.account_sid, auth_token: a.auth_token, webhook_base_url: a.webhook_base_url });

  const draftFromPlivoAccount = (a: PlivoAccount): TwilioAccountDraft =>
    ({ label: a.label, account_sid: a.auth_id, auth_token: a.auth_token, webhook_base_url: a.webhook_base_url });

  const load = async () => {
    setLoading(true);
    try {
      const [nums, accs, plivoNums, plivoAccs] = await Promise.all([
        API.phoneNumbers(),
        API.twilioAccounts(),
        API.plivoPhoneNumbers(),
        API.plivoAccounts(),
      ]);
      setNumbers(nums);
      setAccounts(accs);
      const draftMap: Record<string, TwilioAccountDraft> = {};
      accs.forEach(a => { draftMap[a.id] = draftFromAccount(a); });
      setDrafts(draftMap);
      if (accs.length === 0) setNewAccount(emptyAccountDraft());

      setPlivoNumbers(plivoNums);
      setPlivoAccounts(plivoAccs);
      const plivoDraftMap: Record<string, TwilioAccountDraft> = {};
      plivoAccs.forEach(a => { plivoDraftMap[a.id] = draftFromPlivoAccount(a); });
      setPlivoDrafts(plivoDraftMap);
      if (plivoAccs.length === 0) setNewPlivoAccount(emptyAccountDraft());
    } catch { /* ignore */ }
    setLoading(false);
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, []);

  const updateDraft = (accountId: string, patch: Partial<TwilioAccountDraft>) =>
    setDrafts(d => ({ ...d, [accountId]: { ...d[accountId], ...patch } }));

  const handleSaveAccount = async (account: TwilioAccount) => {
    const draft = drafts[account.id];
    if (!draft) return;
    setSavingAccountId(account.id);
    try {
      const updated = await API.updateTwilioAccount(account.id, draft);
      setAccounts(prev => prev.map(a => a.id === account.id ? updated : a));
      setDrafts(d => ({ ...d, [account.id]: draftFromAccount(updated) }));
      setSavedAccountId(account.id); pushToast("Twilio account saved");
      setTimeout(() => setSavedAccountId(null), 2000);
    } catch { /* ignore */ }
    setSavingAccountId(null);
  };

  const handleCreateAccount = async () => {
    if (!newAccount) return;
    setSavingAccountId("new");
    try {
      const created = await API.addTwilioAccount(newAccount);
      setAccounts(prev => [...prev, created]);
      setDrafts(d => ({ ...d, [created.id]: draftFromAccount(created) }));
      setNewAccount(null);
      pushToast("Twilio account added");
    } catch { /* ignore */ }
    setSavingAccountId(null);
  };

  const handleSetDefaultAccount = async (account: TwilioAccount) => {
    try {
      const updated = await API.setDefaultTwilioAccount(account.id);
      setAccounts(prev => prev.map(a => a.id === updated.id ? updated : { ...a, is_default: false }));
      pushToast(`${updated.label} is now the default account`);
    } catch { /* ignore */ }
  };

  const handleDeleteAccount = async (account: TwilioAccount) => {
    const attached = numbers.filter(n => n.twilio_account_id === account.id).length;
    const msg = attached > 0
      ? `Delete "${account.label}"? ${attached} phone number${attached > 1 ? "s" : ""} using it will fall back to the default account.`
      : `Delete "${account.label}"?`;
    if (!confirm(msg)) return;
    try {
      await API.deleteTwilioAccount(account.id);
      setAccounts(prev => prev.filter(a => a.id !== account.id));
      setNumbers(prev => prev.map(n => n.twilio_account_id === account.id ? { ...n, twilio_account_id: null } : n));
      pushToast("Twilio account removed");
    } catch { /* ignore */ }
  };

  const handleAdd = async () => {
    if (!addPhone.trim()) { setAddError("Phone number is required"); return; }
    setSaving(true); setAddError("");
    try {
      const n = await API.addPhoneNumber({
        phone_number: addPhone.trim(), friendly_name: addName.trim() || undefined,
        twilio_account_id: addAccountId || undefined,
      });
      setNumbers(prev => [...prev, n]);
      setAddPhone(""); setAddName(""); setAddAccountId(""); setShowAdd(false);
      pushToast("Number added");
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Failed to add");
    }
    setSaving(false);
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this phone number?")) return;
    await API.deletePhoneNumber(id);
    setNumbers(prev => prev.filter(n => n.id !== id));
    pushToast("Number removed");
  };

  const handleAttach = async (numberId: string, agentId: string | null) => {
    const updated = agentId ? await API.attachPhoneNumber(numberId, agentId) : await API.detachPhoneNumber(numberId);
    setNumbers(prev => prev.map(n => n.id === numberId ? updated : n));
    const a = agents.find(x => x.id === agentId);
    pushToast(a ? `Connected to ${a.name}` : "Disconnected");
  };

  const handleAccountAssign = async (numberId: string, accountId: string) => {
    const updated = await API.updatePhoneNumber(numberId, { twilio_account_id: accountId });
    setNumbers(prev => prev.map(n => n.id === numberId ? updated : n));
    pushToast(accountId ? "Phone number reassigned" : "Using default Twilio account");
  };

  const handleEditName = async (id: string) => {
    try {
      const updated = await API.updatePhoneNumber(id, { friendly_name: editName.trim() || undefined });
      setNumbers(prev => prev.map(n => n.id === id ? updated : n));
      setEditingId(null);
    } catch { /* ignore */ }
  };

  // --- Plivo handlers - mirror the Twilio ones above ---

  const updatePlivoDraft = (accountId: string, patch: Partial<TwilioAccountDraft>) =>
    setPlivoDrafts(d => ({ ...d, [accountId]: { ...d[accountId], ...patch } }));

  const handleSavePlivoAccount = async (account: PlivoAccount) => {
    const draft = plivoDrafts[account.id];
    if (!draft) return;
    setSavingPlivoAccountId(account.id);
    try {
      const updated = await API.updatePlivoAccount(account.id, {
        label: draft.label, auth_id: draft.account_sid, auth_token: draft.auth_token, webhook_base_url: draft.webhook_base_url,
      });
      setPlivoAccounts(prev => prev.map(a => a.id === account.id ? updated : a));
      setPlivoDrafts(d => ({ ...d, [account.id]: draftFromPlivoAccount(updated) }));
      setSavedPlivoAccountId(account.id); pushToast("Plivo account saved");
      setTimeout(() => setSavedPlivoAccountId(null), 2000);
    } catch { /* ignore */ }
    setSavingPlivoAccountId(null);
  };

  const handleCreatePlivoAccount = async () => {
    if (!newPlivoAccount) return;
    setSavingPlivoAccountId("new");
    try {
      const created = await API.addPlivoAccount({
        label: newPlivoAccount.label, auth_id: newPlivoAccount.account_sid,
        auth_token: newPlivoAccount.auth_token, webhook_base_url: newPlivoAccount.webhook_base_url,
      });
      setPlivoAccounts(prev => [...prev, created]);
      setPlivoDrafts(d => ({ ...d, [created.id]: draftFromPlivoAccount(created) }));
      setNewPlivoAccount(null);
      pushToast("Plivo account added");
    } catch { /* ignore */ }
    setSavingPlivoAccountId(null);
  };

  const handleSetDefaultPlivoAccount = async (account: PlivoAccount) => {
    try {
      const updated = await API.setDefaultPlivoAccount(account.id);
      setPlivoAccounts(prev => prev.map(a => a.id === updated.id ? updated : { ...a, is_default: false }));
      pushToast(`${updated.label} is now the default account`);
    } catch { /* ignore */ }
  };

  const handleDeletePlivoAccount = async (account: PlivoAccount) => {
    const attached = plivoNumbers.filter(n => n.plivo_account_id === account.id).length;
    const msg = attached > 0
      ? `Delete "${account.label}"? ${attached} phone number${attached > 1 ? "s" : ""} using it will fall back to the default account.`
      : `Delete "${account.label}"?`;
    if (!confirm(msg)) return;
    try {
      await API.deletePlivoAccount(account.id);
      setPlivoAccounts(prev => prev.filter(a => a.id !== account.id));
      setPlivoNumbers(prev => prev.map(n => n.plivo_account_id === account.id ? { ...n, plivo_account_id: null } : n));
      pushToast("Plivo account removed");
    } catch { /* ignore */ }
  };

  const handleAddPlivo = async () => {
    if (!addPlivoPhone.trim()) { setAddPlivoError("Phone number is required"); return; }
    setSavingPlivo(true); setAddPlivoError("");
    try {
      const n = await API.addPlivoPhoneNumber({
        phone_number: addPlivoPhone.trim(), friendly_name: addPlivoName.trim() || undefined,
        plivo_account_id: addPlivoAccountId || undefined,
      });
      setPlivoNumbers(prev => [...prev, n]);
      setAddPlivoPhone(""); setAddPlivoName(""); setAddPlivoAccountId(""); setShowAddPlivo(false);
      pushToast("Number added");
    } catch (e: unknown) {
      setAddPlivoError(e instanceof Error ? e.message : "Failed to add");
    }
    setSavingPlivo(false);
  };

  const handleDeletePlivo = async (id: string) => {
    if (!confirm("Delete this phone number?")) return;
    await API.deletePlivoPhoneNumber(id);
    setPlivoNumbers(prev => prev.filter(n => n.id !== id));
    pushToast("Number removed");
  };

  const handleAttachPlivo = async (numberId: string, agentId: string | null) => {
    const updated = agentId ? await API.attachPlivoPhoneNumber(numberId, agentId) : await API.detachPlivoPhoneNumber(numberId);
    setPlivoNumbers(prev => prev.map(n => n.id === numberId ? updated : n));
    const a = agents.find(x => x.id === agentId);
    pushToast(a ? `Connected to ${a.name}` : "Disconnected");
  };

  const handlePlivoAccountAssign = async (numberId: string, accountId: string) => {
    const updated = await API.updatePlivoPhoneNumber(numberId, { plivo_account_id: accountId });
    setPlivoNumbers(prev => prev.map(n => n.id === numberId ? updated : n));
    pushToast(accountId ? "Phone number reassigned" : "Using default Plivo account");
  };

  const handleEditPlivoName = async (id: string) => {
    try {
      const updated = await API.updatePlivoPhoneNumber(id, { friendly_name: editPlivoName.trim() || undefined });
      setPlivoNumbers(prev => prev.map(n => n.id === id ? updated : n));
      setEditingPlivoId(null);
    } catch { /* ignore */ }
  };

  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h1 className="view-title">Phone Numbers</h1>
          <p className="view-sub">Connect a number to put any agent on the phone.</p>
        </div>
        <div className="view-actions">
          <button className="btn btn-ghost" onClick={() => setShowAddPlivo(true)}><Icon name="plus" />Add Plivo number</button>
          <button className="btn btn-primary" onClick={() => setShowAdd(true)}><Icon name="plus" />Add Twilio number</button>
        </div>
      </div>

      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-faint)", textTransform: "uppercase", letterSpacing: 0.4, marginBottom: 8 }}>
        Twilio
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
        {accounts.map(account => {
          const draft = drafts[account.id] ?? draftFromAccount(account);
          const isOpen = openAccountId === account.id;
          return (
            <div key={account.id} className={`card provider-card collapsible${isOpen ? " open" : ""}`}>
              <button className="provider-head" onClick={() => setOpenAccountId(v => v === account.id ? null : account.id)}>
                <div className="provider-ic">T</div>
                <div style={{ flex: 1 }}>
                  <div className="nm">{account.label || "Twilio account"}</div>
                </div>
                {account.is_default && <span className="pill">Default</span>}
                {account.account_sid
                  ? <span className="pill pill-success"><span className="dot" />Connected</span>
                  : <span className="pill pill-warning">Not configured</span>}
                <Icon name="chevron" className="chv" />
              </button>
              <div className="cv" style={{ padding: "0 18px 18px" }}>
                <div className="field">
                  <label>Label</label>
                  <input className="input" placeholder="e.g. Client A, Sandbox" value={draft.label}
                    onChange={e => updateDraft(account.id, { label: e.target.value })} />
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
                  <div className="field">
                    <label>Account SID</label>
                    <input className="input" placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" value={draft.account_sid}
                      onChange={e => updateDraft(account.id, { account_sid: e.target.value })} />
                  </div>
                  <div className="field">
                    <label>Auth token</label>
                    <input className="input" type="password" placeholder={account.auth_token_set ? "••••••••" : "Enter auth token"}
                      value={draft.auth_token} onChange={e => updateDraft(account.id, { auth_token: e.target.value })} />
                  </div>
                </div>
                <div className="field">
                  <label>Server base URL <span style={{ fontWeight: 400, color: "var(--ink-faint)" }}>(public URL of this server, use ngrok for local testing)</span></label>
                  <input className="input" placeholder="https://your-server.com" value={draft.webhook_base_url}
                    onChange={e => updateDraft(account.id, { webhook_base_url: e.target.value })} />
                  {draft.webhook_base_url && (
                    <div style={{ marginTop: 6, fontSize: 11, color: "var(--ink-muted)" }}>
                      Set this as the Voice webhook in the Twilio console (HTTP POST):&nbsp;
                      <code style={{ background: "var(--surface-2)", padding: "1px 5px", borderRadius: 4, fontFamily: "var(--font-mono)" }}>
                        {draft.webhook_base_url}/twilio/voice
                      </code>
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <button className="btn btn-primary" onClick={() => handleSaveAccount(account)} disabled={savingAccountId === account.id}>
                    {savingAccountId === account.id ? "Saving..." : savedAccountId === account.id ? "Saved" : "Save"}
                  </button>
                  {!account.is_default && (
                    <button className="btn btn-ghost" onClick={() => handleSetDefaultAccount(account)}>Set as default</button>
                  )}
                  <button className="btn btn-danger" style={{ marginLeft: "auto" }} onClick={() => handleDeleteAccount(account)}>
                    <Icon name="trash" size={13} />Delete
                  </button>
                </div>
              </div>
            </div>
          );
        })}

        {newAccount ? (
          <div className="card provider-card collapsible open">
            <div className="provider-head" style={{ cursor: "default" }}>
              <div className="provider-ic">T</div>
              <div style={{ flex: 1 }}><div className="nm">New Twilio account</div></div>
            </div>
            <div className="cv" style={{ padding: "0 18px 18px" }}>
              <div className="field">
                <label>Label</label>
                <input className="input" placeholder="e.g. Client A, Sandbox" value={newAccount.label}
                  onChange={e => setNewAccount(d => d && ({ ...d, label: e.target.value }))} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
                <div className="field">
                  <label>Account SID</label>
                  <input className="input" placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" value={newAccount.account_sid}
                    onChange={e => setNewAccount(d => d && ({ ...d, account_sid: e.target.value }))} />
                </div>
                <div className="field">
                  <label>Auth token</label>
                  <input className="input" type="password" placeholder="Enter auth token" value={newAccount.auth_token}
                    onChange={e => setNewAccount(d => d && ({ ...d, auth_token: e.target.value }))} />
                </div>
              </div>
              <div className="field">
                <label>Server base URL <span style={{ fontWeight: 400, color: "var(--ink-faint)" }}>(public URL of this server, use ngrok for local testing)</span></label>
                <input className="input" placeholder="https://your-server.com" value={newAccount.webhook_base_url}
                  onChange={e => setNewAccount(d => d && ({ ...d, webhook_base_url: e.target.value }))} />
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <button className="btn btn-primary" onClick={handleCreateAccount} disabled={savingAccountId === "new"}>
                  {savingAccountId === "new" ? "Adding..." : "Add account"}
                </button>
                {accounts.length > 0 && (
                  <button className="btn btn-ghost" onClick={() => setNewAccount(null)}>Cancel</button>
                )}
              </div>
            </div>
          </div>
        ) : (
          <button className="btn btn-ghost" style={{ alignSelf: "flex-start" }}
            onClick={() => setNewAccount({ ...emptyAccountDraft(), webhook_base_url: accounts.find(a => a.is_default)?.webhook_base_url ?? "" })}>
            <Icon name="plus" />Add Twilio account
          </button>
        )}
      </div>

      {loading ? (
        <div style={{ color: "var(--ink-faint)", fontSize: 14 }}>Loading...</div>
      ) : numbers.length === 0 ? (
        <div className="empty"><div className="ic"><Icon name="phone" /></div><p>No phone numbers yet.</p><p>Add a Twilio number to connect it to an agent.</p></div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {numbers.map(num => {
            const attachedAgent = agents.find(a => a.id === num.agent_id);
            const isEditing = editingId === num.id;
            return (
              <div key={num.id} className="card number-row" style={{ borderTop: "none" }}>
                <div className="number-ic"><Icon name="phone" /></div>
                <div className="number-info">
                  {isEditing ? (
                    <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                      <input className="input" style={{ width: 180 }} value={editName} onChange={e => setEditName(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter") handleEditName(num.id); if (e.key === "Escape") setEditingId(null); }} autoFocus />
                      <button className="btn btn-primary btn-sm" onClick={() => handleEditName(num.id)}>Save</button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditingId(null)}>Cancel</button>
                    </div>
                  ) : (
                    <div className="nm">
                      {num.friendly_name || num.phone_number}
                      {num.friendly_name && <span className="num">{num.phone_number}</span>}
                      <button className="icon-btn" style={{ width: 24, height: 24, border: "none", background: "none" }}
                        onClick={() => { setEditingId(num.id); setEditName(num.friendly_name ?? ""); }} title="Edit name">
                        <Icon name="edit" size={12} />
                      </button>
                    </div>
                  )}
                  <div className="sub">{attachedAgent ? <>Connected to <b>{attachedAgent.name}</b></> : "Not connected to any agent"}</div>
                </div>
                <div className="number-actions">
                  {accounts.length > 1 && (
                    <select className="select" style={{ width: "auto" }} value={num.twilio_account_id ?? ""}
                      onChange={e => handleAccountAssign(num.id, e.target.value)} title="Twilio account">
                      <option value="">Default account</option>
                      {accounts.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
                    </select>
                  )}
                  <select className="select" style={{ width: "auto" }} value={num.agent_id ?? ""} onChange={e => handleAttach(num.id, e.target.value || null)}>
                    <option value="">No agent</option>
                    {agents.filter(a => a.is_active).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                  <button className="btn btn-danger btn-sm" onClick={() => handleDelete(num.id)}><Icon name="trash" size={13} />Delete</button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showAdd && (
        <div className="modal-backdrop" onClick={() => setShowAdd(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-head"><h3>Add a number</h3><button className="icon-btn" onClick={() => setShowAdd(false)}><Icon name="x" /></button></div>
            <div className="modal-body">
              <div className="field">
                <label>Phone number (E.164 format) *</label>
                <input className="input" placeholder="+12025550101" value={addPhone} onChange={e => setAddPhone(e.target.value)} />
              </div>
              <div className="field" style={{ marginBottom: accounts.length > 1 ? 14 : 0 }}>
                <label>Friendly name (optional)</label>
                <input className="input" placeholder="e.g. Support Line" value={addName} onChange={e => setAddName(e.target.value)} />
              </div>
              {accounts.length > 1 && (
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Twilio account</label>
                  <select className="select" value={addAccountId} onChange={e => setAddAccountId(e.target.value)}>
                    <option value="">Default account</option>
                    {accounts.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
                  </select>
                </div>
              )}
              {addError && <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 10 }}>{addError}</div>}
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={() => { setShowAdd(false); setAddPhone(""); setAddName(""); setAddAccountId(""); setAddError(""); }}>Cancel</button>
              <button className="btn btn-primary" onClick={handleAdd} disabled={saving}><Icon name="plus" />{saving ? "Adding..." : "Add number"}</button>
            </div>
          </div>
        </div>
      )}

      {/* --- Plivo - separate section, same patterns as Twilio above --- */}

      <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-faint)", textTransform: "uppercase", letterSpacing: 0.4, margin: "28px 0 8px" }}>
        Plivo
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 14 }}>
        {plivoAccounts.map(account => {
          const draft = plivoDrafts[account.id] ?? draftFromPlivoAccount(account);
          const isOpen = openPlivoAccountId === account.id;
          return (
            <div key={account.id} className={`card provider-card collapsible${isOpen ? " open" : ""}`}>
              <button className="provider-head" onClick={() => setOpenPlivoAccountId(v => v === account.id ? null : account.id)}>
                <div className="provider-ic">P</div>
                <div style={{ flex: 1 }}>
                  <div className="nm">{account.label || "Plivo account"}</div>
                </div>
                {account.is_default && <span className="pill">Default</span>}
                {account.auth_id
                  ? <span className="pill pill-success"><span className="dot" />Connected</span>
                  : <span className="pill pill-warning">Not configured</span>}
                <Icon name="chevron" className="chv" />
              </button>
              <div className="cv" style={{ padding: "0 18px 18px" }}>
                <div className="field">
                  <label>Label</label>
                  <input className="input" placeholder="e.g. Client A, Sandbox" value={draft.label}
                    onChange={e => updatePlivoDraft(account.id, { label: e.target.value })} />
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
                  <div className="field">
                    <label>Auth ID</label>
                    <input className="input" placeholder="MAXXXXXXXXXXXXXXXXXXXX" value={draft.account_sid}
                      onChange={e => updatePlivoDraft(account.id, { account_sid: e.target.value })} />
                  </div>
                  <div className="field">
                    <label>Auth token</label>
                    <input className="input" type="password" placeholder={account.auth_token_set ? "••••••••" : "Enter auth token"}
                      value={draft.auth_token} onChange={e => updatePlivoDraft(account.id, { auth_token: e.target.value })} />
                  </div>
                </div>
                <div className="field">
                  <label>Server base URL <span style={{ fontWeight: 400, color: "var(--ink-faint)" }}>(public URL of this server, use ngrok for local testing)</span></label>
                  <input className="input" placeholder="https://your-server.com" value={draft.webhook_base_url}
                    onChange={e => updatePlivoDraft(account.id, { webhook_base_url: e.target.value })} />
                  {draft.webhook_base_url && (
                    <div style={{ marginTop: 6, fontSize: 11, color: "var(--ink-muted)" }}>
                      We auto-configure the Plivo Application&apos;s answer URL for you:&nbsp;
                      <code style={{ background: "var(--surface-2)", padding: "1px 5px", borderRadius: 4, fontFamily: "var(--font-mono)" }}>
                        {draft.webhook_base_url}/plivo/voice
                      </code>
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <button className="btn btn-primary" onClick={() => handleSavePlivoAccount(account)} disabled={savingPlivoAccountId === account.id}>
                    {savingPlivoAccountId === account.id ? "Saving..." : savedPlivoAccountId === account.id ? "Saved" : "Save"}
                  </button>
                  {!account.is_default && (
                    <button className="btn btn-ghost" onClick={() => handleSetDefaultPlivoAccount(account)}>Set as default</button>
                  )}
                  <button className="btn btn-danger" style={{ marginLeft: "auto" }} onClick={() => handleDeletePlivoAccount(account)}>
                    <Icon name="trash" size={13} />Delete
                  </button>
                </div>
              </div>
            </div>
          );
        })}

        {newPlivoAccount ? (
          <div className="card provider-card collapsible open">
            <div className="provider-head" style={{ cursor: "default" }}>
              <div className="provider-ic">P</div>
              <div style={{ flex: 1 }}><div className="nm">New Plivo account</div></div>
            </div>
            <div className="cv" style={{ padding: "0 18px 18px" }}>
              <div className="field">
                <label>Label</label>
                <input className="input" placeholder="e.g. Client A, Sandbox" value={newPlivoAccount.label}
                  onChange={e => setNewPlivoAccount(d => d && ({ ...d, label: e.target.value }))} />
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
                <div className="field">
                  <label>Auth ID</label>
                  <input className="input" placeholder="MAXXXXXXXXXXXXXXXXXXXX" value={newPlivoAccount.account_sid}
                    onChange={e => setNewPlivoAccount(d => d && ({ ...d, account_sid: e.target.value }))} />
                </div>
                <div className="field">
                  <label>Auth token</label>
                  <input className="input" type="password" placeholder="Enter auth token" value={newPlivoAccount.auth_token}
                    onChange={e => setNewPlivoAccount(d => d && ({ ...d, auth_token: e.target.value }))} />
                </div>
              </div>
              <div className="field">
                <label>Server base URL <span style={{ fontWeight: 400, color: "var(--ink-faint)" }}>(public URL of this server, use ngrok for local testing)</span></label>
                <input className="input" placeholder="https://your-server.com" value={newPlivoAccount.webhook_base_url}
                  onChange={e => setNewPlivoAccount(d => d && ({ ...d, webhook_base_url: e.target.value }))} />
              </div>
              <div style={{ display: "flex", gap: 10 }}>
                <button className="btn btn-primary" onClick={handleCreatePlivoAccount} disabled={savingPlivoAccountId === "new"}>
                  {savingPlivoAccountId === "new" ? "Adding..." : "Add account"}
                </button>
                {plivoAccounts.length > 0 && (
                  <button className="btn btn-ghost" onClick={() => setNewPlivoAccount(null)}>Cancel</button>
                )}
              </div>
            </div>
          </div>
        ) : (
          <button className="btn btn-ghost" style={{ alignSelf: "flex-start" }}
            onClick={() => setNewPlivoAccount({ ...emptyAccountDraft(), webhook_base_url: plivoAccounts.find(a => a.is_default)?.webhook_base_url ?? "" })}>
            <Icon name="plus" />Add Plivo account
          </button>
        )}
      </div>

      {!loading && plivoNumbers.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {plivoNumbers.map(num => {
            const attachedAgent = agents.find(a => a.id === num.agent_id);
            const isEditing = editingPlivoId === num.id;
            return (
              <div key={num.id} className="card number-row" style={{ borderTop: "none" }}>
                <div className="number-ic"><Icon name="phone" /></div>
                <div className="number-info">
                  {isEditing ? (
                    <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                      <input className="input" style={{ width: 180 }} value={editPlivoName} onChange={e => setEditPlivoName(e.target.value)}
                        onKeyDown={e => { if (e.key === "Enter") handleEditPlivoName(num.id); if (e.key === "Escape") setEditingPlivoId(null); }} autoFocus />
                      <button className="btn btn-primary btn-sm" onClick={() => handleEditPlivoName(num.id)}>Save</button>
                      <button className="btn btn-ghost btn-sm" onClick={() => setEditingPlivoId(null)}>Cancel</button>
                    </div>
                  ) : (
                    <div className="nm">
                      {num.friendly_name || num.phone_number}
                      {num.friendly_name && <span className="num">{num.phone_number}</span>}
                      <button className="icon-btn" style={{ width: 24, height: 24, border: "none", background: "none" }}
                        onClick={() => { setEditingPlivoId(num.id); setEditPlivoName(num.friendly_name ?? ""); }} title="Edit name">
                        <Icon name="edit" size={12} />
                      </button>
                    </div>
                  )}
                  <div className="sub">{attachedAgent ? <>Connected to <b>{attachedAgent.name}</b></> : "Not connected to any agent"}</div>
                </div>
                <div className="number-actions">
                  {plivoAccounts.length > 1 && (
                    <select className="select" style={{ width: "auto" }} value={num.plivo_account_id ?? ""}
                      onChange={e => handlePlivoAccountAssign(num.id, e.target.value)} title="Plivo account">
                      <option value="">Default account</option>
                      {plivoAccounts.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
                    </select>
                  )}
                  <select className="select" style={{ width: "auto" }} value={num.agent_id ?? ""} onChange={e => handleAttachPlivo(num.id, e.target.value || null)}>
                    <option value="">No agent</option>
                    {agents.filter(a => a.is_active).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
                  </select>
                  <button className="btn btn-danger btn-sm" onClick={() => handleDeletePlivo(num.id)}><Icon name="trash" size={13} />Delete</button>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {!loading && plivoNumbers.length === 0 && (
        <div className="empty"><div className="ic"><Icon name="phone" /></div><p>No Plivo numbers yet.</p><p>Add a Plivo number to connect it to an agent.</p></div>
      )}

      {showAddPlivo && (
        <div className="modal-backdrop" onClick={() => setShowAddPlivo(false)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-head"><h3>Add a Plivo number</h3><button className="icon-btn" onClick={() => setShowAddPlivo(false)}><Icon name="x" /></button></div>
            <div className="modal-body">
              <div className="field">
                <label>Phone number (E.164 format) *</label>
                <input className="input" placeholder="+12025550101" value={addPlivoPhone} onChange={e => setAddPlivoPhone(e.target.value)} />
              </div>
              <div className="field" style={{ marginBottom: plivoAccounts.length > 1 ? 14 : 0 }}>
                <label>Friendly name (optional)</label>
                <input className="input" placeholder="e.g. Support Line" value={addPlivoName} onChange={e => setAddPlivoName(e.target.value)} />
              </div>
              {plivoAccounts.length > 1 && (
                <div className="field" style={{ marginBottom: 0 }}>
                  <label>Plivo account</label>
                  <select className="select" value={addPlivoAccountId} onChange={e => setAddPlivoAccountId(e.target.value)}>
                    <option value="">Default account</option>
                    {plivoAccounts.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
                  </select>
                </div>
              )}
              {addPlivoError && <div style={{ fontSize: 12, color: "var(--danger)", marginTop: 10 }}>{addPlivoError}</div>}
            </div>
            <div className="modal-foot">
              <button className="btn btn-ghost" onClick={() => { setShowAddPlivo(false); setAddPlivoPhone(""); setAddPlivoName(""); setAddPlivoAccountId(""); setAddPlivoError(""); }}>Cancel</button>
              <button className="btn btn-primary" onClick={handleAddPlivo} disabled={savingPlivo}><Icon name="plus" />{savingPlivo ? "Adding..." : "Add number"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------------------
   Main page
--------------------------------------------------------------------------- */

export default function AgentsPage() {
  const router = useRouter();
  const { isDark, toggle: toggleTheme, mode } = useConsoleMode();
  const [view, setView] = useState<View>("agents");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [showAccount, setShowAccount] = useState(false);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (typeof window !== "undefined" && !localStorage.getItem("access_token")) {
      router.replace("/signin"); return;
    }
    loadAgents();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadAgents = async () => {
    setLoading(true);
    try { setAgents(await API.agents()); } catch { /* ignore */ }
    setLoading(false);
  };

  const handleToggle = async (id: string, active: boolean) => {
    try {
      const updated = await API.updateAgent(id, { is_active: active });
      setAgents(ag => ag.map(a => a.id === id ? updated : a));
      if (selectedAgent?.id === id) setSelectedAgent(updated);
      pushToast(`${updated.name} is ${active ? "live" : "paused"}`);
    } catch (e) {
      pushToast((e as Error).message ?? "Failed to update agent status", "error");
    }
  };

  const handleSignOut = () => { clearTokens(); router.replace("/signin"); };

  // Read client-only to avoid a server/client hydration mismatch (localStorage is unavailable during SSR).
  const [userEmail, setUserEmail] = useState("");
  const [userName, setUserName] = useState("Account");
  useEffect(() => {
    const u = getUser();
    const email = u?.email ?? "";
    setUserEmail(email);
    setUserName(u?.full_name || email.split("@")[0] || "Account");
  }, []);

  const crumbMap: Record<View, string> = {
    agents: "Agents", "knowledge-base": "Knowledge Base", sessions: "Call Analytics", "phone-numbers": "Phone Numbers",
  };

  const goTo = (v: View) => { setView(v); setSelectedAgent(null); setQuery(""); };

  return (
    <div className="lumenc app-shell" data-mode={mode || undefined}>
      <IconSprite />
      <Rail view={view} onView={goTo} agentCount={agents.length} userEmail={userEmail} userName={userName} onOpenAccount={() => setShowAccount(true)} />
      <div className="main">
        <Topbar
          crumb={crumbMap[view]}
          search={view === "agents" && !selectedAgent ? { value: query, onChange: setQuery, placeholder: "Search agents" } : undefined}
          isDark={isDark} onToggleTheme={toggleTheme}
          userEmail={userEmail} onOpenAccount={() => setShowAccount(true)}
        />
        <div className="view-scroll">
          {view === "agents" && (
            selectedAgent ? (
              <AgentDetail agent={selectedAgent} onBack={() => setSelectedAgent(null)}
                onUpdate={updated => { setAgents(ag => ag.map(a => a.id === updated.id ? updated : a)); setSelectedAgent(updated); }}
                onDuplicate={dup => { setAgents(ag => [...ag, dup]); setSelectedAgent(dup); }}
                onGoToKb={() => { setSelectedAgent(null); setView("knowledge-base"); }}
              />
            ) : loading ? (
              <div className="view"><div className="empty"><p>Loading agents...</p></div></div>
            ) : (
              <AgentsGrid agents={agents} query={query} onSelect={setSelectedAgent} onToggle={handleToggle} onCreateClick={() => setShowCreate(true)} />
            )
          )}

          {view === "knowledge-base" && <KnowledgeBaseView />}
          {view === "sessions" && <SessionsView agents={agents} />}
          {view === "phone-numbers" && <PhoneNumbersView agents={agents} />}
        </div>
      </div>

      <TabBar view={view} onView={goTo} onMore={() => setShowAccount(true)} />

      {showCreate && (
        <CreateAgentModal
          onClose={() => setShowCreate(false)}
          onCreate={agent => { setAgents(ag => [...ag, agent]); setShowCreate(false); setSelectedAgent(agent); }}
        />
      )}
      {showAccount && (
        <AccountModal onClose={() => setShowAccount(false)} userName={userName} userEmail={userEmail} onSignOut={handleSignOut} />
      )}
      <ToastHost />
    </div>
  );
}
