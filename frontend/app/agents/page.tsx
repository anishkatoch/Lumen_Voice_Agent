"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { API, Agent, AgentFunction, FunctionParameter, PhoneNumber, HRCandidate, HRInterview } from "@/lib/api";
import { getUser, clearTokens } from "@/lib/auth";
import { useVoiceAgent } from "@/hooks/useVoiceAgent";

// ── Helpers ───────────────────────────────────────────────────────────────────

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

const AGENT_PALETTE = [
  { bg: "#7c3aed", soft: "#efe8ff" },
  { bg: "#2563eb", soft: "#e6f0ff" },
  { bg: "#ea580c", soft: "#ffeee0" },
  { bg: "#0d9488", soft: "#e2f7f4" },
  { bg: "#db2777", soft: "#fde4f0" },
  { bg: "#4f46e5", soft: "#eceafd" },
];

function agentColor(id: string) {
  let hash = 0;
  for (let i = 0; i < id.length; i++) hash = (hash * 31 + id.charCodeAt(i)) >>> 0;
  return AGENT_PALETTE[hash % AGENT_PALETTE.length];
}

function agentInitials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "A";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

type View = "agents" | "knowledge-base" | "sessions" | "phone-numbers";

const SIDEBAR_ICONS: Record<View, JSX.Element> = {
  "agents": (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="7" height="7" rx="1.5" /><rect x="13" y="4" width="7" height="7" rx="1.5" />
      <rect x="4" y="13" width="7" height="7" rx="1.5" /><rect x="13" y="13" width="7" height="7" rx="1.5" />
    </svg>
  ),
  "knowledge-base": (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
    </svg>
  ),
  "sessions": (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" /><path d="M3 12h18" /><path d="M3 18h18" />
    </svg>
  ),
  "phone-numbers": (
    <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.362 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.338 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" />
    </svg>
  ),
};

function Sidebar({ view, onView }: { view: View; onView: (v: View) => void }) {
  const userEmail = typeof window !== "undefined" ? (getUser()?.email ?? "") : "";
  return (
    <aside style={{
      width: 272, minHeight: "100vh", background: "#fff", borderRight: "1px solid #ececf1",
      display: "flex", flexDirection: "column", padding: "22px 0", flexShrink: 0,
      boxShadow: "1px 0 0 rgba(17,17,17,0.02)",
    }}>
      <style>{`
        .lumen-nav-item { position: relative; transition: background-color .15s ease, color .15s ease; }
        .lumen-nav-item:hover { background: #f5f5f9 !important; color: #111 !important; }
        .lumen-nav-item.active:hover { background: #eeeeff !important; }
        .lumen-trial-badge { transition: box-shadow .2s ease; }
        .lumen-trial-badge:hover { box-shadow: 0 2px 10px rgba(99,102,241,0.18); }
      `}</style>

      <div style={{ padding: "0 22px 26px", display: "flex", alignItems: "center", gap: 11 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 10,
          background: "linear-gradient(135deg, #6366f1 0%, #8b5cf6 100%)",
          boxShadow: "0 4px 14px rgba(99,102,241,0.35)",
          display: "flex", alignItems: "center", justifyContent: "center",
          color: "#fff", fontSize: 17, fontWeight: 700, flexShrink: 0,
        }}>L</div>
        <span style={{ fontSize: 21, fontWeight: 700, color: "#111", letterSpacing: "-0.6px" }}>Lumen</span>
      </div>

      <nav style={{ flex: 1, padding: "0 14px" }}>
        {([
          { id: "agents", label: "Agents" },
          { id: "knowledge-base", label: "Knowledge Base" },
          { id: "sessions", label: "Call Analytics" },
          { id: "phone-numbers", label: "Phone Numbers" },
        ] as const).map(({ id, label }) => (
          <button key={id} onClick={() => onView(id)}
            className={`lumen-nav-item${view === id ? " active" : ""}`}
            style={{
              width: "100%", display: "flex", alignItems: "center", gap: 12,
              padding: "11px 14px", borderRadius: 10, border: "none", cursor: "pointer",
              background: view === id ? "#f0f0ff" : "transparent",
              color: view === id ? "#5558e8" : "#555",
              fontWeight: view === id ? 600 : 500,
              fontSize: 15, marginBottom: 4, textAlign: "left",
            }}>
            {view === id && (
              <span style={{
                position: "absolute", left: 0, top: "50%", transform: "translateY(-50%)",
                width: 3, height: 18, borderRadius: 2, background: "#6366f1",
              }} />
            )}
            <span style={{ display: "flex", flexShrink: 0 }}>{SIDEBAR_ICONS[id]}</span>
            {label}
          </button>
        ))}
      </nav>

      <div style={{ padding: "0 18px" }}>
        <div className="lumen-trial-badge" style={{
          background: "linear-gradient(135deg, #f7f7ff 0%, #f0f0ff 100%)", border: "1px solid #e2e2ff", borderRadius: 10,
          padding: "11px 14px", marginBottom: 16, fontSize: 13.5, color: "#5558e8", fontWeight: 600,
          display: "flex", alignItems: "center", gap: 8,
        }}>
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z" />
          </svg>
          Free Trial
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 0 0", borderTop: "1px solid #f0f0f0" }}>
          <div style={{
            width: 34, height: 34, borderRadius: "50%",
            background: "linear-gradient(135deg, #6366f1, #8b5cf6)",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "#fff", fontSize: 14, fontWeight: 600, flexShrink: 0,
          }}>
            {userEmail[0]?.toUpperCase() ?? "U"}
          </div>
          <span style={{ fontSize: 13, color: "#444", fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {userEmail}
          </span>
        </div>
      </div>
    </aside>
  );
}

// ── Agent Card ────────────────────────────────────────────────────────────────

function AgentCard({ agent, onClick, onToggle }: {
  agent: Agent;
  onClick: () => void;
  onToggle: (active: boolean) => Promise<void>;
}) {
  const [toggling, setToggling] = useState(false);
  const color = agentColor(agent.id);

  return (
    <div onClick={onClick}
      style={{
        background: "#fff", borderRadius: 14, padding: "18px",
        border: "1px solid #f0f0f0", cursor: "pointer",
        transition: "box-shadow 0.15s, transform 0.1s",
        position: "relative", overflow: "hidden",
      }}
      onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = "0 4px 24px rgba(0,0,0,0.08)"; (e.currentTarget as HTMLDivElement).style.transform = "translateY(-1px)"; }}
      onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = "none"; (e.currentTarget as HTMLDivElement).style.transform = "none"; }}
    >
      <div style={{
        position: "absolute", top: -26, right: -26, width: 90, height: 90, borderRadius: "50%",
        background: color.soft, opacity: 0.9, pointerEvents: "none",
      }} />

      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 14 }}>
        <div style={{
          width: 38, height: 38, borderRadius: 10, background: color.bg,
          display: "flex", alignItems: "center", justifyContent: "center",
          color: "#fff", fontSize: 13, fontWeight: 700, flexShrink: 0, zIndex: 1,
        }}>
          {agentInitials(agent.name)}
        </div>

        <div style={{ zIndex: 1 }}
          onClick={async e => {
            e.stopPropagation();
            if (toggling) return;
            setToggling(true);
            try { await onToggle(!agent.is_active); } finally { setToggling(false); }
          }}>
          <div style={{
            width: 36, height: 20, borderRadius: 10,
            background: agent.is_active ? "#6366f1" : "#e0e0e0",
            position: "relative", transition: "background 0.2s", cursor: "pointer",
            opacity: toggling ? 0.6 : 1,
          }}>
            <div style={{
              position: "absolute", top: 2, left: agent.is_active ? 18 : 2,
              width: 16, height: 16, borderRadius: "50%", background: "#fff",
              transition: "left 0.2s", boxShadow: "0 1px 3px rgba(0,0,0,0.2)",
            }} />
          </div>
        </div>
      </div>

      <div style={{ fontWeight: 600, fontSize: 15, color: "#111", marginBottom: 4, zIndex: 1, position: "relative" }}>{agent.name}</div>
      <div style={{ fontSize: 12, color: "#aaa", marginBottom: 12, zIndex: 1, position: "relative" }}>
        edited {timeAgo(agent.updated_at)}
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", zIndex: 1, position: "relative" }}>
        <div style={{
          display: "inline-flex", alignItems: "center", gap: 5, padding: "4px 10px",
          borderRadius: 20, background: agent.is_active ? "#f0fdf4" : "#f5f5f5",
          border: `1px solid ${agent.is_active ? "#bbf7d0" : "#e5e5e5"}`,
        }}>
          <div style={{
            width: 6, height: 6, borderRadius: "50%",
            background: agent.is_active ? "#22c55e" : "#bbb",
          }} />
          <span style={{ fontSize: 11, fontWeight: 500, color: agent.is_active ? "#16a34a" : "#888" }}>
            {agent.is_active ? "Live" : "Off"}
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", gap: 2, height: 14 }}>
          {[6, 11, 8, 14, 5].map((h, i) => (
            <div key={i} style={{ width: 2.5, height: h, borderRadius: 1, background: agent.is_active ? color.bg : "#e5e5e5" }} />
          ))}
        </div>
      </div>
    </div>
  );
}

function CreateCard({ onClick }: { onClick: () => void }) {
  return (
    <div onClick={onClick}
      style={{
        background: "#fff", borderRadius: 14, padding: "20px",
        border: "2px dashed #d1d5db", cursor: "pointer",
        display: "flex", flexDirection: "column", alignItems: "center",
        justifyContent: "center", minHeight: 160, gap: 10,
        transition: "border-color 0.15s, background 0.15s",
      }}
      onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.borderColor = "#6366f1"; (e.currentTarget as HTMLDivElement).style.background = "#fafafa"; }}
      onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.borderColor = "#d1d5db"; (e.currentTarget as HTMLDivElement).style.background = "#fff"; }}
    >
      <div style={{
        width: 36, height: 36, borderRadius: "50%", background: "#f5f5ff",
        display: "flex", alignItems: "center", justifyContent: "center",
        fontSize: 22, color: "#6366f1",
      }}>+</div>
      <span style={{ fontSize: 13, color: "#888", fontWeight: 500 }}>Create a new agent</span>
    </div>
  );
}

// ── Create Agent Modal ────────────────────────────────────────────────────────

