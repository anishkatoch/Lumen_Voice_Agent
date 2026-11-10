"use client";
import { useState, useEffect, useCallback } from "react";
import {
  Settings, Plug, HardDrive, KeyRound, LogOut, Check, X, RefreshCw,
  Eye, EyeOff, Loader2, Zap, Mic, Cpu, ChevronRight, CheckCircle2,
  XCircle, Activity, PlayCircle, Phone, ArrowLeft, Search, Clock,
  Calendar, DollarSign,
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
  tts_elevenlabs_voice_id: string;
  tts_openai_voice: string;
  tts_openai_model: string;
  llm_provider:     string;
  llm_model:        string;
  llm_api_key:      string;
  llm_base_url:     string;
  user_kb_max_mb:   string;
  agent_kb_max_mb:  string;
  voice_pipeline:   string;
  realtime_voice:   string;
}

interface KbRequest {
  id: string; user_id: string; user_email: string;
  status: string; created_at: string;
}
interface KbUser { user_id: string; granted_at?: string | null; }

interface ProviderStatus { configured: boolean; endpoint?: string | null; deployment?: string | null; }
interface SystemStatus {
  providers: {
    azure_openai: ProviderStatus; openai: ProviderStatus; groq: ProviderStatus;
    anthropic: ProviderStatus; deepgram: ProviderStatus; elevenlabs: ProviderStatus;
  };
  active_pipeline: {
    voice_pipeline: string;
    stt: string; tts: string;
    llm: { provider: string; model: string; note: string | null };
    realtime_voice: string;
  };
}

interface ElevenLabsVoice {
  voice_id: string; name: string; category: string | null;
  preview_url: string | null; free: boolean;
}

interface LlmTestResult {
  ok: boolean; provider: string; model: string; latency_ms?: number;
  error?: string; attempts?: { provider: string; model: string; latency_ms: number; error: string }[];
}

interface CallAccount {
  user_id: string; email: string; call_count: number; last_call_at: string;
}

interface AdminConversation {
  id: string;
  title: string;
  created_at: string;
  agent_id?: string | null;
  sentiment?: "POSITIVE" | "NEGATIVE" | "NEUTRAL" | null;
  dominant_emotion?: string | null;
  call_status?: "ringing" | "in_progress" | "completed" | "no_response" | "failed" | "busy" | "no_answer" | null;
  failure_reason?: string | null;
  summary?: string | null;
  to_number?: string | null;
  from_number?: string | null;
  duration_seconds?: number | null;
  cost_usd?: number | null;
}

interface AdminMessage {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

interface AdminConversationQA {
  questions: { question: string; answered: boolean; answer: string | null }[];
}

type Tab = "providers" | "storage" | "kb" | "status" | "calls";
const NAV_ITEMS: { id: Tab; label: string; icon: typeof Plug }[] = [
  { id: "providers", label: "Providers", icon: Plug },
  { id: "calls",     label: "Call Analytics", icon: Phone },
  { id: "status",    label: "System status", icon: Activity },
  { id: "storage",   label: "Storage",   icon: HardDrive },
  { id: "kb",        label: "KB Access", icon: KeyRound },
];

// Curated model lists so the admin picks from models that actually exist,
// instead of guessing a free-text string - "Custom..." still escapes to a
// text field for anything not listed (new releases, fine-tunes, etc).
const MODEL_OPTIONS: Record<string, string[]> = {
  openai: ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1", "gpt-4.1-nano", "gpt-5-mini", "gpt-5", "o3-mini", "o3"],
  groq: ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "mixtral-8x7b-32768", "gemma2-9b-it"],
  anthropic: ["claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-5"],
};

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

function ModelSelect({ label, value, onChange, options, hint, customPlaceholder }: {
  label: string; value: string; onChange: (v: string) => void; options: string[];
  hint?: string; customPlaceholder: string;
}) {
  const isKnown = options.includes(value);
  const [custom, setCustom] = useState(!isKnown);
  useEffect(() => { setCustom(!options.includes(value)); }, [value, options]);

  return (
    <div className="field">
      <label>{label}</label>
      {!custom ? (
        <select className="select" value={value}
          onChange={e => e.target.value === "__custom__" ? setCustom(true) : onChange(e.target.value)}>
          {options.map(m => <option key={m} value={m}>{m}</option>)}
          <option value="__custom__">Custom…</option>
        </select>
      ) : (
        <div style={{ display: "flex", gap: 8 }}>
          <input className="input" value={value} onChange={e => onChange(e.target.value)} placeholder={customPlaceholder} autoFocus />
          {options.length > 0 && (
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => { setCustom(false); onChange(options[0]); }}>
              Use list
            </button>
          )}
        </div>
      )}
      {hint && <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 5, lineHeight: 1.5 }}>{hint}</p>}
    </div>
  );
}