function CreateAgentModal({ onClose, onCreate }: { onClose: () => void; onCreate: (agent: Agent) => void }) {
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    if (!name.trim()) { setErr("Name is required"); return; }
    setLoading(true); setErr("");
    try {
      const agent = await API.createAgent({ name: name.trim(), description: desc.trim() || undefined });
      onCreate(agent);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to create agent");
    } finally { setLoading(false); }
  };

  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
    }} onClick={onClose}>
      <div style={{
        background: "#fff", borderRadius: 16, padding: 32, width: 420,
        boxShadow: "0 20px 60px rgba(0,0,0,0.15)",
      }} onClick={e => e.stopPropagation()}>
        <h2 style={{ margin: "0 0 20px", fontSize: 18, fontWeight: 700 }}>Create Agent</h2>
        <div style={{ marginBottom: 14 }}>
          <label style={{ fontSize: 12, color: "#666", fontWeight: 500, display: "block", marginBottom: 6 }}>Agent Name *</label>
          <input value={name} onChange={e => setName(e.target.value)}
            placeholder="e.g. Receptionist, Support Bot"
            onKeyDown={e => e.key === "Enter" && submit()}
            style={{ width: "100%", padding: "10px 12px", borderRadius: 8, border: "1px solid #e0e0e0", fontSize: 14, outline: "none", boxSizing: "border-box" }} />
        </div>
        <div style={{ marginBottom: 20 }}>
          <label style={{ fontSize: 12, color: "#666", fontWeight: 500, display: "block", marginBottom: 6 }}>Description (optional)</label>
          <input value={desc} onChange={e => setDesc(e.target.value)}
            placeholder="Brief description"
            style={{ width: "100%", padding: "10px 12px", borderRadius: 8, border: "1px solid #e0e0e0", fontSize: 14, outline: "none", boxSizing: "border-box" }} />
        </div>
        {err && <div style={{ color: "#ef4444", fontSize: 13, marginBottom: 14 }}>{err}</div>}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={{ padding: "9px 18px", borderRadius: 8, border: "1px solid #e0e0e0", background: "#fff", cursor: "pointer", fontSize: 14 }}>Cancel</button>
          <button onClick={submit} disabled={loading}
            style={{ padding: "9px 18px", borderRadius: 8, border: "none", background: "#111", color: "#fff", cursor: "pointer", fontSize: 14, fontWeight: 500 }}>
            {loading ? "Creating..." : "Create Agent"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Function Form (inline) ────────────────────────────────────────────────────

function FunctionForm({
  agentId, initial, onSave, onCancel,
}: {
  agentId: string;
  initial?: AgentFunction;
  onSave: (fn: AgentFunction) => void;
  onCancel: () => void;
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
      const headersObj = Object.fromEntries(
        headers.filter(h => h.key.trim()).map(h => [h.key.trim(), h.value])
      );
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
      onSave(fn);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Save failed");
    } finally { setLoading(false); }
  };

  const inp = {
    width: "100%", padding: "8px 10px", borderRadius: 7, border: "1px solid #e5e5e5",
    fontSize: 13, outline: "none", boxSizing: "border-box" as const, color: "#111",
  };

  return (
    <div style={{ padding: 16, background: "#fafafa", borderRadius: 10, border: "1px solid #f0f0f0" }}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
        <div>
          <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Function Name *</label>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="checkAvailability" style={inp} />
        </div>
        <div>
          <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Trigger</label>
          <select value={trigger} onChange={e => setTrigger(e.target.value)} style={{ ...inp, background: "#fff" }}>
            <option value="llm_tool_call">Agent decides (LLM tool call)</option>
            <option value="always">Always run</option>
            <option value="on_start">On conversation start</option>
          </select>
        </div>
      </div>
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Description</label>
        <textarea value={desc} onChange={e => setDesc(e.target.value)}
          placeholder="Describe what this function does and when to call it"
          rows={2} style={{ ...inp, resize: "vertical", fontFamily: "inherit" }} />
      </div>
      <div style={{ marginBottom: 4 }}>
        <label style={{ fontSize: 11, color: "#888", display: "block", marginBottom: 4 }}>Webhook URL</label>
        <div style={{ display: "flex", gap: 8 }}>
          <select value={method} onChange={e => setMethod(e.target.value)}
            style={{ ...inp, width: 100, flexShrink: 0, background: "#fff", fontWeight: 600,
              color: method === "GET" ? "#3b82f6" : method === "DELETE" ? "#ef4444" : method === "PUT" || method === "PATCH" ? "#f59e0b" : "#6366f1" }}>
            <option value="GET">GET</option>
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
            <option value="DELETE">DELETE</option>
          </select>
          <input value={url} onChange={e => setUrl(e.target.value)}
            placeholder="https://your-api.com/endpoint" style={{ ...inp, flex: 1 }} />
        </div>
        <div style={{ fontSize: 11, color: "#aaa", marginTop: 6, lineHeight: 1.5 }}>
          A webhook is a URL your agent calls automatically when it needs external data.
          For example: check a calendar, look up a customer record, or submit a booking.
          {method === "GET" && " GET - fetches data, no body sent."}
          {method === "POST" && " POST - sends parameters in the request body."}
          {method === "PUT" && " PUT - replaces a resource with the sent data."}
          {method === "PATCH" && " PATCH - partially updates a resource."}
          {method === "DELETE" && " DELETE - removes a resource."}
        </div>
      </div>
      <div style={{ marginBottom: 12 }} />
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 8 }}>
          <label style={{ fontSize: 11, color: "#888" }}>Parameters</label>
          <button onClick={addParam}
            style={{ fontSize: 11, color: "#6366f1", background: "none", border: "none", cursor: "pointer", fontWeight: 500 }}>
            + Add parameter
          </button>
        </div>
        {params.map((p, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 110px 1fr 28px", gap: 6, marginBottom: 6, alignItems: "center" }}>
            <input value={p.name} onChange={e => updateParam(i, "name", e.target.value)} placeholder="name" style={{ ...inp, fontSize: 12 }} />
            <select value={p.type} onChange={e => updateParam(i, "type", e.target.value)} style={{ ...inp, fontSize: 12, background: "#fff" }}>
              {(["string", "number", "boolean", "object", "array"] as const).map(t => <option key={t}>{t}</option>)}
            </select>
            <input value={p.description} onChange={e => updateParam(i, "description", e.target.value)} placeholder="description" style={{ ...inp, fontSize: 12 }} />
            <button onClick={() => removeParam(i)}
              style={{ width: 28, height: 28, borderRadius: 6, border: "1px solid #f0f0f0", background: "#fff", cursor: "pointer", color: "#999", fontSize: 16, display: "flex", alignItems: "center", justifyContent: "center" }}>
              ×
            </button>
          </div>
        ))}
      </div>

      {/* Headers */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
          <div>
            <label style={{ fontSize: 11, color: "#888" }}>Headers</label>
            <span style={{ fontSize: 10, color: "#bbb", marginLeft: 6 }}>optional - e.g. Authorization, API-Key</span>
          </div>
          <button onClick={addHeader}
            style={{ fontSize: 11, color: "#6366f1", background: "none", border: "none", cursor: "pointer", fontWeight: 500 }}>
            + Add header
          </button>
        </div>
        {headers.length === 0 && (
          <div style={{ fontSize: 11, color: "#ccc", padding: "8px 0" }}>
            No headers added. Most APIs require at least an Authorization header.
          </div>
        )}
        {headers.map((h, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr 28px", gap: 6, marginBottom: 6, alignItems: "center" }}>
            <input value={h.key} onChange={e => updateHeader(i, "key", e.target.value)}
              placeholder="Header name (e.g. Authorization)"
              style={{ ...inp, fontSize: 12 }} />
            <input value={h.value} onChange={e => updateHeader(i, "value", e.target.value)}
              placeholder="Value (e.g. Bearer sk-...)"
              style={{ ...inp, fontSize: 12 }} />
            <button onClick={() => removeHeader(i)}
              style={{ width: 28, height: 28, borderRadius: 6, border: "1px solid #f0f0f0", background: "#fff", cursor: "pointer", color: "#999", fontSize: 16, display: "flex", alignItems: "center", justifyContent: "center" }}>
              ×
            </button>
          </div>
        ))}
      </div>
      {err && <div style={{ color: "#ef4444", fontSize: 12, marginBottom: 8 }}>{err}</div>}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button onClick={onCancel}
          style={{ padding: "7px 14px", borderRadius: 7, border: "1px solid #e0e0e0", background: "#fff", cursor: "pointer", fontSize: 13 }}>
          Cancel
        </button>
        <button onClick={save} disabled={loading}
          style={{ padding: "7px 14px", borderRadius: 7, border: "none", background: "#111", color: "#fff", cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
          {loading ? "Saving..." : "Save function"}
        </button>
      </div>
    </div>
  );
}

// ── Floating Orb (right panel) ────────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-unused-vars
function AgentOrb3D({ name }: { name: string }) {
  return (
    <>
      <style>{`@keyframes orbFloat { 0%,100% { transform:translateY(0); } 50% { transform:translateY(-8px); } }`}</style>
      <div style={{
        width: 130, height: 130, borderRadius: "50%",
        background: "radial-gradient(circle at 38% 32%, #e8f4ff 0%, #a8c8f0 15%, #7eb3e8 30%, #6366f1 55%, #8b5cf6 75%, #c4b5fd 100%)",
        boxShadow: "0 0 50px #6366f166, 0 0 90px #8b5cf633, 0 8px 32px rgba(99,102,241,0.3)",
        margin: "0 auto 20px",
        animation: "orbFloat 3s ease-in-out infinite",
      }} />
    </>
  );
}

// ── Voice Right Panel ─────────────────────────────────────────────────────────

function VoicePanel({ agentId, agentName, docIds }: { agentId: string; agentName: string; docIds: string[] }) {
  const { state, connected, chat, error, connect, disconnect, startMic, stopMic, isMicActive } = useVoiceAgent();
  const chatEndRef = useRef<HTMLDivElement>(null);

  const isIdle = !connected;
  const isPaused = connected && !isMicActive;

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat]);

  const handleStart = useCallback(() => {
    connect({ agentId, docIds: docIds.length > 0 ? docIds : undefined });
  }, [connect, agentId, docIds]);
  const handleStop   = useCallback(() => stopMic(),    [stopMic]);
  const handleResume = useCallback(() => startMic(),   [startMic]);
  const handleReset  = useCallback(() => disconnect(), [disconnect]);

  const statusLabel = () => {
    if (state === "LISTENING")  return "Listening...";
    if (state === "PROCESSING") return "Thinking...";
    if (state === "SPEAKING")   return "Speaking...";
    if (isPaused)               return "Paused";
    return "Connecting...";
  };

  const waveColor = state === "LISTENING" ? "#22c55e"
    : state === "SPEAKING"   ? "#6366f1"
    : state === "PROCESSING" ? "#f59e0b"
    : "#06b6d4";

  return (
    <div style={{
      width: 360, flexShrink: 0,
      borderLeft: "1px solid #f0f0f0",
      display: "flex", flexDirection: "column",
      position: "sticky", top: 0, height: "100vh",
      background: "linear-gradient(180deg, #ffffff 60%, #fdf4ff 100%)",
    }}>
      {/* Top bar */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "14px 18px", borderBottom: "1px solid #f5f5f5", flexShrink: 0,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: "#111" }}>Audio</span>
          <span style={{ fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 20, background: "#ede9fe", color: "#7c3aed" }}>TEST</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 5, fontSize: 12, color: "#999" }}>
          <div style={{ width: 7, height: 7, borderRadius: "50%", background: connected ? "#22c55e" : "#d1d5db" }} />
          {connected ? "connected" : "not connected"}
        </div>
      </div>

      {/* Main area */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>

        {/* ── IDLE: orb + tagline ── */}
        {isIdle && (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "0 24px 90px" }}>
            <AgentOrb3D name={agentName} />
            <div style={{ fontWeight: 700, fontSize: 18, color: "#111", marginBottom: 6, textAlign: "center" }}>
              Talk to {agentName}
            </div>
            <div style={{ fontSize: 13, color: "#aaa", marginBottom: 20, textAlign: "center", lineHeight: 1.5 }}>
              Test your prompt, functions, and knowledge live
            </div>
            <div style={{
              display: "flex", alignItems: "center", gap: 8, padding: "8px 16px", borderRadius: 30,
              background: "#fff", border: "1px solid #f0f0f0", boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
              fontSize: 13, color: "#888",
            }}>
              <span style={{ display: "flex", gap: 2, alignItems: "flex-end" }}>
                {[3, 5, 7, 5, 3].map((h, i) => (
                  <div key={i} style={{ width: 3, height: h, borderRadius: 2, background: "#d1d5db" }} />
                ))}
              </span>
              Mic ready · Default input
            </div>
            {error && (
              <div style={{ marginTop: 14, padding: "8px 12px", borderRadius: 8, background: "#fff5f5", border: "1px solid #fecaca", color: "#dc2626", fontSize: 12, textAlign: "center" }}>
                {error}
              </div>
            )}
          </div>
        )}

        {/* ── CONNECTED: full transcript ── */}
        {!isIdle && (
          <div style={{ flex: 1, display: "flex", flexDirection: "column", padding: "14px 18px 90px", overflow: "hidden" }}>
            {/* Status pill */}
            <div style={{
              display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
              padding: "7px 16px", borderRadius: 30, alignSelf: "center",
              background: "#fff", border: "1px solid #f0f0f0", boxShadow: "0 1px 4px rgba(0,0,0,0.06)",
              fontSize: 12, color: "#444", marginBottom: 14, flexShrink: 0,
            }}>
              <span style={{ display: "flex", gap: 2, alignItems: "flex-end" }}>
                {[3, 5, 7, 5, 3].map((h, i) => (
                  <div key={i} style={{ width: 3, height: h, borderRadius: 2, background: waveColor, transition: "background 0.3s" }} />
                ))}
              </span>
              {statusLabel()}
            </div>

            {error && (
              <div style={{ padding: "8px 12px", borderRadius: 8, background: "#fff5f5", border: "1px solid #fecaca", color: "#dc2626", fontSize: 12, marginBottom: 10, flexShrink: 0 }}>
                {error}
              </div>
            )}

            {/* Scrollable transcript */}
            <div style={{ flex: 1, overflowY: "auto", display: "flex", flexDirection: "column", gap: 10 }}>
              {chat.length === 0 ? (
                <div style={{ color: "#bbb", fontSize: 13, textAlign: "center", marginTop: 40 }}>
                  Waiting for conversation...
                </div>
              ) : (
                chat.map(entry => (
                  <div key={entry.id} style={{ display: "flex", flexDirection: "column", alignItems: entry.role === "assistant" ? "flex-start" : "flex-end" }}>
                    <div style={{ fontSize: 10, fontWeight: 700, color: entry.role === "assistant" ? "#6366f1" : "#888", marginBottom: 3 }}>
                      {entry.role === "assistant" ? agentName.toUpperCase() : "YOU"}
                    </div>
                    <div style={{
                      maxWidth: "88%", padding: "9px 13px", fontSize: 13, color: "#333", lineHeight: 1.55,
                      background: entry.role === "assistant" ? "#f0f0ff" : "#f3f4f6",
                      borderRadius: 14,
                      borderBottomLeftRadius: entry.role === "assistant" ? 3 : 14,
                      borderBottomRightRadius: entry.role === "user" ? 3 : 14,
                    }}>
                      {entry.text}
                    </div>
                  </div>
                ))
              )}
              <div ref={chatEndRef} />
            </div>
          </div>
        )}
      </div>

      {/* Bottom buttons */}
      <div style={{
        position: "absolute", bottom: 0, left: 0, right: 0, padding: "14px 18px",
        background: isIdle
          ? "linear-gradient(to top, #fdf4ff 70%, transparent)"
          : "linear-gradient(to top, #ffffff 70%, transparent)",
      }}>
        {isIdle ? (
          <button onClick={handleStart}
            style={{
              width: "100%", padding: "14px 0", borderRadius: 50, border: "none", cursor: "pointer",
              fontSize: 14, fontWeight: 600, background: "#111", color: "#fff",
              display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
              boxShadow: "0 4px 16px rgba(0,0,0,0.2)", transition: "all 0.2s",
            }}>
            <span style={{ fontSize: 9 }}>●</span> Start talking
          </button>
        ) : (
          <div style={{ display: "flex", gap: 8 }}>
            {isPaused ? (
              <button onClick={handleResume}
                style={{
                  flex: 1, padding: "13px 0", borderRadius: 50, border: "none", cursor: "pointer",
                  fontSize: 14, fontWeight: 600, background: "#22c55e", color: "#fff",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
                  boxShadow: "0 4px 14px rgba(34,197,94,0.35)", transition: "all 0.2s",
                }}>
                ▶ Resume
              </button>
            ) : (
              <button onClick={handleStop}
                style={{
                  flex: 1, padding: "13px 0", borderRadius: 50, border: "none", cursor: "pointer",
                  fontSize: 14, fontWeight: 600, background: "#f59e0b", color: "#fff",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 7,
                  boxShadow: "0 4px 14px rgba(245,158,11,0.35)", transition: "all 0.2s",
                }}>
                ⏸ Stop
              </button>
            )}
            <button onClick={handleReset}
              style={{
                padding: "13px 22px", borderRadius: 50, border: "1px solid #e0e0e0",
                cursor: "pointer", fontSize: 14, fontWeight: 600, background: "#fff", color: "#666",
                display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                transition: "all 0.2s",
              }}>
              ↺ Reset
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Section Card ──────────────────────────────────────────────────────────────

function Section({ title, children, extra }: { title: string; children: React.ReactNode; extra?: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  return (
    <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #f0f0f0", marginBottom: 16, overflow: "hidden" }}>
      <div style={{ padding: "14px 18px", borderBottom: collapsed ? "none" : "1px solid #f5f5f5", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <span style={{ fontWeight: 600, fontSize: 15, color: "#111" }}>{title}</span>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {extra}
          <button
            type="button"
            onClick={() => setCollapsed(c => !c)}
            aria-label={collapsed ? `Expand ${title}` : `Collapse ${title}`}
            aria-expanded={!collapsed}
            title={collapsed ? "Show" : "Hide"}
            style={{
              background: "none", border: "none", cursor: "pointer", padding: 4,
              color: "#999", fontSize: 13, lineHeight: 1, display: "flex", alignItems: "center",
              transform: collapsed ? "rotate(-90deg)" : "rotate(0deg)", transition: "transform 0.15s",
            }}
          >
            ▼
          </button>
        </div>
      </div>
      {!collapsed && <div style={{ padding: "16px 18px" }}>{children}</div>}
    </div>
  );
}

// ── Agent Detail ──────────────────────────────────────────────────────────────

// ── HR Section inside AgentDetail ────────────────────────────────────────────

function AgentHRSection({ agentId, hrInstructions, onHrInstructionsChange }: {
  agentId: string;
  hrInstructions: string;
  onHrInstructionsChange: (v: string) => void;
}) {
  const [candidates, setCandidates] = useState<HRCandidate[]>([]);
  const [interviews, setInterviews] = useState<HRInterview[]>([]);
  const [loading, setLoading] = useState(true);

  // Candidate modal
  const [showCand, setShowCand] = useState(false);
  const [editCand, setEditCand] = useState<HRCandidate | null>(null);
  const [cForm, setCForm] = useState({ name: "", phone: "", email: "", role: "", notes: "", scheduled_at: "", call_lead_minutes: 30, specific_questions: "" });
  const [cResumeFile, setCResumeFile] = useState<File | null>(null);
  const [cSaving, setCSaving] = useState(false);
  const [cErr, setCErr] = useState("");

  // Interview modal
  const [showInt, setShowInt] = useState(false);
  const [editInt, setEditInt] = useState<HRInterview | null>(null);
  const [iForm, setIForm] = useState({ candidate_id: "", scheduled_at: "", call_lead_minutes: 30, specific_questions: "" });
  const [iSaving, setISaving] = useState(false);
  const [iErr, setIErr] = useState("");

  const inp: React.CSSProperties = {
    padding: "8px 12px", border: "1px solid #e0e0e0", borderRadius: 8,
    fontSize: 13, outline: "none", color: "#111", background: "#fff",
    width: "100%", boxSizing: "border-box",
  };
  const lbl: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: "#666", display: "block", marginBottom: 4 };

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

  // Candidate handlers
  const openAddCand = () => { setCForm({ name: "", phone: "", email: "", role: "", notes: "", scheduled_at: "", call_lead_minutes: 30, specific_questions: "" }); setCResumeFile(null); setCErr(""); setEditCand(null); setShowCand(true); };
  const openEditCand = (c: HRCandidate) => { setCForm({ name: c.name, phone: c.phone, email: c.email ?? "", role: c.role ?? "", notes: c.notes ?? "", scheduled_at: "", call_lead_minutes: 30, specific_questions: "" }); setCResumeFile(null); setCErr(""); setEditCand(c); setShowCand(true); };
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
      // Upload CV file if one was selected
      if (cResumeFile) {
        const withResume = await API.uploadCandidateResume(saved.id, cResumeFile);
        setCandidates(cs => cs.map(c => c.id === withResume.id ? withResume : c));
      }
      // Create interview if a date was provided
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
    } catch (e: unknown) { setCErr(e instanceof Error ? e.message : "Failed"); }
    setCSaving(false);
  };
  const deleteCand = async (id: string) => {
    if (!confirm("Delete this candidate?")) return;
    await API.deleteCandidate(id);
    setCandidates(cs => cs.filter(c => c.id !== id));
  };

  // Interview handlers
  const openAddInt = (candidateId: string) => {
    const now = new Date(); now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    setIForm({ candidate_id: candidateId, scheduled_at: now.toISOString().slice(0, 16), call_lead_minutes: 30, specific_questions: "" });
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
    } catch (e: unknown) { setIErr(e instanceof Error ? e.message : "Failed"); }
    setISaving(false);
  };
  const deleteInt = async (id: string) => {
    if (!confirm("Delete this scheduled call?")) return;
    await API.deleteInterview(id);
    setInterviews(is => is.filter(i => i.id !== id));
  };
  const cancelInt = async (id: string) => {
    const u = await API.updateInterview(id, { status: "cancelled" });
    setInterviews(is => is.map(i => i.id === u.id ? u : i));
  };

  return (
    <>
      {/* HR Instructions box */}
      <Section title="HR Instructions">
        <div style={{ fontSize: 12, color: "#888", marginBottom: 8 }}>
          Default questions and instructions for every call this agent makes.
          The agent will always follow these regardless of which candidate it calls.
        </div>
        <textarea
          value={hrInstructions}
          onChange={e => onHrInstructionsChange(e.target.value)}
          rows={6}
          placeholder={"Always greet the candidate warmly by name.\nAsk these questions in order:\n1. Tell me about yourself.\n2. Why are you interested in this role?\n3. What is your expected CTC?\n\nIf the candidate asks about next steps, tell them HR will follow up within 3 business days."}
          style={{ ...inp, fontFamily: "ui-monospace, monospace", lineHeight: 1.6, resize: "vertical", color: "#333" }}
        />
        <div style={{ fontSize: 11, color: "#bbb", textAlign: "right", marginTop: 4 }}>{hrInstructions.length} chars · saved with the agent</div>
      </Section>

      {/* Candidates + Interviews merged */}
      <Section title="HR Call Manager">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <span style={{ fontSize: 12, color: "#888" }}>{candidates.length} candidate{candidates.length !== 1 ? "s" : ""}</span>
          <button onClick={openAddCand} style={{ padding: "5px 14px", background: "#6366f1", color: "#fff", border: "none", borderRadius: 7, cursor: "pointer", fontSize: 12, fontWeight: 500 }}>+ Add Candidate</button>
        </div>

        {loading ? <div style={{ color: "#aaa", fontSize: 13 }}>Loading...</div> : candidates.length === 0 ? (
          <div style={{ color: "#bbb", fontSize: 13, textAlign: "center", padding: "20px 0" }}>
            No candidates yet. Add a candidate to get started.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {candidates.map(c => {
              const candInterviews = interviews.filter(iv => iv.candidate_id === c.id);
              return (
                <div key={c.id} style={{ border: "1px solid #e8e8f0", borderRadius: 12, overflow: "hidden", background: "#fff" }}>
                  {/* Candidate row */}
                  <div style={{ padding: "12px 14px", background: "#fafafa", display: "flex", alignItems: "center", gap: 12 }}>
                    <div style={{ width: 34, height: 34, borderRadius: "50%", background: "#f0f0ff", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 14, fontWeight: 600, color: "#6366f1", flexShrink: 0 }}>
                      {c.name[0]?.toUpperCase()}
                    </div>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
                        <span style={{ fontWeight: 600, fontSize: 13, color: "#111" }}>{c.name}</span>
                        {c.role && <span style={{ fontSize: 11, color: "#6366f1", background: "#f0f0ff", padding: "1px 7px", borderRadius: 20 }}>{c.role}</span>}
                        {c.resume_file_name && <span style={{ fontSize: 10, color: "#16a34a", background: "#f0fdf4", padding: "1px 7px", borderRadius: 20 }}>CV ✓</span>}
                      </div>
                      <div style={{ fontSize: 11, color: "#888" }}>📞 {c.phone}{c.email ? ` · ${c.email}` : ""}</div>
                    </div>
                    <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                      <button onClick={() => openAddInt(c.id)} style={{ padding: "4px 10px", border: "1px solid #6366f1", borderRadius: 6, background: "#fff", color: "#6366f1", fontSize: 11, cursor: "pointer", fontWeight: 500 }}>+ Schedule Call</button>
                      <button onClick={() => openEditCand(c)} style={{ padding: "4px 10px", border: "1px solid #e0e0e0", borderRadius: 6, background: "#fff", color: "#555", fontSize: 11, cursor: "pointer" }}>Edit</button>
                      <button onClick={() => deleteCand(c.id)} style={{ padding: "4px 10px", border: "1px solid #fee2e2", borderRadius: 6, background: "#fff", color: "#ef4444", fontSize: 11, cursor: "pointer" }}>Del</button>
                    </div>
                  </div>
                  {/* Scheduled calls for this candidate */}
                  {candInterviews.length === 0 ? (
                    <div style={{ padding: "8px 14px 8px 60px", fontSize: 11, color: "#ccc", borderTop: "1px solid #f5f5f5" }}>
                      No calls scheduled - click &quot;+ Schedule Call&quot; to set one up
                    </div>
                  ) : (
                    candInterviews.map(iv => {
                      const s = STATUS_COLOR[iv.status] || STATUS_COLOR.pending;
                      return (
                        <div key={iv.id} style={{ borderTop: "1px solid #f0f0f0", padding: "10px 14px 10px 60px", display: "flex", alignItems: "flex-start", gap: 10 }}>
                          <div style={{ flex: 1 }}>
                            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 3 }}>
                              <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 20, background: s.bg, color: s.text, border: `1px solid ${s.border}`, fontWeight: 600 }}>{iv.status}</span>
                              <span style={{ fontSize: 12, color: "#333", fontWeight: 500 }}>📅 {new Date(iv.scheduled_at).toLocaleString()}</span>
                              <span style={{ fontSize: 11, color: "#888" }}>⏰ call {iv.call_lead_minutes} min before</span>
                            </div>
                            {iv.specific_questions && (
                              <div style={{ fontSize: 11, color: "#777", marginTop: 2 }}>
                                <span style={{ color: "#999", fontWeight: 600 }}>Q: </span>{iv.specific_questions.slice(0, 120)}{iv.specific_questions.length > 120 ? "…" : ""}
                              </div>
                            )}
                          </div>
                          <div style={{ display: "flex", gap: 5, flexShrink: 0 }}>
                            {iv.status === "pending" && <>
                              <button onClick={() => openEditInt(iv)} style={{ padding: "3px 9px", border: "1px solid #e0e0e0", borderRadius: 6, background: "#fff", color: "#555", fontSize: 11, cursor: "pointer" }}>Edit</button>
                              <button onClick={() => cancelInt(iv.id)} style={{ padding: "3px 9px", border: "1px solid #fde68a", borderRadius: 6, background: "#fff", color: "#d97706", fontSize: 11, cursor: "pointer" }}>Cancel</button>
                            </>}
                            <button onClick={() => deleteInt(iv.id)} style={{ padding: "3px 9px", border: "1px solid #fee2e2", borderRadius: 6, background: "#fff", color: "#ef4444", fontSize: 11, cursor: "pointer" }}>Del</button>
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

      {/* Candidate modal */}
      {showCand && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 2000 }} onClick={() => setShowCand(false)}>
          <div style={{ background: "#fff", borderRadius: 16, padding: 28, width: 540, maxHeight: "90vh", overflowY: "auto", boxShadow: "0 20px 60px rgba(0,0,0,0.18)" }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#111", marginBottom: 18 }}>{editCand ? "Edit Candidate" : "Add Candidate"}</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
              <div><label style={lbl}>Full Name *</label><input style={inp} value={cForm.name} onChange={e => setCForm(f => ({ ...f, name: e.target.value }))} placeholder="Priya Sharma" /></div>
              <div><label style={lbl}>Phone (E.164) *</label><input style={inp} value={cForm.phone} onChange={e => setCForm(f => ({ ...f, phone: e.target.value }))} placeholder="+919876543210" /></div>
              <div><label style={lbl}>Email</label><input style={inp} value={cForm.email} onChange={e => setCForm(f => ({ ...f, email: e.target.value }))} placeholder="priya@email.com" /></div>
              <div><label style={lbl}>Role Applied For</label><input style={inp} value={cForm.role} onChange={e => setCForm(f => ({ ...f, role: e.target.value }))} placeholder="Senior Engineer" /></div>
            </div>
            <div style={{ marginBottom: 12 }}><label style={lbl}>HR Notes (visible to agent)</label><textarea style={{ ...inp, height: 60, resize: "vertical" }} value={cForm.notes} onChange={e => setCForm(f => ({ ...f, notes: e.target.value }))} placeholder="5 YOE, strong backend, referred by..." /></div>
            <div style={{ marginBottom: 12 }}>
              <label style={lbl}>CV / Resume (PDF or DOCX)</label>
              <div style={{ border: "2px dashed #e0e0e0", borderRadius: 10, padding: "16px 20px", textAlign: "center", cursor: "pointer", background: cResumeFile ? "#f0fdf4" : "#fafafa" }}
                onClick={() => document.getElementById("cv-upload-input")?.click()}
                onDragOver={e => e.preventDefault()}
                onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) setCResumeFile(f); }}>
                <input id="cv-upload-input" type="file" accept=".pdf,.docx,.txt" style={{ display: "none" }}
                  onChange={e => { const f = e.target.files?.[0]; if (f) setCResumeFile(f); }} />
                {cResumeFile ? (
                  <div>
                    <div style={{ fontSize: 20, marginBottom: 4 }}>📄</div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#16a34a" }}>{cResumeFile.name}</div>
                    <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>Click to change</div>
                  </div>
                ) : editCand?.resume_file_name ? (
                  <div>
                    <div style={{ fontSize: 20, marginBottom: 4 }}>📄</div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: "#16a34a" }}>✓ {editCand.resume_file_name}</div>
                    <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>Click or drag to replace</div>
                  </div>
                ) : (
                  <div>
                    <div style={{ fontSize: 28, marginBottom: 6 }}>📎</div>
                    <div style={{ fontSize: 13, color: "#666" }}>Click or drag & drop CV here</div>
                    <div style={{ fontSize: 11, color: "#aaa", marginTop: 2 }}>PDF, DOCX, or TXT</div>
                  </div>
                )}
              </div>
            </div>
            {/* Interview scheduling - optional, inline */}
            <div style={{ margin: "16px 0 12px", borderTop: "1px solid #f0f0f0", paddingTop: 16 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#6366f1", marginBottom: 10 }}>Schedule Interview Call <span style={{ fontWeight: 400, color: "#aaa" }}>(optional)</span></div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 10 }}>
                <div>
                  <label style={lbl}>Interview Date & Time</label>
                  <input type="datetime-local" style={inp} value={cForm.scheduled_at} onChange={e => setCForm(f => ({ ...f, scheduled_at: e.target.value }))} />
                </div>
                <div>
                  <label style={lbl}>Call X min before interview</label>
                  <input type="number" style={inp} min={1} max={120} value={cForm.call_lead_minutes} onChange={e => setCForm(f => ({ ...f, call_lead_minutes: parseInt(e.target.value) || 30 }))} />
                </div>
              </div>
              <div>
                <label style={lbl}>Specific questions for this candidate</label>
                <textarea style={{ ...inp, height: 80, resize: "vertical" }} value={cForm.specific_questions} onChange={e => setCForm(f => ({ ...f, specific_questions: e.target.value }))}
                  placeholder={"1. Walk me through your last project.\n2. What is your expected CTC?"} />
              </div>
            </div>
            {cErr && <div style={{ fontSize: 12, color: "#ef4444", marginBottom: 10 }}>{cErr}</div>}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={() => setShowCand(false)} style={{ padding: "8px 16px", border: "1px solid #e0e0e0", borderRadius: 8, background: "#fff", color: "#555", cursor: "pointer", fontSize: 13 }}>Cancel</button>
              <button onClick={saveCand} disabled={cSaving} style={{ padding: "8px 16px", background: "#6366f1", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
                {cSaving ? "Saving..." : editCand ? "Save" : "Add Candidate"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Interview modal */}
      {showInt && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 2000 }} onClick={() => setShowInt(false)}>
          <div style={{ background: "#fff", borderRadius: 16, padding: 28, width: 500, maxHeight: "90vh", overflowY: "auto", boxShadow: "0 20px 60px rgba(0,0,0,0.18)" }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 15, fontWeight: 700, color: "#111", marginBottom: 6 }}>{editInt ? "Edit Scheduled Call" : "Schedule Call"}</div>
            {(() => { const c = candidates.find(x => x.id === iForm.candidate_id); return c ? (
              <div style={{ fontSize: 12, color: "#6366f1", background: "#f0f0ff", display: "inline-block", padding: "3px 10px", borderRadius: 20, marginBottom: 16 }}>
                {c.name}{c.role ? ` - ${c.role}` : ""}
              </div>
            ) : null; })()}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
              <div>
                <label style={lbl}>Interview Date & Time *</label>
                <input type="datetime-local" style={inp} value={iForm.scheduled_at} onChange={e => setIForm(f => ({ ...f, scheduled_at: e.target.value }))} />
              </div>
              <div>
                <label style={lbl}>Call X min before interview</label>
                <input type="number" style={inp} min={1} max={120} value={iForm.call_lead_minutes} onChange={e => setIForm(f => ({ ...f, call_lead_minutes: parseInt(e.target.value) || 30 }))} />
                <div style={{ fontSize: 10, color: "#aaa", marginTop: 2 }}>Agent dials this many minutes before the interview</div>
              </div>
            </div>
            <div style={{ marginBottom: 16 }}>
              <label style={lbl}>Specific questions for this candidate</label>
              <textarea style={{ ...inp, height: 120, resize: "vertical" }} value={iForm.specific_questions} onChange={e => setIForm(f => ({ ...f, specific_questions: e.target.value }))}
                placeholder={"1. Walk me through your last project.\n2. How do you handle conflicts in a team?\n3. What is your expected joining date?"} />
              <div style={{ fontSize: 11, color: "#aaa", marginTop: 2 }}>These are in addition to the agent&apos;s HR Instructions above.</div>
            </div>
            {iErr && <div style={{ fontSize: 12, color: "#ef4444", marginBottom: 10 }}>{iErr}</div>}
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
              <button onClick={() => setShowInt(false)} style={{ padding: "8px 16px", border: "1px solid #e0e0e0", borderRadius: 8, background: "#fff", color: "#555", cursor: "pointer", fontSize: 13 }}>Cancel</button>
              <button onClick={saveInt} disabled={iSaving} style={{ padding: "8px 16px", background: "#6366f1", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
                {iSaving ? "Saving..." : editInt ? "Save" : "Schedule Call"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function AgentDetail({ agent, onBack, onUpdate, onDuplicate, onGoToKb }: {
  agent: Agent;
  onBack: () => void;
  onUpdate: (a: Agent) => void;
  onDuplicate: (a: Agent) => void;
  onGoToKb: () => void;
}) {
  const [name, setName] = useState(agent.name);
  const [prompt, setPrompt] = useState(agent.system_prompt ?? "");
  const [kbInstructions, setKbInstructions] = useState(agent.kb_instructions ?? "");
  const [hrInstructions, setHrInstructions] = useState(agent.hr_instructions ?? "");
  const [firstMsgEnabled, setFirstMsgEnabled] = useState(agent.first_message_enabled);
  const [firstMsg, setFirstMsg] = useState(agent.first_message ?? "");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  // KB doc selection from user's personal KB
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
    setName(agent.name); setPrompt(agent.system_prompt ?? "");
    setKbInstructions(agent.kb_instructions ?? "");
    setHrInstructions(agent.hr_instructions ?? "");
    setFirstMsgEnabled(agent.first_message_enabled); setFirstMsg(agent.first_message ?? "");
    setSelectedDocIds(agent.selected_kb_doc_ids ?? []);
    loadUserKb(); loadFuncs();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agent.id]);

  // Close dropdown on outside click
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
        first_message_enabled: firstMsgEnabled,
        first_message: firstMsg || null,
        selected_kb_doc_ids: selectedDocIds,
      });
      onUpdate(updated); setSaveMsg("Saved!");
      setTimeout(() => setSaveMsg(""), 2000);
    } catch (e: unknown) {
      setSaveMsg(e instanceof Error ? e.message : "Save failed");
    } finally { setSaving(false); }
  };

  const handleDuplicate = async () => {
    try { onDuplicate(await API.duplicateAgent(agent.id)); } catch { /* ignore */ }
  };

  const handleDeleteAgent = async () => {
    try { await API.deleteAgent(agent.id); onBack(); } catch { /* ignore */ }
  };

  const templatePrompts: Record<string, string> = {
    Receptionist: "You are a friendly receptionist. Greet callers warmly, answer questions about business hours and services, and help schedule appointments.",
    Support: "You are a helpful customer support agent. Listen carefully to issues, empathize with the caller, and provide clear step-by-step solutions.",
    Sales: "You are a professional sales agent. Understand the prospect's needs, highlight relevant product benefits, and guide them toward a purchasing decision.",
    Assistant: "You are a helpful personal assistant. Complete tasks efficiently, provide accurate information, and proactively offer useful suggestions.",
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

  const funcBadge = (fn: AgentFunction) => (fn.url ? "ACTIVE" : "DRAFT");

  const inp = {
    width: "100%", padding: "10px 12px", borderRadius: 8, border: "1px solid #e5e5e5",
    fontSize: 13, outline: "none", boxSizing: "border-box" as const, fontFamily: "inherit",
  };

  return (
    <div style={{ display: "flex", flex: 1, minHeight: "100vh", background: "#f8f8f8", zoom: 1.12 } as React.CSSProperties}>
      {/* Left content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "28px 28px 80px" }}>
        {/* Breadcrumb + header */}
        <div style={{ marginBottom: 24 }}>
          <div style={{ fontSize: 12, color: "#999", marginBottom: 10 }}>
            <button onClick={onBack}
              style={{ background: "none", border: "none", color: "#999", cursor: "pointer", padding: 0, fontSize: 12 }}>
              Agents
            </button>
            {" / "}
            <span style={{ color: "#333" }}>{agent.name}</span>
          </div>
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 14, minWidth: 0 }}>
              <div style={{ minWidth: 0 }}>
                <input value={name} onChange={e => setName(e.target.value)}
                  style={{ fontSize: 22, fontWeight: 700, color: "#111", border: "none", background: "transparent", outline: "none", padding: 0, fontFamily: "inherit", width: "100%" }} />
                <div style={{ fontSize: 12, color: "#aaa", marginTop: 2 }}>
                  Voice agent · edited {timeAgo(agent.updated_at)}
                </div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
              <button onClick={handleDuplicate}
                style={{ padding: "8px 14px", borderRadius: 8, border: "1px solid #e0e0e0", background: "#fff", color: "#111", cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
                Duplicate
              </button>
              <button onClick={saveAgent} disabled={saving}
                style={{ padding: "8px 16px", borderRadius: 8, border: "none", background: saveMsg === "Saved!" ? "#22c55e" : "#111", color: "#fff", cursor: "pointer", fontSize: 13, fontWeight: 500, transition: "background 0.3s" }}>
                {saving ? "Saving..." : saveMsg || "+ Save agent"}
              </button>
            </div>
          </div>
        </div>

        {/* System Prompt */}
        <Section title="System Prompt">
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 10 }}>
            {Object.keys(templatePrompts).map(t => (
              <button key={t} onClick={() => setPrompt(templatePrompts[t])}
                style={{ padding: "4px 10px", borderRadius: 20, border: "1px solid #e5e5e5", background: "#fafafa", cursor: "pointer", fontSize: 12, color: "#555", fontWeight: 500 }}>
                + {t}
              </button>
            ))}
          </div>
          <textarea value={prompt} onChange={e => setPrompt(e.target.value)}
            placeholder="Describe how this agent should behave, its personality, and what tasks it handles..."
            rows={8}
            style={{ ...inp, fontFamily: "ui-monospace, monospace", lineHeight: 1.6, resize: "vertical", color: "#333" }} />
          <div style={{ textAlign: "right", fontSize: 11, color: "#bbb", marginTop: 4 }}>{prompt.length} chars</div>
          {selectedDocIds.length > 0 && (
            <div style={{ marginTop: 10, padding: "10px 12px", borderRadius: 8, background: "#f5f5ff", border: "1px solid #e0e0ff" }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "#6366f1", marginBottom: 3 }}>
                Attached KB documents ({selectedDocIds.length})
              </div>
              <div style={{ fontSize: 12, color: "#555", marginBottom: 4, lineHeight: 1.5 }}>
                {selectedDocIds
                  .map(id => userKbDocs.find(d => d.id === id)?.display_name ?? "")
                  .filter(Boolean)
                  .join(" · ")}
              </div>
              <div style={{ fontSize: 11, color: "#999" }}>
                Reference these in your prompt - e.g. &quot;When asked about [topic], refer to the attached documents.&quot;
              </div>
            </div>
          )}
        </Section>

        {/* Custom Functions */}
        <Section title="Custom Functions">
          {funcsLoading ? (
            <div style={{ color: "#999", fontSize: 13 }}>Loading...</div>
          ) : (
            <>
              {functions.map(fn => (
                <div key={fn.id} style={{ marginBottom: 8 }}>
                  {editingFunc?.id === fn.id ? (
                    <FunctionForm agentId={agent.id} initial={fn}
                      onSave={updated => { setFunctions(fns => fns.map(f => f.id === fn.id ? updated : f)); setEditingFunc(null); }}
                      onCancel={() => setEditingFunc(null)} />
                  ) : (
                    <div style={{ padding: "12px 14px", borderRadius: 8, border: "1px solid #f0f0f0", background: "#fff" }}>
                      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <button onClick={() => setExpandedFuncs(s => { const n = new Set(s); if (n.has(fn.id)) { n.delete(fn.id); } else { n.add(fn.id); } return n; })}
                            style={{ background: "none", border: "none", cursor: "pointer", fontSize: 11, color: "#999", padding: 0, width: 14 }}>
                            {expandedFuncs.has(fn.id) ? "▼" : "▶"}
                          </button>
                          <span style={{ fontSize: 14, fontWeight: 500, color: "#111" }}>{fn.name}</span>
                          <span style={{
                            fontSize: 10, padding: "2px 7px", borderRadius: 4, fontWeight: 600,
                            background: funcBadge(fn) === "ACTIVE" ? "#f0fdf4" : "#f5f5f5",
                            color: funcBadge(fn) === "ACTIVE" ? "#16a34a" : "#888",
                            border: `1px solid ${funcBadge(fn) === "ACTIVE" ? "#bbf7d0" : "#e5e5e5"}`,
                          }}>
                            {funcBadge(fn)}
                          </span>
                        </div>
                        <div style={{ display: "flex", gap: 8 }}>
                          <button onClick={() => setEditingFunc(fn)}
                            style={{ fontSize: 12, color: "#6366f1", background: "none", border: "none", cursor: "pointer" }}>Edit</button>
                          <button onClick={async () => {
                            if (!confirm("Delete this function?")) return;
                            await API.deleteAgentFunction(agent.id, fn.id);
                            setFunctions(fns => fns.filter(f => f.id !== fn.id));
                          }}
                            style={{ fontSize: 12, color: "#ef4444", background: "none", border: "none", cursor: "pointer" }}>Delete</button>
                        </div>
                      </div>
                      {expandedFuncs.has(fn.id) && (
                        <div style={{ marginTop: 8, paddingTop: 8, borderTop: "1px solid #f5f5f5" }}>
                          {fn.description && <p style={{ fontSize: 12, color: "#666", margin: "0 0 6px" }}>{fn.description}</p>}
                          {fn.url && <p style={{ fontSize: 11, color: "#999", margin: "0 0 6px", fontFamily: "monospace" }}>POST {fn.url}</p>}
                          {fn.parameters.length > 0 && (
                            <div style={{ fontSize: 11, color: "#888" }}>
                              {fn.parameters.map(p => `${p.name}: ${p.type}`).join(" · ")}
                            </div>
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
                <button onClick={() => setShowFuncForm(true)}
                  style={{
                    width: "100%", padding: 10, borderRadius: 8, border: "1px dashed #d1d5db",
                    background: "transparent", cursor: "pointer", fontSize: 13, color: "#888",
                    display: "flex", alignItems: "center", justifyContent: "center", gap: 6,
                  }}>
                  <span style={{ fontSize: 16 }}>+</span> Add function
                </button>
              )}
            </>
          )}
        </Section>

        {/* Knowledge Base */}
        <Section title="Knowledge Base">
          <div style={{ marginBottom: 14 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <label style={{ fontSize: 12, color: "#666", fontWeight: 500 }}>
                Select documents from your Knowledge Base
              </label>
              {/* "Add" hyperlink → goes to KB page */}
              <button onClick={onGoToKb}
                style={{ fontSize: 12, color: "#6366f1", background: "none", border: "none", cursor: "pointer", fontWeight: 500, textDecoration: "underline", padding: 0 }}>
                + Add documents
              </button>
            </div>

            {/* Multi-select dropdown */}
            <div ref={kbDropRef} style={{ position: "relative" }}>
              <div onClick={() => setKbOpen(o => !o)}
                style={{
                  padding: "10px 12px", borderRadius: 8, border: "1px solid #e5e5e5",
                  background: "#fff", cursor: "pointer", fontSize: 13,
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  minHeight: 40,
                }}>
                <span style={{ color: selectedDocIds.length === 0 ? "#bbb" : "#111" }}>
                  {selectedDocIds.length === 0
                    ? "Choose documents…"
                    : `${selectedDocIds.length} document${selectedDocIds.length > 1 ? "s" : ""} selected`}
                </span>
                <span style={{ color: "#aaa", fontSize: 10 }}>{kbOpen ? "▲" : "▼"}</span>
              </div>

              {kbOpen && (
                <div style={{
                  position: "absolute", top: "calc(100% + 4px)", left: 0, right: 0, zIndex: 100,
                  background: "#fff", border: "1px solid #e5e5e5", borderRadius: 10,
                  boxShadow: "0 8px 24px rgba(0,0,0,0.1)", maxHeight: 260, overflowY: "auto",
                }}>
                  {kbLoading ? (
                    <div style={{ padding: 14, color: "#aaa", fontSize: 13 }}>Loading your documents...</div>
                  ) : userKbDocs.length === 0 ? (
                    <div style={{ padding: 14 }}>
                      <div style={{ color: "#bbb", fontSize: 13, marginBottom: 8 }}>No documents in your Knowledge Base yet.</div>
                      <button onClick={() => { setKbOpen(false); onGoToKb(); }}
                        style={{ fontSize: 13, color: "#6366f1", background: "none", border: "none", cursor: "pointer", padding: 0, fontWeight: 500, textDecoration: "underline" }}>
                        Go to Knowledge Base to upload files
                      </button>
                    </div>
                  ) : (
                    userKbDocs.map(doc => {
                      const ext = doc.file_name.split(".").pop()?.toLowerCase() ?? "txt";
                      const badge = ext === "pdf" ? { bg: "#eff6ff", color: "#3b82f6", label: "PDF" }
                        : ext === "docx" ? { bg: "#f5f3ff", color: "#8b5cf6", label: "DOC" }
                        : { bg: "#f0fdf4", color: "#16a34a", label: "TXT" };
                      const checked = selectedDocIds.includes(doc.id);
                      return (
                        <div key={doc.id} onClick={() => setSelectedDocIds(ids =>
                            checked ? ids.filter(x => x !== doc.id) : [...ids, doc.id]
                          )}
                          style={{
                            display: "flex", alignItems: "center", gap: 10, padding: "10px 14px",
                            cursor: "pointer", background: checked ? "#f5f5ff" : "transparent",
                            borderBottom: "1px solid #f5f5f5", transition: "background 0.1s",
                          }}
                          onMouseEnter={e => { if (!checked) (e.currentTarget as HTMLDivElement).style.background = "#fafafa"; }}
                          onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = checked ? "#f5f5ff" : "transparent"; }}>
                          {/* Checkbox */}
                          <div style={{
                            width: 16, height: 16, borderRadius: 4, border: `2px solid ${checked ? "#6366f1" : "#d1d5db"}`,
                            background: checked ? "#6366f1" : "#fff", flexShrink: 0,
                            display: "flex", alignItems: "center", justifyContent: "center",
                          }}>
                            {checked && <span style={{ color: "#fff", fontSize: 10, lineHeight: 1 }}>✓</span>}
                          </div>
                          <span style={{ padding: "2px 6px", borderRadius: 4, fontSize: 10, fontWeight: 700, background: badge.bg, color: badge.color, flexShrink: 0 }}>
                            {badge.label}
                          </span>
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: 13, fontWeight: 500, color: "#111", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {doc.display_name || doc.file_name}
                            </div>
                            <div style={{ fontSize: 11, color: "#aaa" }}>{formatBytes(doc.file_size)}</div>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              )}
            </div>

            {/* Selected chips */}
            {selectedDocIds.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}>
                {selectedDocIds.map(id => {
                  const doc = userKbDocs.find(d => d.id === id);
                  if (!doc) return null;
                  return (
                    <span key={id} style={{
                      display: "inline-flex", alignItems: "center", gap: 5,
                      padding: "4px 10px", borderRadius: 20, background: "#f0f0ff",
                      border: "1px solid #e0e0ff", fontSize: 12, color: "#6366f1",
                    }}>
                      {doc.display_name || doc.file_name}
                      <button onClick={() => setSelectedDocIds(ids => ids.filter(x => x !== id))}
                        style={{ background: "none", border: "none", cursor: "pointer", color: "#a5b4fc", fontSize: 14, padding: 0, lineHeight: 1 }}>
                        ×
                      </button>
                    </span>
                  );
                })}
              </div>
            )}
          </div>

          <div>
            <label style={{ fontSize: 12, color: "#666", fontWeight: 500, display: "block", marginBottom: 6 }}>KB Instructions</label>
            <textarea value={kbInstructions} onChange={e => setKbInstructions(e.target.value)}
              placeholder="Instructions for how the agent should use knowledge base results..."
              rows={3}
              style={{ ...inp, resize: "vertical", color: "#333" }} />
          </div>
        </Section>

        {/* First Words */}
        <Section title="First Words">
          <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
            <div>
              <label style={{ fontSize: 12, color: "#666", fontWeight: 500, display: "block", marginBottom: 6 }}>Behavior</label>
              <select value={firstMsgEnabled ? "agent" : "user"} onChange={e => setFirstMsgEnabled(e.target.value === "agent")}
                style={{ padding: "9px 12px", borderRadius: 8, border: "1px solid #e5e5e5", fontSize: 13, outline: "none", background: "#fff", color: "#111827", cursor: "pointer" }}>
                <option value="agent">Agent speaks first</option>
                <option value="user">User speaks first</option>
              </select>
            </div>
            {firstMsgEnabled && (
              <div style={{ flex: 1 }}>
                <label style={{ fontSize: 12, color: "#666", fontWeight: 500, display: "block", marginBottom: 6 }}>Opening message</label>
                <input value={firstMsg} onChange={e => setFirstMsg(e.target.value)}
                  placeholder="Hello! How can I help you today?"
                  style={{ ...inp, color: "#111827" }} />
              </div>
            )}
          </div>
        </Section>

        {/* HR Instructions + HR Call Manager - only for the HR Support Agent */}
        {agent.name === "HR Support Agent" && (
          <AgentHRSection agentId={agent.id} hrInstructions={hrInstructions} onHrInstructionsChange={setHrInstructions} />
        )}

        {/* Danger zone */}
        <div style={{ marginTop: 4 }}>
          {!showDelete ? (
            <button onClick={() => setShowDelete(true)}
              style={{ fontSize: 12, color: "#ef4444", background: "none", border: "none", cursor: "pointer", padding: 0 }}>
              Delete this agent
            </button>
          ) : (
            <div style={{ padding: 16, borderRadius: 10, border: "1px solid #fecaca", background: "#fff5f5" }}>
              <p style={{ fontSize: 13, color: "#dc2626", margin: "0 0 12px" }}>Delete &quot;{agent.name}&quot;? This cannot be undone.</p>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => setShowDelete(false)}
                  style={{ padding: "7px 14px", borderRadius: 7, border: "1px solid #e0e0e0", background: "#fff", cursor: "pointer", fontSize: 13 }}>Cancel</button>
                <button onClick={handleDeleteAgent}
                  style={{ padding: "7px 14px", borderRadius: 7, border: "none", background: "#ef4444", color: "#fff", cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
                  Yes, delete
                </button>
              </div>
            </div>
          )}
        </div>
      </div>

      <VoicePanel agentId={agent.id} agentName={agent.name} docIds={selectedDocIds} />
    </div>
  );
}

// ── Knowledge Base View ───────────────────────────────────────────────────────

const KB_TOTAL_MB = 10;
const KB_FILE_MB = 3;

function KnowledgeBaseView() {
  const [docs, setDocs] = useState<import("@/lib/api").UserDocument[]>([]);
  const [usedBytes, setUsedBytes] = useState(0);
  const [limitBytes, setLimitBytes] = useState(KB_TOTAL_MB * 1024 * 1024);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
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
    if (file.size > KB_FILE_MB * 1024 * 1024) {
      setErr(`File too large. Maximum ${KB_FILE_MB} MB per file.`); return;
    }
    if (usedBytes + file.size > limitBytes) {
      setErr(`Not enough space. You have ${formatBytes(limitBytes - usedBytes)} remaining.`); return;
    }
    const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
    if (!["pdf", "docx", "txt"].includes(ext)) {
      setErr("Only PDF, DOCX, and TXT files are supported."); return;
    }
    setUploading(true);
    try {
      const doc = await API.uploadUserKbDocument(file);
      setDocs(d => [...d, doc]);
      setUsedBytes(b => b + file.size);
    } catch (e: unknown) { setErr(e instanceof Error ? e.message : "Upload failed"); }
    setUploading(false);
  };

  const handleDelete = async (docId: string, fileSize: number) => {
    try {
      await API.deleteUserKbDocument(docId);
      setDocs(d => d.filter(x => x.id !== docId));
      setUsedBytes(b => b - fileSize);
    } catch { /* ignore */ }
  };

  const usedPct = Math.min(100, (usedBytes / limitBytes) * 100);
  const barColor = usedPct > 85 ? "#ef4444" : usedPct > 65 ? "#f59e0b" : "#6366f1";
  const indexedCount = docs.length; // all stored docs are indexed synchronously on upload

  const inp = { display: "none" };

  return (
    <div style={{ flex: 1, padding: "28px 32px 60px", overflowY: "auto" as const }}>
      <div style={{ maxWidth: 760 }}>
        <div style={{ marginBottom: 24 }}>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: "#111", margin: 0 }}>Knowledge Base</h1>
          <p style={{ color: "#aaa", fontSize: 14, margin: "4px 0 0" }}>
            Your personal document library - attach documents to agents from their KB section
          </p>
        </div>

        {/* Stat cards */}
        <div style={{ display: "flex", gap: 16, marginBottom: 20 }}>
          <StatCard label="Documents" value={docs.length} color="#6366f1" />
          <StatCard label="Indexed" value={indexedCount} color="#16a34a" />
          <div style={{
            flex: 1, background: "#fff", borderRadius: 14, padding: "16px 20px",
            border: "1px solid #f0f0f0",
          }}>
            <div style={{ fontSize: 12.5, color: "#999", fontWeight: 500, marginBottom: 6 }}>Storage used</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: "#111" }}>{formatBytes(usedBytes)}</div>
          </div>
        </div>

        {/* Storage bar */}
        <div style={{ background: "#fff", borderRadius: 12, border: "1px solid #f0f0f0", padding: "18px 20px", marginBottom: 20 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "#111" }}>Storage</span>
            <span style={{ fontSize: 12, color: "#888" }}>
              {formatBytes(usedBytes)} / {formatBytes(limitBytes)}
              <span style={{ color: "#bbb", marginLeft: 6 }}>· max {KB_FILE_MB} MB per file</span>
            </span>
          </div>
          <div style={{ height: 6, borderRadius: 3, background: "#f0f0f0", overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${usedPct}%`, borderRadius: 3, background: barColor, transition: "width 0.4s" }} />
          </div>
        </div>

        {/* Upload zone */}
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={e => { e.preventDefault(); (e.currentTarget as HTMLDivElement).style.borderColor = "#6366f1"; (e.currentTarget as HTMLDivElement).style.background = "#f5f5ff"; }}
          onDragLeave={e => { (e.currentTarget as HTMLDivElement).style.borderColor = "#d1d5db"; (e.currentTarget as HTMLDivElement).style.background = "#fff"; }}
          onDrop={async e => {
            e.preventDefault();
            (e.currentTarget as HTMLDivElement).style.borderColor = "#d1d5db";
            (e.currentTarget as HTMLDivElement).style.background = "#fff";
            const file = e.dataTransfer.files[0];
            if (file) await handleUpload(file);
          }}
          style={{
            border: "2px dashed #d1d5db", borderRadius: 12, padding: "28px 20px",
            textAlign: "center", cursor: "pointer", background: "#fff",
            transition: "border-color 0.15s, background 0.15s", marginBottom: 20,
          }}>
          <div style={{
            width: 40, height: 40, borderRadius: "50%", background: "#eef0ff",
            display: "flex", alignItems: "center", justifyContent: "center",
            margin: "0 auto 10px", color: "#6366f1",
          }}>
            {uploading ? (
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: "lumen-spin 0.8s linear infinite" }}>
                <path d="M21 12a9 9 0 1 1-9-9" />
              </svg>
            ) : (
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 3v12" /><path d="m7 8 5-5 5 5" /><path d="M5 21h14" />
              </svg>
            )}
          </div>
          <style>{`@keyframes lumen-spin { to { transform: rotate(360deg); } }`}</style>
          <div style={{ fontSize: 14, color: "#555", fontWeight: 500 }}>
            {uploading ? "Uploading..." : <>Drop files here or <span style={{ color: "#6366f1" }}>browse</span></>}
          </div>
          <div style={{ fontSize: 12, color: "#bbb", marginTop: 6 }}>
            PDF, DOCX, TXT · max {KB_FILE_MB} MB per file · {KB_TOTAL_MB} MB total
          </div>
        </div>
        <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" style={inp}
          onChange={e => { const f = e.target.files?.[0]; if (f) handleUpload(f); e.target.value = ""; }} />

        {err && (
          <div style={{ padding: "10px 14px", borderRadius: 8, background: "#fff5f5", border: "1px solid #fecaca", color: "#dc2626", fontSize: 13, marginBottom: 16 }}>
            {err}
          </div>
        )}

        {/* Document list */}
        {loading ? (
          <div style={{ color: "#aaa", fontSize: 14 }}>Loading...</div>
        ) : docs.length === 0 ? (
          <div style={{ color: "#bbb", fontSize: 14, textAlign: "center", padding: "40px 0" }}>
            No documents yet. Upload your first file above.
          </div>
        ) : (
          <div>
            {docs.map(doc => {
              const ext = doc.file_name.split(".").pop()?.toLowerCase() ?? "txt";
              const badge = ext === "pdf" ? { bg: "#eff6ff", color: "#3b82f6", label: "PDF" }
                : ext === "docx" ? { bg: "#f5f3ff", color: "#8b5cf6", label: "DOC" }
                : { bg: "#f0fdf4", color: "#16a34a", label: "TXT" };
              return (
                <div key={doc.id} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "14px 16px", borderRadius: 10, border: "1px solid #f0f0f0",
                  background: "#fff", marginBottom: 8,
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <span style={{
                      width: 38, height: 38, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 10.5, fontWeight: 700, background: badge.bg, color: badge.color, flexShrink: 0,
                    }}>
                      {badge.label}
                    </span>
                    <div>
                      <div style={{ fontSize: 14, fontWeight: 500, color: "#111" }}>{doc.display_name || doc.file_name}</div>
                      <div style={{ fontSize: 11, color: "#aaa", marginTop: 2, display: "flex", gap: 8 }}>
                        <span>{formatBytes(doc.file_size)}</span>
                        <span>·</span>
                        <span style={{ color: "#22c55e" }}>● indexed</span>
                        <span>·</span>
                        <span>{timeAgo(doc.created_at)}</span>
                      </div>
                    </div>
                  </div>
                  <button onClick={() => handleDelete(doc.id, doc.file_size)}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "#ccc", padding: 6, borderRadius: 6, transition: "color 0.15s", display: "flex" }}
                    onMouseEnter={e => (e.currentTarget.style.color = "#ef4444")}
                    onMouseLeave={e => (e.currentTarget.style.color = "#ccc")}>
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M3 6h18" /><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                    </svg>
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Call Analytics View ───────────────────────────────────────────────────────

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
}

function sentimentStyle(s: string | null | undefined): { bg: string; color: string; label: string } {
  if (s === "POSITIVE") return { bg: "#f0fdf4", color: "#16a34a", label: "Positive" };
  if (s === "NEGATIVE") return { bg: "#fff5f5", color: "#dc2626", label: "Negative" };
  return { bg: "#f5f5f5", color: "#888", label: "Neutral" };
}

function callStatusStyle(s: string | null | undefined): { bg: string; color: string; label: string } {
  if (s === "failed") return { bg: "#fff5f5", color: "#dc2626", label: "Failed" };
  if (s === "busy") return { bg: "#fff7ed", color: "#ea580c", label: "Busy" };
  if (s === "no_answer") return { bg: "#fff7ed", color: "#ea580c", label: "No Answer" };
  if (s === "in_progress") return { bg: "#eff6ff", color: "#2563eb", label: "In Progress" };
  if (s === "ringing") return { bg: "#eff6ff", color: "#2563eb", label: "Ringing" };
  return { bg: "#f0fdf4", color: "#16a34a", label: "Completed" };
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
    `Call status: ${callStatusStyle(conv.call_status).label}`,
    ...(conv.failure_reason ? [`Failure reason: ${conv.failure_reason}`] : []),
    `Sentiment: ${conv.sentiment ?? "N/A"}`,
    `Emotion: ${conv.dominant_emotion ?? "N/A"}`,
    "",
    "── Transcript ──",
    "",
    ...msgs.map(m => `[${m.role === "user" ? "User" : "Agent"}] ${m.content}`),
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = `session-${conv.id.slice(0, 8)}.txt`;
  a.click(); URL.revokeObjectURL(url);
}

function SessionDetail({ conv, agents, onBack }: {
  conv: ConvDetail;
  agents: Agent[];
  onBack: () => void;
}) {
  const [msgs, setMsgs] = useState<import("@/lib/api").Message[]>([]);
  const [loading, setLoading] = useState(true);
  const agentName = agents.find(a => a.id === conv.agent_id)?.name ?? "Voice Agent";
  const sStyle = sentimentStyle(conv.sentiment);
  const cStyle = callStatusStyle(conv.call_status);
  const failed = FAILED_CALL_STATUSES.has(conv.call_status ?? "");

  useEffect(() => {
    API.messages(conv.id).then(m => { setMsgs(m); setLoading(false); }).catch(() => setLoading(false));
  }, [conv.id]);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: "100vh" }}>
      {/* Header */}
      <div style={{ padding: "16px 28px", borderBottom: "1px solid #f0f0f0", background: "#fff", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button onClick={onBack}
            style={{ background: "none", border: "none", cursor: "pointer", color: "#999", fontSize: 12, padding: 0 }}>
            ← Call Analytics
          </button>
          <span style={{ color: "#ddd" }}>/</span>
          <span style={{ fontSize: 14, fontWeight: 600, color: "#111" }}>{conv.title}</span>
        </div>
        <button onClick={() => downloadTranscript(conv, msgs)}
          style={{ padding: "7px 14px", borderRadius: 8, border: "1px solid #e0e0e0", background: "#fff", cursor: "pointer", fontSize: 13, fontWeight: 500 }}>
          ↓ Download
        </button>
      </div>

      {/* Failure banner */}
      {failed && (
        <div style={{ padding: "12px 28px", background: "#fff5f5", borderBottom: "1px solid #fecaca", display: "flex", alignItems: "center", gap: 10 }}>
          <span style={{ fontSize: 16 }}>⚠️</span>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "#dc2626" }}>Call {cStyle.label.toLowerCase()}</div>
            <div style={{ fontSize: 12, color: "#b91c1c", marginTop: 1 }}>
              {conv.failure_reason || "No further details were recorded for this call."}
            </div>
          </div>
        </div>
      )}

      {/* Meta bar */}
      <div style={{ padding: "12px 28px", background: "#fafafa", borderBottom: "1px solid #f0f0f0", display: "flex", gap: 20, alignItems: "center", flexWrap: "wrap" }}>
        <div style={{ fontSize: 12, color: "#888" }}>
          <span style={{ color: "#555", fontWeight: 500 }}>Agent:</span> {agentName}
        </div>
        <div style={{ fontSize: 12, color: "#888" }}>
          <span style={{ color: "#555", fontWeight: 500 }}>Date:</span> {new Date(conv.created_at).toLocaleString()}
        </div>
        {!loading && msgs.length > 0 && (
          <div style={{ fontSize: 12, color: "#888" }}>
            <span style={{ color: "#555", fontWeight: 500 }}>Duration:</span> {durationLabel(msgs)}
          </div>
        )}
        <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, background: cStyle.bg, color: cStyle.color }}>
          {cStyle.label}
        </span>
        {conv.sentiment && (
          <span style={{ fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20, background: sStyle.bg, color: sStyle.color }}>
            {sStyle.label}
            {conv.dominant_emotion && ` · ${conv.dominant_emotion}`}
          </span>
        )}
      </div>

      {/* Transcript */}
      <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px 60px" }}>
        {loading ? (
          <div style={{ color: "#aaa", fontSize: 14 }}>Loading transcript...</div>
        ) : msgs.length === 0 ? (
          <div style={{ color: "#bbb", fontSize: 14 }}>No messages in this session.</div>
        ) : (
          <div style={{ maxWidth: 700 }}>
            {msgs.map((m, i) => (
              <div key={i} style={{
                display: "flex", gap: 12, marginBottom: 16,
                flexDirection: m.role === "user" ? "row-reverse" : "row",
              }}>
                <div style={{
                  width: 30, height: 30, borderRadius: "50%", flexShrink: 0,
                  background: m.role === "user" ? "#6366f1" : "#f0f0f0",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 12, color: m.role === "user" ? "#fff" : "#888", fontWeight: 600,
                }}>
                  {m.role === "user" ? "U" : "A"}
                </div>
                <div style={{
                  maxWidth: "70%", padding: "10px 14px", borderRadius: 12,
                  background: m.role === "user" ? "#6366f1" : "#fff",
                  color: m.role === "user" ? "#fff" : "#111",
                  border: m.role === "user" ? "none" : "1px solid #f0f0f0",
                  fontSize: 14, lineHeight: 1.6,
                }}>
                  {m.content}
                  <div style={{ fontSize: 10, marginTop: 4, opacity: 0.6, textAlign: m.role === "user" ? "left" : "right" }}>
                    {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
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
    API.conversations().then(data => {
      setConvs(data as ConvDetail[]);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  if (selected) {
    return <SessionDetail conv={selected} agents={agents} onBack={() => setSelected(null)} />;
  }

  const total = convs.length;
  const failedCount = convs.filter(c => FAILED_CALL_STATUSES.has(c.call_status ?? "")).length;
  const completedCount = convs.filter(c => (c.call_status ?? "completed") === "completed").length;
  const inProgressCount = convs.filter(c => ACTIVE_CALL_STATUSES.has(c.call_status ?? "")).length;
  const successRate = total > 0 ? Math.round((completedCount / total) * 100) : null;

  const filtered = convs.filter(c => {
    if (agentFilter !== "all" && c.agent_id !== agentFilter) return false;
    if (filter === "failed") return FAILED_CALL_STATUSES.has(c.call_status ?? "");
    if (filter === "completed") return (c.call_status ?? "completed") === "completed";
    return true;
  });

  return (
    <div style={{ flex: 1, padding: "28px 28px 60px", overflowY: "auto" as const }}>
      <div style={{ marginBottom: 24 }}>
        <h1 style={{ fontSize: 24, fontWeight: 700, color: "#111", margin: 0 }}>Call Analytics</h1>
        <p style={{ color: "#aaa", fontSize: 14, margin: "4px 0 0" }}>Every voice conversation — outcomes, sentiment, and why calls fail</p>
      </div>

      {!loading && total > 0 && (
        <div style={{ display: "flex", gap: 14, marginBottom: 24, flexWrap: "wrap" }}>
          <StatCard label="Total Calls" value={total} color="#111" />
          <StatCard label="Completed" value={completedCount} color="#16a34a" />
          <StatCard label="Failed / Busy / No Answer" value={failedCount} color="#dc2626" />
          {inProgressCount > 0 && <StatCard label="In Progress" value={inProgressCount} color="#2563eb" />}
          <div style={{ flex: 1, background: "#fff", borderRadius: 14, padding: "16px 20px", border: "1px solid #f0f0f0", minWidth: 140 }}>
            <div style={{ fontSize: 12.5, color: "#999", fontWeight: 500, marginBottom: 6 }}>Success Rate</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: successRate !== null && successRate < 70 ? "#dc2626" : "#16a34a" }}>
              {successRate !== null ? `${successRate}%` : "—"}
            </div>
          </div>
        </div>
      )}

      {!loading && total > 0 && (
        <div style={{ display: "flex", gap: 10, marginBottom: 18, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ display: "flex", gap: 6 }}>
            {([["all", "All"], ["completed", "Completed"], ["failed", "Failed"]] as [CallFilter, string][]).map(([key, label]) => (
              <button key={key} onClick={() => setFilter(key)}
                style={{
                  padding: "6px 14px", borderRadius: 20, border: "1px solid #e5e5e5", cursor: "pointer",
                  fontSize: 12.5, fontWeight: 600,
                  background: filter === key ? "#111" : "#fff",
                  color: filter === key ? "#fff" : "#555",
                }}>
                {label}
              </button>
            ))}
          </div>
          {agents.length > 0 && (
            <select value={agentFilter} onChange={e => setAgentFilter(e.target.value)}
              style={{ padding: "6px 12px", borderRadius: 20, border: "1px solid #e5e5e5", fontSize: 12.5, color: "#555", background: "#fff", cursor: "pointer" }}>
              <option value="all">All agents</option>
              {agents.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          )}
        </div>
      )}

      {loading ? (
        <div style={{ color: "#aaa", fontSize: 14 }}>Loading call analytics...</div>
      ) : convs.length === 0 ? (
        <div style={{ color: "#bbb", fontSize: 14, textAlign: "center", padding: "60px 0" }}>
          No calls yet. Start a voice conversation with an agent.
        </div>
      ) : filtered.length === 0 ? (
        <div style={{ color: "#bbb", fontSize: 14, textAlign: "center", padding: "60px 0" }}>
          No calls match this filter.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 14 }}>
          {filtered.map(conv => {
            const sStyle = sentimentStyle(conv.sentiment);
            const cStyle = callStatusStyle(conv.call_status);
            const isFailed = FAILED_CALL_STATUSES.has(conv.call_status ?? "");
            const agentName = agents.find(a => a.id === conv.agent_id)?.name;
            return (
              <div key={conv.id} onClick={() => setSelected(conv)}
                style={{
                  background: "#fff", borderRadius: 14, padding: "18px 20px",
                  border: isFailed ? "1px solid #fecaca" : "1px solid #f0f0f0", cursor: "pointer",
                  transition: "box-shadow 0.15s, transform 0.1s",
                }}
                onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = "0 4px 24px rgba(0,0,0,0.08)"; (e.currentTarget as HTMLDivElement).style.transform = "translateY(-1px)"; }}
                onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.boxShadow = "none"; (e.currentTarget as HTMLDivElement).style.transform = "none"; }}>
                {/* Icon */}
                <div style={{
                  width: 40, height: 40, borderRadius: 10, background: isFailed ? "#fff5f5" : "#f0f0ff",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 18, marginBottom: 12,
                }}>{isFailed ? "⚠️" : "💬"}</div>

                <div style={{ fontWeight: 600, fontSize: 15, color: "#111", marginBottom: 4 }}>{conv.title}</div>
                {agentName && (
                  <div style={{ fontSize: 12, color: "#6366f1", marginBottom: 4, fontWeight: 500 }}>{agentName}</div>
                )}
                <div style={{ fontSize: 12, color: "#aaa", marginBottom: 12 }}>{timeAgo(conv.created_at)}</div>

                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: isFailed ? 8 : 0 }}>
                  <span style={{
                    fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20,
                    background: cStyle.bg, color: cStyle.color,
                  }}>
                    {cStyle.label}
                  </span>
                  {conv.sentiment && (
                    <span style={{
                      fontSize: 11, fontWeight: 600, padding: "3px 10px", borderRadius: 20,
                      background: sStyle.bg, color: sStyle.color,
                    }}>
                      {sStyle.label}{conv.dominant_emotion ? ` · ${conv.dominant_emotion}` : ""}
                    </span>
                  )}
                </div>

                {isFailed && (
                  <div style={{ fontSize: 11.5, color: "#b91c1c", lineHeight: 1.4, overflow: "hidden", textOverflow: "ellipsis", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                    {conv.failure_reason || "No further details recorded."}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Agents Grid ───────────────────────────────────────────────────────────────

function StatCard({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div style={{
      flex: 1, background: "#fff", borderRadius: 14, padding: "16px 20px",
      border: "1px solid #f0f0f0",
    }}>
      <div style={{ fontSize: 12.5, color: "#999", fontWeight: 500, marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

function AgentsGrid({ agents, onSelect, onToggle, onCreateClick }: {
  agents: Agent[];
  onSelect: (a: Agent) => void;
  onToggle: (id: string, active: boolean) => Promise<void>;
  onCreateClick: () => void;
}) {
  const [query, setQuery] = useState("");
  const filtered = query.trim()
    ? agents.filter(a => a.name.toLowerCase().includes(query.trim().toLowerCase()))
    : agents;

  const liveCount = agents.filter(a => a.is_active).length;
  const updatedTodayCount = agents.filter(a => isToday(a.updated_at)).length;

  return (
    <div style={{ flex: 1, padding: "28px 28px 60px", overflowY: "auto" }}>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", marginBottom: 24, gap: 16, flexWrap: "wrap" }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 700, color: "#111", margin: 0 }}>Agents</h1>
          <p style={{ color: "#aaa", fontSize: 14, margin: "4px 0 0" }}>Build and manage your voice AI agents</p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ position: "relative" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#aaa" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
              style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)" }}>
              <circle cx="11" cy="11" r="8" /><path d="m21 21-4.3-4.3" />
            </svg>
            <input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search agents"
              style={{
                padding: "9px 12px 9px 32px", borderRadius: 9, border: "1px solid #e5e5e5",
                fontSize: 13.5, width: 200, outline: "none", color: "#333",
              }} />
          </div>
          <button onClick={onCreateClick} style={{
            display: "flex", alignItems: "center", gap: 6, padding: "9px 16px", borderRadius: 9,
            border: "none", background: "#6366f1", color: "#fff", fontSize: 13.5, fontWeight: 600, cursor: "pointer",
          }}>
            <span style={{ fontSize: 15, lineHeight: 1 }}>+</span> New agent
          </button>
        </div>
      </div>

      <div style={{ display: "flex", gap: 16, marginBottom: 24 }}>
        <StatCard label="Total agents" value={agents.length} color="#6366f1" />
        <StatCard label="Live now" value={liveCount} color="#16a34a" />
        <StatCard label="Updated today" value={updatedTodayCount} color="#111" />
      </div>

      <div style={{
        background: "linear-gradient(135deg, #f0f0ff 0%, #fff0f8 50%, #f0f8ff 100%)",
        borderRadius: 16, padding: 24, border: "1px solid #ebebff",
      }}>
        {filtered.length === 0 && query.trim() ? (
          <div style={{ padding: "20px 4px", color: "#999", fontSize: 13.5 }}>
            No agents match &quot;{query}&quot;
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 16 }}>
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
    </div>
  );
}

// ── Phone Numbers View ────────────────────────────────────────────────────────

interface TwilioSettings { account_sid: string; auth_token: string; auth_token_set?: boolean; webhook_base_url: string; }

function PhoneNumbersView({ agents }: { agents: Agent[] }) {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [addPhone, setAddPhone] = useState("");
  const [addName, setAddName] = useState("");
  const [addError, setAddError] = useState("");
  const [saving, setSaving] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editName, setEditName] = useState("");

  // Twilio credentials panel
  const [showTwilio, setShowTwilio] = useState(false);
  const [twilioSettings, setTwilioSettings] = useState<TwilioSettings>({ account_sid: "", auth_token: "", webhook_base_url: "" });
  const [twilioSaving, setTwilioSaving] = useState(false);
  const [twilioSaved, setTwilioSaved] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [nums, ts] = await Promise.all([
        API.phoneNumbers(),
        API.request<TwilioSettings>("/api/twilio/settings"),
      ]);
      setNumbers(nums);
      setTwilioSettings(ts);
      // Auto-open if not yet configured
      if (!ts.account_sid) setShowTwilio(true);
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const handleSaveTwilio = async () => {
    setTwilioSaving(true);
    try {
      await API.request("/api/twilio/settings", { method: "PUT", body: JSON.stringify(twilioSettings) });
      setTwilioSaved(true);
      setTimeout(() => setTwilioSaved(false), 2000);
    } catch { /* ignore */ }
    setTwilioSaving(false);
  };

  const handleAdd = async () => {
    if (!addPhone.trim()) { setAddError("Phone number is required"); return; }
    setSaving(true); setAddError("");
    try {
      const n = await API.addPhoneNumber({ phone_number: addPhone.trim(), friendly_name: addName.trim() || undefined });
      setNumbers(prev => [...prev, n]);
      setAddPhone(""); setAddName(""); setShowAdd(false);
    } catch (e: unknown) {
      setAddError(e instanceof Error ? e.message : "Failed to add");
    }
    setSaving(false);
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Delete this phone number?")) return;
    await API.deletePhoneNumber(id);
    setNumbers(prev => prev.filter(n => n.id !== id));
  };

  const handleAttach = async (numberId: string, agentId: string | null) => {
    let updated: PhoneNumber;
    if (agentId) {
      updated = await API.attachPhoneNumber(numberId, agentId);
    } else {
      updated = await API.detachPhoneNumber(numberId);
    }
    setNumbers(prev => prev.map(n => n.id === numberId ? updated : n));
  };

  const handleEditName = async (id: string) => {
    try {
      const updated = await API.updatePhoneNumber(id, { friendly_name: editName.trim() || undefined });
      setNumbers(prev => prev.map(n => n.id === id ? updated : n));
      setEditingId(null);
    } catch { /* ignore */ }
  };

  const inp = {
    padding: "8px 12px", border: "1px solid #e0e0e0", borderRadius: 8,
    fontSize: 13, outline: "none", color: "#111", background: "#fff", width: "100%", boxSizing: "border-box" as const,
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}>
      <div style={{
        padding: "14px 28px", borderBottom: "1px solid #f0f0f0",
        background: "#fff", display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: "#111" }}>Phone Numbers</span>
        <button onClick={() => setShowAdd(true)} style={{
          padding: "7px 16px", background: "#6366f1", color: "#fff",
          border: "none", borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 500,
        }}>+ Add Number</button>
      </div>

      {/* Twilio credentials panel */}
      <div style={{ borderBottom: "1px solid #f0f0f0", background: showTwilio ? "#fafafa" : "#fff" }}>
        <button
          onClick={() => setShowTwilio(v => !v)}
          style={{
            width: "100%", display: "flex", alignItems: "center", justifyContent: "space-between",
            padding: "12px 28px", background: "none", border: "none", cursor: "pointer",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 16 }}>#</span>
            <span style={{ fontSize: 13, fontWeight: 600, color: "#333" }}>Twilio Connection</span>
            {twilioSettings.account_sid ? (
              <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 20, background: "#f0fdf4", color: "#16a34a", border: "1px solid #bbf7d0" }}>Connected</span>
            ) : (
              <span style={{ fontSize: 11, padding: "2px 8px", borderRadius: 20, background: "#fef9ec", color: "#d97706", border: "1px solid #fde68a" }}>Not configured</span>
            )}
          </div>
          <span style={{ color: "#aaa", fontSize: 12 }}>{showTwilio ? "▲" : "▼"}</span>
        </button>

        {showTwilio && (
          <div style={{ padding: "0 28px 20px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: "#666", display: "block", marginBottom: 4 }}>Account SID</label>
                <input
                  style={{ ...inp, fontSize: 12 }}
                  placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                  value={twilioSettings.account_sid}
                  onChange={e => setTwilioSettings(s => ({ ...s, account_sid: e.target.value }))}
                />
              </div>
              <div>
                <label style={{ fontSize: 11, fontWeight: 600, color: "#666", display: "block", marginBottom: 4 }}>Auth Token</label>
                <input
                  style={{ ...inp, fontSize: 12 }}
                  type="password"
                  placeholder={twilioSettings.auth_token_set ? "••••••••" : "Enter auth token"}
                  value={twilioSettings.auth_token}
                  onChange={e => setTwilioSettings(s => ({ ...s, auth_token: e.target.value }))}
                />
              </div>
            </div>
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 11, fontWeight: 600, color: "#666", display: "block", marginBottom: 4 }}>
                Server Base URL <span style={{ fontWeight: 400, color: "#aaa" }}>(public URL of this server - use ngrok for local testing)</span>
              </label>
              <input
                style={{ ...inp, fontSize: 12 }}
                placeholder="https://your-server.com"
                value={twilioSettings.webhook_base_url}
                onChange={e => setTwilioSettings(s => ({ ...s, webhook_base_url: e.target.value }))}
              />
              {twilioSettings.webhook_base_url && (
                <div style={{ marginTop: 6, fontSize: 11, color: "#888" }}>
                  Set this as Voice webhook in Twilio console → HTTP POST:&nbsp;
                  <code style={{ background: "#f3f4f6", padding: "1px 5px", borderRadius: 4 }}>
                    {twilioSettings.webhook_base_url}/twilio/voice
                  </code>
                </div>
              )}
            </div>
            <button
              onClick={handleSaveTwilio}
              disabled={twilioSaving}
              style={{
                padding: "7px 18px", background: twilioSaved ? "#16a34a" : "#6366f1",
                color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 500,
              }}
            >
              {twilioSaving ? "Saving..." : twilioSaved ? "✓ Saved" : "Save Twilio Settings"}
            </button>
          </div>
        )}
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "24px 28px" }}>
        {loading ? (
          <div style={{ color: "#aaa", fontSize: 14 }}>Loading...</div>
        ) : numbers.length === 0 ? (
          <div style={{
            textAlign: "center", padding: "60px 0", color: "#aaa",
          }}>
            <div style={{ fontSize: 36, marginBottom: 12 }}>#</div>
            <div style={{ fontSize: 14 }}>No phone numbers yet.</div>
            <div style={{ fontSize: 12, marginTop: 6 }}>Add a Twilio number to connect it to an agent.</div>
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {numbers.map(num => {
              const attachedAgent = agents.find(a => a.id === num.agent_id);
              const isEditing = editingId === num.id;
              return (
                <div key={num.id} style={{
                  background: "#fff", border: "1px solid #f0f0f0", borderRadius: 14,
                  padding: "18px 20px", display: "flex", alignItems: "center", gap: 16,
                }}>
                  <div style={{
                    width: 44, height: 44, borderRadius: "50%", flexShrink: 0,
                    background: num.agent_id ? "#f0fdf4" : "#f5f5f5",
                    border: `2px solid ${num.agent_id ? "#bbf7d0" : "#e5e5e5"}`,
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 18, color: num.agent_id ? "#22c55e" : "#bbb",
                  }}>
                    #
                  </div>

                  <div style={{ flex: 1, minWidth: 0 }}>
                    {isEditing ? (
                      <div style={{ display: "flex", gap: 8, marginBottom: 4 }}>
                        <input
                          style={{ ...inp, width: 180 }}
                          value={editName}
                          onChange={e => setEditName(e.target.value)}
                          onKeyDown={e => { if (e.key === "Enter") handleEditName(num.id); if (e.key === "Escape") setEditingId(null); }}
                          autoFocus
                        />
                        <button onClick={() => handleEditName(num.id)} style={{
                          padding: "6px 12px", background: "#6366f1", color: "#fff",
                          border: "none", borderRadius: 6, cursor: "pointer", fontSize: 12,
                        }}>Save</button>
                        <button onClick={() => setEditingId(null)} style={{
                          padding: "6px 10px", background: "#f5f5f5", color: "#666",
                          border: "1px solid #e0e0e0", borderRadius: 6, cursor: "pointer", fontSize: 12,
                        }}>Cancel</button>
                      </div>
                    ) : (
                      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                        <span style={{ fontWeight: 600, fontSize: 14, color: "#111" }}>
                          {num.friendly_name || num.phone_number}
                        </span>
                        {num.friendly_name && (
                          <span style={{ fontSize: 11, color: "#aaa" }}>{num.phone_number}</span>
                        )}
                        <button onClick={() => { setEditingId(num.id); setEditName(num.friendly_name ?? ""); }}
                          style={{ background: "none", border: "none", cursor: "pointer", color: "#bbb", fontSize: 11, padding: "0 2px" }}
                          title="Edit name">✏</button>
                      </div>
                    )}
                    <div style={{ fontSize: 12, color: "#888", marginTop: 2 }}>
                      {attachedAgent ? (
                        <span>Connected to <strong style={{ color: "#6366f1" }}>{attachedAgent.name}</strong></span>
                      ) : (
                        <span style={{ color: "#bbb" }}>Not connected to any agent</span>
                      )}
                    </div>
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: 10, flexShrink: 0 }}>
                    <select
                      value={num.agent_id ?? ""}
                      onChange={e => handleAttach(num.id, e.target.value || null)}
                      style={{
                        padding: "6px 10px", border: "1px solid #e0e0e0", borderRadius: 8,
                        fontSize: 12, color: "#333", background: "#fff", cursor: "pointer", outline: "none",
                      }}
                    >
                      <option value="">No agent</option>
                      {agents.filter(a => a.is_active).map(a => (
                        <option key={a.id} value={a.id}>{a.name}</option>
                      ))}
                    </select>

                    <button onClick={() => handleDelete(num.id)} style={{
                      background: "none", border: "1px solid #fee2e2", borderRadius: 8,
                      color: "#ef4444", fontSize: 12, padding: "6px 10px", cursor: "pointer",
                    }}>Delete</button>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {showAdd && (
        <div style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.4)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
        }} onClick={() => setShowAdd(false)}>
          <div style={{
            background: "#fff", borderRadius: 16, padding: 28, width: 420,
            boxShadow: "0 20px 60px rgba(0,0,0,0.15)",
          }} onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 16, fontWeight: 700, color: "#111", marginBottom: 20 }}>Add Phone Number</div>

            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "#555", display: "block", marginBottom: 5 }}>
                Phone Number (E.164 format) *
              </label>
              <input
                style={inp}
                placeholder="+12025550101"
                value={addPhone}
                onChange={e => setAddPhone(e.target.value)}
              />
            </div>

            <div style={{ marginBottom: 20 }}>
              <label style={{ fontSize: 12, fontWeight: 600, color: "#555", display: "block", marginBottom: 5 }}>
                Friendly Name (optional)
              </label>
              <input
                style={inp}
                placeholder="e.g. Support Line"
                value={addName}
                onChange={e => setAddName(e.target.value)}
              />
            </div>

            {addError && (
              <div style={{ fontSize: 12, color: "#ef4444", marginBottom: 14 }}>{addError}</div>
            )}

            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button onClick={() => { setShowAdd(false); setAddPhone(""); setAddName(""); setAddError(""); }}
                style={{ padding: "9px 18px", border: "1px solid #e0e0e0", borderRadius: 8, background: "#fff", color: "#555", cursor: "pointer", fontSize: 13 }}>
                Cancel
              </button>
              <button onClick={handleAdd} disabled={saving}
                style={{ padding: "9px 18px", background: "#6366f1", color: "#fff", border: "none", borderRadius: 8, cursor: "pointer", fontSize: 13, fontWeight: 500, opacity: saving ? 0.7 : 1 }}>
                {saving ? "Adding..." : "Add Number"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── HR Manager View ───────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, { bg: string; text: string; border: string }> = {
  pending:   { bg: "#fef9ec", text: "#d97706", border: "#fde68a" },
  calling:   { bg: "#eff6ff", text: "#2563eb", border: "#bfdbfe" },
  completed: { bg: "#f0fdf4", text: "#16a34a", border: "#bbf7d0" },
  failed:    { bg: "#fef2f2", text: "#dc2626", border: "#fecaca" },
  cancelled: { bg: "#f5f5f5", text: "#888",    border: "#e5e5e5" },
};


// ── Main Page ─────────────────────────────────────────────────────────────────

export default function AgentsPage() {
  const router = useRouter();
  const [view, setView] = useState<View>("agents");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);

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
    } catch (e) {
      alert((e as Error).message ?? "Failed to update agent status");
    }
  };

  const handleSignOut = () => { clearTokens(); router.replace("/signin"); };

  return (
    <div style={{
      display: "flex", minHeight: "100vh", background: "#f8f8f8",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    }}>
      <Sidebar view={view} onView={v => { setView(v); setSelectedAgent(null); }} />

      {view === "agents" && (
        selectedAgent ? (
          <AgentDetail agent={selectedAgent} onBack={() => setSelectedAgent(null)}
            onUpdate={updated => { setAgents(ag => ag.map(a => a.id === updated.id ? updated : a)); setSelectedAgent(updated); }}
            onDuplicate={dup => { setAgents(ag => [...ag, dup]); setSelectedAgent(dup); }}
            onGoToKb={() => { setSelectedAgent(null); setView("knowledge-base"); }}
          />
        ) : (
          <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
            <div style={{ padding: "12px 28px", borderBottom: "1px solid #f0f0f0", background: "#fff", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 12.5, fontWeight: 500, color: "#999" }}>Agents</span>
              <button onClick={handleSignOut} style={{ fontSize: 12.5, fontWeight: 500, color: "#999", background: "none", border: "none", cursor: "pointer" }}>Sign out</button>
            </div>
            {loading ? (
              <div style={{ padding: 40, color: "#aaa", fontSize: 14 }}>Loading agents...</div>
            ) : (
              <AgentsGrid agents={agents} onSelect={setSelectedAgent} onToggle={handleToggle} onCreateClick={() => setShowCreate(true)} />
            )}
          </div>
        )
      )}

      {view === "knowledge-base" && <KnowledgeBaseView />}

      {view === "sessions" && <SessionsView agents={agents} />}

      {view === "phone-numbers" && <PhoneNumbersView agents={agents} />}

      {showCreate && (
        <CreateAgentModal
          onClose={() => setShowCreate(false)}
          onCreate={agent => { setAgents(ag => [...ag, agent]); setShowCreate(false); setSelectedAgent(agent); }}
        />
      )}
    </div>
  );
}