function ElevenLabsVoicePicker({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const [voices, setVoices] = useState<ElevenLabsVoice[] | null>(null);
  const [err, setErr] = useState("");
  const [showAll, setShowAll] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);

  useEffect(() => {
    api<ElevenLabsVoice[]>("/api/admin/elevenlabs-voices")
      .then(setVoices)
      .catch(e => setErr(e instanceof Error ? e.message : "Failed to load voices"));
  }, []);

  function preview(v: ElevenLabsVoice) {
    if (!v.preview_url) return;
    const audio = new Audio(v.preview_url);
    setPlaying(v.voice_id);
    audio.play().catch(() => {});
    audio.onended = () => setPlaying(p => (p === v.voice_id ? null : p));
  }

  if (err) return <Notice>Couldn&apos;t load ElevenLabs voices: {err}</Notice>;
  if (!voices) return <p style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>Loading voices…</p>;

  const shown = showAll ? voices : voices.filter(v => v.free);

  return (
    <div className="field">
      <label>Voice</label>
      <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: -2, marginBottom: 8 }}>
        {showAll ? "All voices on this account." : "Free voices only - usable on any plan, including the free tier."}
        {" "}
        <button type="button" className="btn btn-ghost btn-sm" style={{ padding: "1px 6px" }}
          onClick={() => setShowAll(s => !s)}>
          {showAll ? "Show free only" : "Show all"}
        </button>
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, maxHeight: 280, overflowY: "auto" }}>
        {shown.map(v => (
          <label key={v.voice_id} style={{
            display: "flex", alignItems: "center", gap: 10, padding: "8px 10px",
            borderRadius: "var(--r-md)", border: "1px solid var(--border)",
            background: value === v.voice_id ? "var(--accent-soft)" : "transparent",
            cursor: "pointer",
          }}>
            <input type="radio" name="elevenlabs_voice" checked={value === v.voice_id}
              onChange={() => onChange(v.voice_id)} />
            <span style={{ flex: 1, fontSize: 13 }}>
              {v.name}
              <span style={{ color: "var(--ink-muted)", fontSize: 11.5, marginLeft: 6 }}>
                {v.category}{!v.free && " · paid tier"}
              </span>
            </span>
            {v.preview_url && (
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => preview(v)}>
                <PlayCircle size={14} style={playing === v.voice_id ? { color: "var(--accent)" } : undefined} />
              </button>
            )}
          </label>
        ))}
        {shown.length === 0 && (
          <p style={{ fontSize: 12.5, color: "var(--ink-muted)" }}>No voices found.</p>
        )}
      </div>
      <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 8, lineHeight: 1.5 }}>
        Leave unset to use the ELEVENLABS_VOICE_ID environment default.
      </p>
    </div>
  );
}

const PIPELINE_PRESETS = [
  {
    id: "deepgram_elevenlabs", icon: Mic, title: "Deepgram + ElevenLabs",
    desc: "Streaming speech recognition with low latency, and premium natural-sounding voices. Default, best for live calls.",
    stt: "deepgram", tts: "elevenlabs",
  },
  {
    id: "openai", icon: Cpu, title: "OpenAI (Whisper + TTS)",
    desc: "Single-vendor pipeline using your OpenAI key for both transcription and speech. Batch transcription (not streaming).",
    stt: "openai_whisper", tts: "openai_tts",
  },
] as const;

function PipelinePresetPicker({ stt, tts, onPick }: { stt: string; tts: string; onPick: (stt: string, tts: string) => void }) {
  const activeId = PIPELINE_PRESETS.find(p => p.stt === stt && p.tts === tts)?.id;
  return (
    <div>
      <div className="preset-grid">
        {PIPELINE_PRESETS.map(p => (
          <button key={p.id} type="button" className={`preset-card${activeId === p.id ? " active" : ""}`}
            onClick={() => onPick(p.stt, p.tts)}>
            <div className="preset-card-top">
              <div className="preset-card-icon"><p.icon size={17} /></div>
              {activeId === p.id && <CheckCircle2 size={18} style={{ color: "var(--accent)" }} />}
            </div>
            <div className="preset-card-title">{p.title}</div>
            <div className="preset-card-desc">{p.desc}</div>
          </button>
        ))}
      </div>
      {!activeId && (
        <Notice>Custom mix in use (STT and TTS don&apos;t match a preset) — see the sections below to fine-tune each one.</Notice>
      )}
    </div>
  );
}

function LlmTestButton({ provider, model, unsaved }: { provider: string; model: string; unsaved: boolean }) {
  const [state, setState] = useState<"idle" | "testing" | "done">("idle");
  const [result, setResult] = useState<LlmTestResult | null>(null);

  async function run() {
    setState("testing"); setResult(null);
    try {
      const r = await api<LlmTestResult>("/api/admin/test-llm", { method: "POST" });
      setResult(r);
    } catch (e: unknown) {
      setResult({ ok: false, provider, model, error: e instanceof Error ? e.message : "Test failed" });
    }
    setState("done");
  }

  return (
    <div style={{ marginTop: 4, marginBottom: 14 }}>
      <button type="button" className="btn btn-ghost btn-sm" onClick={run} disabled={state === "testing"}>
        {state === "testing" ? <Loader2 size={14} style={{ animation: "lc-spin .7s linear infinite" }} /> : <PlayCircle size={14} />}
        {state === "testing" ? "Testing connection…" : "Test connection"}
      </button>
      {unsaved && (
        <p style={{ fontSize: 11.5, color: "var(--warning)", marginTop: 6 }}>
          You have unsaved changes — this tests the last <i>saved</i> settings, not what&apos;s in the fields above yet.
        </p>
      )}
      {result && (
        <div style={{
          marginTop: 10, padding: "10px 12px", borderRadius: "var(--r-md)", fontSize: 12.5, lineHeight: 1.55,
          background: result.ok ? "var(--success-soft)" : "var(--danger-soft)",
          color: result.ok ? "var(--success)" : "var(--danger)",
          display: "flex", gap: 8, alignItems: "flex-start",
        }}>
          {result.ok ? <CheckCircle2 size={15} style={{ flex: "none", marginTop: 1 }} /> : <XCircle size={15} style={{ flex: "none", marginTop: 1 }} />}
          <div>
            {result.ok
              ? <>Connected via <b>{result.provider}</b> ({result.model}) in {result.latency_ms}ms.</>
              : <>Failed via <b>{result.provider}</b> ({result.model}): {result.error}</>}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Providers Tab ─────────────────────────────────────────────────────────────
function ProvidersTab({ config, status, onSave }: {
  config: AdminConfig; status: SystemStatus | null; onSave: (updates: Partial<AdminConfig>) => Promise<void>;
}) {
  const [local, setLocal] = useState<AdminConfig>(config);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");
  const [advancedOpen, setAdvancedOpen] = useState(false);

  useEffect(() => { setLocal(config); }, [config]);
  useEffect(() => {
    // Auto-expand the per-component overrides when the current mix doesn't match a preset,
    // so the admin can immediately see what's actually set instead of hunting for it.
    const matchesPreset = PIPELINE_PRESETS.some(p => p.stt === config.stt_provider && p.tts === config.tts_provider);
    setAdvancedOpen(!matchesPreset);
  }, [config.stt_provider, config.tts_provider]);

  const set = (k: keyof AdminConfig) => (v: string) => setLocal(p => ({ ...p, [k]: v }));

  async function handleSave() {
    setSaving(true); setErr(""); setSaved(false);
    try { await onSave(local); setSaved(true); setTimeout(() => setSaved(false), 2000); }
    catch (e: unknown) { setErr(e instanceof Error ? e.message : "Save failed"); }
    finally { setSaving(false); }
  }

  const azure = status?.providers.azure_openai;
  const azureWillRunInstead = local.llm_provider === "openai" && azure?.configured;

  return (
    <div style={{ maxWidth: 980 }}>
      <Section title="Voice pipeline">
        <RadioGroup label="Pipeline" value={local.voice_pipeline} onChange={set("voice_pipeline")}
          options={[
            { value: "cascade",        label: "Cascade (STT → LLM → TTS)" },
            { value: "openai_classic", label: "OpenAI Classic (Whisper + LLM + OpenAI TTS)" },
            { value: "realtime",       label: "OpenAI Realtime (speech-to-speech)" },
          ]} />

        {local.voice_pipeline === "realtime" ? (
          <div style={{ marginTop: 14 }}>
            <Notice>
              Speech-to-speech via OpenAI&apos;s Realtime API - always uses the plain OPENAI_API_KEY (never Azure).
              Calls bypass Deepgram, the LLM provider below, and ElevenLabs/OpenAI TTS entirely; everything below
              this card (STT/TTS/LLM provider) is ignored while this is active.
            </Notice>
            <div style={{ marginTop: 14 }}>
              <RadioGroup label="Voice" value={local.realtime_voice} onChange={set("realtime_voice")}
                options={["marin","cedar","alloy","ash","ballad","coral","echo","sage","shimmer","verse"].map(v => ({ value: v, label: v }))} />
            </div>
          </div>
        ) : local.voice_pipeline === "openai_classic" ? (
          <div style={{ marginTop: 14 }}>
            <Notice>
              Single-vendor cascade: OpenAI handles both transcription (gpt-4o-transcribe/Whisper - Azure OpenAI
              first if configured, else your OPENAI_API_KEY) and speech. Bypasses Deepgram and ElevenLabs entirely;
              the STT/TTS provider settings below are ignored while this is active. The LLM provider below still applies.
            </Notice>
            <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 4 }}>
              <RadioGroup label="Voice" value={local.tts_openai_voice} onChange={set("tts_openai_voice")}
                options={["alloy","echo","fable","onyx","nova","shimmer"].map(v => ({ value: v, label: v }))} />
              <RadioGroup label="Model" value={local.tts_openai_model} onChange={set("tts_openai_model")}
                options={[{ value: "tts-1", label: "tts-1 (fast)" }, { value: "tts-1-hd", label: "tts-1-hd (quality)" }]} />
            </div>
          </div>
        ) : (
          <>
            <div style={{ marginTop: 14 }}>
              <PipelinePresetPicker stt={local.stt_provider} tts={local.tts_provider}
                onPick={(stt, tts) => setLocal(p => ({ ...p, stt_provider: stt, tts_provider: tts }))} />
            </div>

            <button type="button" className={`collapse-toggle${advancedOpen ? " open" : ""}`} style={{ marginTop: 14 }}
              onClick={() => setAdvancedOpen(v => !v)}>
              <ChevronRight size={14} className="chev" /> Override STT / TTS individually
            </button>

            {advancedOpen && (
              <div style={{ marginTop: 10, paddingTop: 14, borderTop: "1px solid var(--border)", display: "flex", flexDirection: "column", gap: 16 }}>
                <div>
                  <RadioGroup label="STT provider" value={local.stt_provider} onChange={set("stt_provider")}
                    options={[
                      { value: "deepgram",       label: "Deepgram" },
                      { value: "openai_whisper", label: "OpenAI Whisper" },
                    ]} />
                  {local.stt_provider === "openai_whisper" && (
                    <Notice>
                      Whisper is batch-only (no streaming). Each utterance is sent to OpenAI after silence is detected.
                      Uses the OpenAI API key configured below under LLM.
                    </Notice>
                  )}
                </div>
                <div>
                  <RadioGroup label="TTS provider" value={local.tts_provider} onChange={set("tts_provider")}
                    options={[
                      { value: "elevenlabs", label: "ElevenLabs" },
                      { value: "openai_tts", label: "OpenAI TTS" },
                    ]} />
                  {local.tts_provider === "elevenlabs" && (
                    <div style={{ marginTop: 14 }}>
                      <ElevenLabsVoicePicker value={local.tts_elevenlabs_voice_id} onChange={set("tts_elevenlabs_voice_id")} />
                    </div>
                  )}
                  {local.tts_provider === "openai_tts" && (
                    <div style={{ marginTop: 14, display: "flex", flexDirection: "column", gap: 4 }}>
                      <RadioGroup label="Voice" value={local.tts_openai_voice} onChange={set("tts_openai_voice")}
                        options={["alloy","echo","fable","onyx","nova","shimmer"].map(v => ({ value: v, label: v }))} />
                      <RadioGroup label="Model" value={local.tts_openai_model} onChange={set("tts_openai_model")}
                        options={[{ value: "tts-1", label: "tts-1 (fast)" }, { value: "tts-1-hd", label: "tts-1-hd (quality)" }]} />
                      <Notice>OpenAI TTS outputs 24 kHz PCM; it is automatically resampled to 16 kHz.</Notice>
                    </div>
                  )}
                </div>
              </div>
            )}
          </>
        )}
      </Section>

      <Section title="Language model (LLM)">
        <RadioGroup label="LLM provider" value={local.llm_provider} onChange={set("llm_provider")}
          options={[
            { value: "openai",    label: "OpenAI (default)" },
            { value: "groq",      label: "Groq" },
            { value: "anthropic", label: "Anthropic" },
            { value: "ollama",    label: "Ollama (local)" },
          ]} />

        {azureWillRunInstead && (
          <div style={{
            margin: "14px 0", padding: "10px 12px", borderRadius: "var(--r-md)",
            background: "var(--accent-soft)", color: "var(--accent-strong)",
            fontSize: 12.5, lineHeight: 1.55, display: "flex", gap: 8, alignItems: "flex-start",
          }}>
            <Zap size={15} style={{ flex: "none", marginTop: 1 }} />
            <div>
              <b>Azure OpenAI is configured and runs first.</b> Every call actually goes to your Azure
              deployment (<code style={{ fontFamily: "var(--font-mono)" }}>{azure?.deployment}</code>) — the model/key below
              are only used as a fallback if Azure errors. This happens automatically whenever the provider
              is &quot;OpenAI&quot; and Azure env vars are set; there&apos;s nothing to configure here for it.
            </div>
          </div>
        )}

        {local.llm_provider === "ollama" ? (
          <Field label="Model name" value={local.llm_model} onChange={set("llm_model")}
            placeholder="llama3.2" hint="Any model pulled in Ollama (ollama list)." />
        ) : (
          <ModelSelect label="Model" value={local.llm_model} onChange={set("llm_model")}
            options={MODEL_OPTIONS[local.llm_provider] ?? []}
            customPlaceholder="Model name"
            hint={azureWillRunInstead ? "Used only if the Azure OpenAI call above fails." : undefined} />
        )}

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

        <LlmTestButton provider={azureWillRunInstead ? "azure_openai" : local.llm_provider} model={local.llm_model}
          unsaved={local.llm_provider !== config.llm_provider || local.llm_model !== config.llm_model ||
                   local.llm_api_key !== config.llm_api_key || local.llm_base_url !== config.llm_base_url} />
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
    <div style={{ maxWidth: 980 }}>
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
    <div style={{ maxWidth: 1100 }}>
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

// ── System Status Tab ─────────────────────────────────────────────────────────
function ProviderStatusRow({ label, sub, configured }: { label: string; sub: string; configured: boolean }) {
  return (
    <div className="status-row">
      <span className={`conn-dot${configured ? " on" : ""}`} style={!configured ? { background: "var(--border-strong)" } : undefined} />
      <div className="meta">
        <div className="name">{label}</div>
        <div className="sub">{sub}</div>
      </div>
      <span className={`pill ${configured ? "pill-success" : "pill-neutral"}`}>
        {configured ? "Configured" : "Not set"}
      </span>
    </div>
  );
}

function StatusTab() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try { setStatus(await api<SystemStatus>("/api/admin/status")); } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !status) {
    return <p style={{ fontSize: 13.5, textAlign: "center", padding: "32px 0", color: "var(--ink-muted)" }}>Loading…</p>;
  }
  if (!status) return <div className="empty"><p>Could not load system status.</p></div>;

  const p = status.providers;
  const ap = status.active_pipeline;

  return (
    <div style={{ maxWidth: 1100 }}>
      <Section title="Right now, a new call would use">
        {ap.voice_pipeline === "realtime" ? (
          <div className="status-grid">
            <div className="status-row">
              <span className="conn-dot on" />
              <div className="meta">
                <div className="name">Voice pipeline</div>
                <div className="sub">OpenAI Realtime (speech-to-speech) · voice: {ap.realtime_voice}</div>
              </div>
            </div>
          </div>
        ) : (
          <div className="status-grid">
            <div className="status-row">
              <span className="conn-dot on" />
              <div className="meta">
                <div className="name">Speech-to-text</div>
                <div className="sub">{ap.stt === "deepgram" ? "Deepgram (streaming)" : "OpenAI Whisper (batch)"}</div>
              </div>
            </div>
            <div className="status-row">
              <span className="conn-dot on" />
              <div className="meta">
                <div className="name">Text-to-speech</div>
                <div className="sub">{ap.tts === "elevenlabs" ? "ElevenLabs" : "OpenAI TTS"}</div>
              </div>
            </div>
            <div className="status-row">
              <span className="conn-dot on" />
              <div className="meta">
                <div className="name">Language model</div>
                <div className="sub">{ap.llm.provider} · {ap.llm.model}</div>
              </div>
            </div>
          </div>
        )}
        {ap.voice_pipeline !== "realtime" && ap.llm.note && (
          <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 12, lineHeight: 1.55 }}>{ap.llm.note}</p>
        )}
        <button type="button" className="btn btn-ghost btn-sm" style={{ marginTop: 14 }} onClick={load}>
          <RefreshCw size={13} /> Refresh
        </button>
      </Section>

      <Section title="Configured credentials">
        <div className="status-grid">
          <ProviderStatusRow label="Azure OpenAI" configured={p.azure_openai.configured}
            sub={p.azure_openai.configured ? (p.azure_openai.deployment ?? "") : "AZURE_OPENAI_* env vars"} />
          <ProviderStatusRow label="OpenAI" configured={p.openai.configured} sub="OPENAI_API_KEY env var" />
          <ProviderStatusRow label="Groq" configured={p.groq.configured} sub="GROQ_API_KEY env var" />
          <ProviderStatusRow label="Anthropic" configured={p.anthropic.configured} sub="ANTHROPIC_API_KEY env var or Providers tab" />
          <ProviderStatusRow label="Deepgram" configured={p.deepgram.configured} sub="DEEPGRAM_API_KEY env var" />
          <ProviderStatusRow label="ElevenLabs" configured={p.elevenlabs.configured} sub="ELEVENLABS_API_KEY env var" />
        </div>
      </Section>
    </div>
  );
}

// ── Call Analytics Tab ───────────────────────────────────────────────────────
const FAILED_CALL_STATUSES = new Set(["failed", "busy", "no_answer"]);

function sentimentPill(s: string | null | undefined): { cls: string; label: string } {
  if (s === "POSITIVE") return { cls: "pill-success", label: "Positive" };
  if (s === "NEGATIVE") return { cls: "pill-danger", label: "Negative" };
  return { cls: "pill-neutral", label: "Neutral" };
}

function callStatusPill(s: string | null | undefined): { cls: string; label: string } {
  if (s === "failed") return { cls: "pill-danger", label: "Failed" };
  if (s === "busy") return { cls: "pill-warning", label: "Busy" };
  if (s === "no_answer") return { cls: "pill-warning", label: "No answer" };
  if (s === "no_response") return { cls: "pill-warning", label: "No conversation" };
  if (s === "in_progress") return { cls: "pill-accent", label: "In progress" };
  if (s === "ringing") return { cls: "pill-accent", label: "Ringing" };
  return { cls: "pill-success", label: "Completed" };
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return "Unknown";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60), s = seconds % 60;
  return s > 0 ? `${m}m ${s}s` : `${m} min`;
}

function CallDetailView({ account, conv, onBack }: {
  account: CallAccount; conv: AdminConversation; onBack: () => void;
}) {
  const [msgs, setMsgs] = useState<AdminMessage[]>([]);
  const [qa, setQa] = useState<AdminConversationQA | null>(null);
  const [loading, setLoading] = useState(true);
  const cPill = callStatusPill(conv.call_status);
  const sPill = sentimentPill(conv.sentiment);
  const failed = FAILED_CALL_STATUSES.has(conv.call_status ?? "");

  useEffect(() => {
    api<AdminMessage[]>(`/api/admin/call-accounts/${account.user_id}/conversations/${conv.id}/messages`)
      .then(m => { setMsgs(m); setLoading(false); }).catch(() => setLoading(false));
    api<AdminConversationQA>(`/api/admin/call-accounts/${account.user_id}/conversations/${conv.id}/qa`)
      .then(setQa).catch(() => setQa(null));
  }, [account.user_id, conv.id]);

  return (
    <div style={{ maxWidth: 1100 }}>
      <button className="back-link" onClick={onBack}><ArrowLeft size={14} />{account.email}</button>
      <div className="view-head">
        <div><h1 className="view-title" style={{ fontSize: 20 }}>{conv.title}</h1></div>
      </div>

      {failed && (
        <div className="fail-banner">
          <X size={16} />
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
              <div className="conv-card-body"><p style={{ fontSize: 14, lineHeight: 1.5 }}>{conv.summary}</p></div>
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
                      {q.answered ? <CheckCircle2 size={15} /> : <X size={15} />}
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
                <p style={{ fontSize: 13.5, color: "var(--ink-faint)" }}>Loading transcript...</p>
              ) : msgs.length === 0 ? (
                <div className="empty"><p>No messages in this session.</p></div>
              ) : (
                <div className="transcript">
                  {msgs.map((m, i) => (
                    <div key={i} className={`bubble-row ${m.role === "user" ? "user" : "agent"}`}>
                      <div className="bubble-col">
                        <div className="msg-meta">
                          <span className="msg-name">{m.role === "user" ? "Caller" : "Agent"}</span>
                          <span>{new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
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
                <Phone size={15} />
                <div><div className="detail-label">Dialed number</div><div className="detail-value">{conv.to_number || "Unknown"}</div></div>
              </div>
              <div className="detail-row">
                <Phone size={15} />
                <div><div className="detail-label">From</div><div className="detail-value">{conv.from_number || "Unknown"}</div></div>
              </div>
              <div className="detail-row">
                <Calendar size={15} />
                <div><div className="detail-label">Time</div><div className="detail-value">{new Date(conv.created_at).toLocaleString()}</div></div>
              </div>
              <div className="detail-row">
                <Clock size={15} />
                <div><div className="detail-label">Duration</div><div className="detail-value">{formatDuration(conv.duration_seconds)}</div></div>
              </div>
              {conv.cost_usd != null && (
                <div className="detail-row">
                  <DollarSign size={15} />
                  <div><div className="detail-label">Estimated cost</div><div className="detail-value">${conv.cost_usd.toFixed(4)}</div></div>
                </div>
              )}
              <div className="detail-row">
                <CheckCircle2 size={15} />
                <div><div className="detail-label">Status</div><div><span className={`pill ${cPill.cls}`}>{cPill.label}</span></div></div>
              </div>
              {conv.sentiment && (
                <div className="detail-row">
                  <Activity size={15} />
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

type CallFilter = "all" | "completed" | "no_response" | "failed";

function AccountCallsView({ account, onBack }: { account: CallAccount; onBack: () => void }) {
  const [convs, setConvs] = useState<AdminConversation[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<AdminConversation | null>(null);
  const [filter, setFilter] = useState<CallFilter>("all");

  const load = useCallback(async () => {
    setLoading(true);
    try { setConvs(await api<AdminConversation[]>(`/api/admin/call-accounts/${account.user_id}/conversations`)); }
    catch { /* ignore */ }
    setLoading(false);
  }, [account.user_id]);

  useEffect(() => { load(); }, [load]);

  if (selected) {
    return <CallDetailView account={account} conv={selected} onBack={() => setSelected(null)} />;
  }

  const total = convs.length;
  const failedCount = convs.filter(c => FAILED_CALL_STATUSES.has(c.call_status ?? "")).length;
  const completedCount = convs.filter(c => (c.call_status ?? "completed") === "completed").length;
  const noResponseCount = convs.filter(c => c.call_status === "no_response").length;
  const totalCost = convs.reduce((s, c) => s + (c.cost_usd ?? 0), 0);

  const filtered = convs.filter(c => {
    if (filter === "failed") return FAILED_CALL_STATUSES.has(c.call_status ?? "");
    if (filter === "completed") return (c.call_status ?? "completed") === "completed";
    if (filter === "no_response") return c.call_status === "no_response";
    return true;
  });

  return (
    <div style={{ maxWidth: 1100 }}>
      <button className="back-link" onClick={onBack}><ArrowLeft size={14} />All accounts</button>
      <div className="view-head">
        <div>
          <h1 className="view-title" style={{ fontSize: 20 }}>{account.email}</h1>
          <p className="view-sub">Every call this account has placed or received.</p>
        </div>
      </div>

      {!loading && total > 0 && (
        <div className="stat-grid">
          <div className="stat-card"><div className="stat-top"><span className="stat-label">Total calls</span></div><div className="stat-value">{total}</div></div>
          <div className="stat-card"><div className="stat-top"><span className="stat-label">Completed</span></div><div className="stat-value">{completedCount}</div></div>
          <div className="stat-card"><div className="stat-top"><span className="stat-label">No conversation</span></div><div className="stat-value">{noResponseCount}</div></div>
          <div className="stat-card"><div className="stat-top"><span className="stat-label">Failed / busy / no answer</span></div><div className="stat-value">{failedCount}</div></div>
          <div className="stat-card"><div className="stat-top"><span className="stat-label">Estimated cost</span></div><div className="stat-value">${totalCost.toFixed(2)}</div></div>
        </div>
      )}

      {!loading && total > 0 && (
        <div className="filter-row">
          <div className="segmented">
            {([["all", "All"], ["completed", "Completed"], ["no_response", "No conversation"], ["failed", "Failed"]] as [CallFilter, string][]).map(([key, label]) => (
              <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>
            ))}
          </div>
        </div>
      )}

      {loading ? (
        <p style={{ fontSize: 13.5, color: "var(--ink-faint)" }}>Loading calls...</p>
      ) : convs.length === 0 ? (
        <div className="empty"><div className="ic"><Phone size={20} /></div><p>No calls recorded for this account yet.</p></div>
      ) : filtered.length === 0 ? (
        <div className="empty"><div className="ic"><Phone size={20} /></div><p>No calls match this filter.</p></div>
      ) : (
        <div className="call-grid">
          {filtered.map(conv => {
            const cPill = callStatusPill(conv.call_status);
            const isFailed = FAILED_CALL_STATUSES.has(conv.call_status ?? "");
            return (
              <button key={conv.id} className={`call-card ${isFailed ? "bad" : "ok"}`}
                style={{ textAlign: "left", width: "100%" }} onClick={() => setSelected(conv)}>
                <div className="call-card-top">
                  <span className={`pill ${cPill.cls}`}>{cPill.label}</span>
                  <span style={{ fontSize: 11.5, color: "var(--ink-faint)" }}>{new Date(conv.created_at).toLocaleString()}</span>
                </div>
                <p style={{ fontSize: 13.5, fontWeight: 600, marginTop: 8, color: "var(--ink)" }}>
                  {conv.to_number || conv.title}
                </p>
                {conv.from_number && (
                  <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>from {conv.from_number}</p>
                )}
                <p style={{ fontSize: 11.5, color: "var(--ink-faint)", marginTop: 8 }}>
                  {formatDuration(conv.duration_seconds)}
                  {conv.cost_usd != null && ` · $${conv.cost_usd.toFixed(4)}`}
                </p>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function CallAnalyticsTab() {
  const [accounts, setAccounts] = useState<CallAccount[]>([]);
  const [loading, setLoading] = useState(true);
  const [account, setAccount] = useState<CallAccount | null>(null);
  const [search, setSearch] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try { setAccounts(await api<CallAccount[]>("/api/admin/call-accounts")); }
    catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  if (account) {
    return <AccountCallsView account={account} onBack={() => setAccount(null)} />;
  }

  const filtered = accounts.filter(a => a.email.toLowerCase().includes(search.toLowerCase()));

  return (
    <div style={{ maxWidth: 1100 }}>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, alignItems: "center" }}>
        <div style={{ position: "relative", flex: 1, maxWidth: 360 }}>
          <Search size={14} style={{ position: "absolute", left: 10, top: "50%", transform: "translateY(-50%)", color: "var(--ink-faint)" }} />
          <input className="input" style={{ paddingLeft: 32 }} placeholder="Search by email..."
            value={search} onChange={e => setSearch(e.target.value)} />
        </div>
        <button className="icon-btn" aria-label="Refresh" onClick={load}><RefreshCw size={15} /></button>
      </div>

      {loading ? (
        <p style={{ fontSize: 13.5, textAlign: "center", padding: "32px 0", color: "var(--ink-muted)" }}>Loading accounts...</p>
      ) : accounts.length === 0 ? (
        <div className="empty"><div className="ic"><Phone size={20} /></div><p>No accounts have made any calls yet.</p></div>
      ) : filtered.length === 0 ? (
        <div className="empty"><p>No accounts match &quot;{search}&quot;.</p></div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {filtered.map(a => (
            <button key={a.user_id} className="card" onClick={() => setAccount(a)}
              style={{ padding: "14px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, textAlign: "left", width: "100%", cursor: "pointer" }}>
              <div>
                <p style={{ fontSize: 13.5, fontWeight: 600, color: "var(--ink)" }}>{a.email}</p>
                <p style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>Last call {new Date(a.last_call_at).toLocaleString()}</p>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="pill pill-accent">{a.call_count} call{a.call_count !== 1 ? "s" : ""}</span>
                <ChevronRight size={16} style={{ color: "var(--ink-faint)" }} />
              </div>
            </button>
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
  const [status,    setStatus]    = useState<SystemStatus | null>(null);
  const [tab, setTab] = useState<Tab>("providers");

  // Check existing token on mount
  useEffect(() => {
    const token = getToken();
    if (!token) { setCheckDone(true); return; }
    api<AdminConfig>("/api/admin/config")
      .then(cfg => { setConfig(cfg); setLoggedIn(true); api<SystemStatus>("/api/admin/status").then(setStatus).catch(() => {}); })
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
      api<SystemStatus>("/api/admin/status").then(setStatus).catch(() => {});
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
    setStatus(null);
  }

  async function saveConfig(updates: Partial<AdminConfig>) {
    const updated = await api<AdminConfig>("/api/admin/config", {
      method: "PUT",
      body: JSON.stringify(updates),
    });
    setConfig(updated);
    api<SystemStatus>("/api/admin/status").then(setStatus).catch(() => {});
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
    calls:     { title: "Call analytics",    sub: "Pick an account to see every call it has placed or received, who it dialed, and the conversation." },
    status:    { title: "System status",      sub: "What's actually configured and what the next call will use." },
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

            {config && tab === "providers" && <ProvidersTab config={config} status={status} onSave={saveConfig} />}
            {config && tab === "storage"   && <StorageTab   config={config} onSave={saveConfig} />}
            {tab === "calls" && <CallAnalyticsTab />}
            {tab === "status" && <StatusTab />}
            {tab === "kb" && <KbAccessTab />}
          </div>
        </div>
      </div>
    </div>
  );
}
