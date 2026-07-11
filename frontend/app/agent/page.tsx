"use client";
import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { isLoggedIn, getUser, clearTokens } from "@/lib/auth";
import { API, Conversation, Message } from "@/lib/api";
import { useVoiceAgent, AgentState, ChatEntry } from "@/hooks/useVoiceAgent";
import {
  Mic, MicOff, LogOut, MessageSquare, ChevronLeft, ChevronRight,
  Loader2, Volume2, BrainCircuit, Radio, X, Clock, Download, Send
} from "lucide-react";

// Light theme palette
const C = {
  bg:          "#f8f9fc",
  sidebar:     "#f0eeff",
  sidebarBdr:  "#e5e0ff",
  header:      "rgba(255,255,255,0.92)",
  headerBdr:   "#e9eaf0",
  surface:     "#ffffff",
  surfaceBdr:  "#e5e7eb",
  text:        "#111827",
  muted:       "#6b7280",
  accent:      "#7c6cfc",
  accent2:     "#9d8dfd",
  bottomBar:   "rgba(255,255,255,0.92)",
  gridDot:     "rgba(124,108,252,0.12)",
  glowBg:      "radial-gradient(ellipse 60% 40% at 50% 55%, rgba(124,108,252,0.08) 0%, transparent 70%)",
  errBg:       "#fef2f2",
  errBdr:      "#fecaca",
  errText:     "#dc2626",
  aiBubbleBg:  "#f0eeff",
  aiBubbleBdr: "#c4b5fd",
};

// ── Download helpers ─────────────────────────────────────────────────────────
function formatTranscript(lines: { role: string; text: string; created_at?: string }[], title = "Lumen Transcript") {
  const date = new Date().toLocaleString(undefined, { dateStyle: "long", timeStyle: "short" });
  const divider = "─".repeat(48);
  const body = lines.map(l => {
    const who = l.role === "user" ? "You" : "Lumen";
    const time = l.created_at
      ? new Date(l.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
      : "";
    return `${who}${time ? ` [${time}]` : ""}:\n${l.text}\n`;
  }).join("\n");
  return `${title}\nExported: ${date}\n${divider}\n\n${body}`;
}

function triggerDownload(content: string, filename: string) {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ── State badge ──────────────────────────────────────────────────────────────
function StateBadge({ state }: { state: AgentState }) {
  const cfg: Record<AgentState, { label: string; color: string; icon: React.ReactNode }> = {
    IDLE:       { label: "Disconnected", color: "#9ca3af", icon: <Radio className="w-3.5 h-3.5" /> },
    LISTENING:  { label: "Listening",    color: "#16a34a", icon: <Mic className="w-3.5 h-3.5" /> },
    PROCESSING: { label: "Thinking",     color: "#d97706", icon: <BrainCircuit className="w-3.5 h-3.5" /> },
    SPEAKING:   { label: "Speaking",     color: "#7c6cfc", icon: <Volume2 className="w-3.5 h-3.5" /> },
  };
  const { label, color, icon } = cfg[state];
  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium"
      style={{ background: `${color}14`, border: `1px solid ${color}30`, color }}>
      {icon} {label}
    </div>
  );
}

// ── Sound wave visualizer ────────────────────────────────────────────────────
function SoundWave({ active }: { active: boolean }) {
  return (
    <div className="flex items-center gap-1 h-6">
      {[...Array(5)].map((_, i) => (
        <div key={i} className={`w-1 rounded-full transition-all ${active ? "wave-bar" : ""}`}
          style={{
            height: active ? undefined : "6px",
            background: active ? C.accent : "rgba(124,108,252,0.2)",
            animationDelay: `${i * 0.1}s`,
          }} />
      ))}
    </div>
  );
}

// ── Big mic orb ──────────────────────────────────────────────────────────────
function MicOrb({ state, isMicActive, onClick }: { state: AgentState; isMicActive: boolean; onClick: () => void }) {
  const isListening  = state === "LISTENING" && isMicActive;
  const isProcessing = state === "PROCESSING";
  const isSpeaking   = state === "SPEAKING";

  return (
    <button onClick={onClick}
      className="relative w-24 h-24 rounded-full flex items-center justify-center transition-all duration-300 focus:outline-none"
      style={{
        background: isListening
          ? "linear-gradient(135deg, #22c55e, #16a34a)"
          : isSpeaking
          ? "linear-gradient(135deg, #7c6cfc, #a78bfa)"
          : isProcessing
          ? "linear-gradient(135deg, #f59e0b, #d97706)"
          : "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
        boxShadow: isListening
          ? "0 0 0 0 rgba(34,197,94,0.4), 0 4px 40px rgba(34,197,94,0.3)"
          : isSpeaking
          ? "0 4px 40px rgba(124,108,252,0.4)"
          : "0 4px 28px rgba(124,108,252,0.3)",
        animation: isListening ? "pulse-ring 1.5s ease-out infinite" : undefined,
      }}>
      {isProcessing
        ? <Loader2 className="w-9 h-9 text-white animate-spin" />
        : isMicActive
        ? <MicOff className="w-9 h-9 text-white" />
        : <Mic className="w-9 h-9 text-white" />}
    </button>
  );
}

// ── Chat bubble ──────────────────────────────────────────────────────────────
function ChatBubble({ entry }: { entry: ChatEntry }) {
  const isUser = entry.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} animate-fade-in`}>
      <div className="max-w-xs lg:max-w-sm xl:max-w-md px-4 py-3 rounded-2xl text-sm leading-relaxed"
        style={{
          background: isUser ? "linear-gradient(135deg, #7c6cfc, #9d8dfd)" : C.aiBubbleBg,
          border: isUser ? "none" : `1px solid ${C.aiBubbleBdr}`,
          color: isUser ? "white" : C.text,
          borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
          boxShadow: isUser ? "0 2px 12px rgba(124,108,252,0.25)" : "0 1px 4px rgba(0,0,0,0.06)",
        }}>
        {entry.text}
      </div>
    </div>
  );
}

// ── History panel ────────────────────────────────────────────────────────────
function HistoryPanel({ conversations, onSelect, selectedId, onClose }: {
  conversations: Conversation[];
  onSelect: (id: string) => void;
  selectedId: string | null;
  onClose: () => void;
}) {
  return (
    <div className="flex flex-col h-full" style={{ background: C.sidebar }}>
      <div className="flex items-center justify-between px-5 py-4 border-b"
        style={{ borderColor: C.sidebarBdr }}>
        <div className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4" style={{ color: C.accent }} />
          <span className="font-semibold text-sm" style={{ color: C.text }}>History</span>
        </div>
        <button onClick={onClose} className="p-1 rounded-lg" style={{ color: C.muted }}>
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        {conversations.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm" style={{ color: C.muted }}>
            No conversations yet.<br />Start talking to create one.
          </div>
        ) : (
          conversations.map(c => {
            const s = c.sentiment;
            const badge = s === "POSITIVE"
              ? { emoji: "🟢", label: "Positive", color: "#16a34a" }
              : s === "NEGATIVE"
              ? { emoji: "🔴", label: "Negative", color: "#dc2626" }
              : s === "NEUTRAL"
              ? { emoji: "🟡", label: "Neutral",  color: "#d97706" }
              : null;
            return (
              <button key={c.id} onClick={() => onSelect(c.id)}
                className="w-full text-left px-5 py-3 transition-colors"
                style={{
                  background: selectedId === c.id ? "rgba(124,108,252,0.08)" : "transparent",
                  borderLeft: selectedId === c.id ? `2px solid ${C.accent}` : "2px solid transparent",
                }}
                onMouseEnter={e => { if (selectedId !== c.id) (e.currentTarget as HTMLButtonElement).style.background = "rgba(124,108,252,0.04)"; }}
                onMouseLeave={e => { if (selectedId !== c.id) (e.currentTarget as HTMLButtonElement).style.background = "transparent"; }}>
                <div className="text-sm font-medium truncate" style={{ color: C.text }}>{c.title}</div>
                <div className="flex items-center justify-between mt-1">
                  <div className="flex items-center gap-1 text-xs" style={{ color: C.muted }}>
                    <Clock className="w-3 h-3" />
                    {new Date(c.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                  </div>
                  {badge && (
                    <span className="text-xs font-medium px-1.5 py-0.5 rounded-full"
                      style={{ background: `${badge.color}18`, color: badge.color }}>
                      {badge.emoji} {badge.label}
                    </span>
                  )}
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}

// ── Messages viewer ──────────────────────────────────────────────────────────
function MessagesPanel({ messages, loading, onClose }: { messages: Message[]; loading: boolean; onClose: () => void }) {
  function handleDownload() {
    if (!messages.length) return;
    const lines = messages.map(m => ({ role: m.role, text: m.content, created_at: m.created_at }));
    triggerDownload(formatTranscript(lines, "Lumen Conversation"), `lumen-conversation-${Date.now()}.txt`);
  }

  return (
    <div className="h-full flex flex-col animate-fade-in" style={{ background: C.bg }}>
      <div className="flex items-center justify-between px-5 py-4 border-b shrink-0"
        style={{ borderColor: C.surfaceBdr, background: C.header, backdropFilter: "blur(12px)" }}>
        <span className="font-semibold" style={{ color: C.text }}>Conversation</span>
        <div className="flex items-center gap-2">
          {messages.length > 0 && (
            <button onClick={handleDownload}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{ background: "#f5f3ff", border: "1px solid #ede9fe", color: C.accent }}
              title="Download transcript">
              <Download className="w-3.5 h-3.5" /> Download
            </button>
          )}
          <button onClick={onClose} className="p-1 rounded-lg" style={{ color: C.muted }} title="Back to live session">
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin" style={{ color: C.accent }} />
          </div>
        ) : messages.length === 0 ? (
          <div className="text-center py-12 text-sm" style={{ color: C.muted }}>No messages in this conversation.</div>
        ) : (
          messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className="max-w-xs lg:max-w-sm px-4 py-3 rounded-2xl text-sm leading-relaxed"
                style={{
                  background: m.role === "user" ? "linear-gradient(135deg, #7c6cfc, #9d8dfd)" : C.aiBubbleBg,
                  border: m.role === "user" ? "none" : `1px solid ${C.aiBubbleBdr}`,
                  color: m.role === "user" ? "white" : C.text,
                  borderRadius: m.role === "user" ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
                  boxShadow: m.role === "user" ? "0 2px 12px rgba(124,108,252,0.25)" : "0 1px 4px rgba(0,0,0,0.06)",
                }}>
                {m.content}
                <div className="text-xs mt-1 opacity-40">
                  {new Date(m.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </div>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function AgentPage() {
  const router = useRouter();
  const [user, setUserState] = useState<ReturnType<typeof getUser>>(null);
  const {
    state, connected, transcript, chat,
    error, isMicActive, connect, disconnect, sendTextMessage,
  } = useVoiceAgent();
  const [textInput, setTextInput] = useState("");

  const [sidebarOpen, setSidebarOpen]     = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [messages, setMessages]           = useState<Message[]>([]);
  const [loadingMsgs, setLoadingMsgs]     = useState(false);
  const [showMsgs, setShowMsgs]           = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isLoggedIn()) router.replace("/signin");
    else setUserState(getUser());
  }, [router]);

  useEffect(() => { API.conversations().then(setConversations).catch(() => {}); }, []);

  useEffect(() => {
    if (state === "LISTENING") API.conversations().then(setConversations).catch(() => {});
  }, [state]);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [chat]);

  async function loadMessages(id: string) {
    setSelectedConvId(id);
    setShowMsgs(true);
    setLoadingMsgs(true);
    try { setMessages(await API.messages(id)); }
    catch { setMessages([]); }
    finally { setLoadingMsgs(false); }
  }

  async function handleSignOut() {
    try { await API.signOut(); } catch { /* ignore */ }
    clearTokens();
    disconnect();
    router.replace("/signin");
  }

  function toggleConnection() {
    if (connected) disconnect(); else connect();
  }

  const initials = user?.full_name
    ? user.full_name.split(" ").map(w => w[0]).join("").toUpperCase().slice(0, 2)
    : user?.email?.[0]?.toUpperCase() ?? "?";

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: C.bg }}>

      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <div className="w-64 shrink-0 flex flex-col animate-slide-in"
          style={{ borderRight: `1px solid ${C.sidebarBdr}` }}>
          <HistoryPanel conversations={conversations} onSelect={loadMessages}
            selectedId={selectedConvId} onClose={() => setSidebarOpen(false)} />
        </div>
      )}

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col min-w-0 relative overflow-hidden">

        {/* Ambient background — empty state only */}
        {chat.length === 0 && !showMsgs && (
          <>
            <div className="absolute inset-0 pointer-events-none" style={{ backgroundImage: C.glowBg }} />
            <div className="absolute inset-0 pointer-events-none" style={{
              backgroundImage: `radial-gradient(circle, ${C.gridDot} 1px, transparent 1px)`,
              backgroundSize: "32px 32px",
            }} />
          </>
        )}

        {/* Top bar */}
        <header className="flex items-center justify-between px-5 py-3 shrink-0 relative z-10"
          style={{
            borderBottom: `1px solid ${C.headerBdr}`,
            background: C.header,
            backdropFilter: "blur(12px)",
          }}>
          <div className="flex items-center gap-3">
            {!sidebarOpen && (
              <button onClick={() => setSidebarOpen(true)}
                className="p-2 rounded-lg transition-colors mr-1"
                style={{ color: C.muted }}
                onMouseEnter={e => (e.currentTarget.style.background = "rgba(124,108,252,0.06)")}
                onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                <ChevronRight className="w-4 h-4" />
              </button>
            )}
            {sidebarOpen && (
              <button onClick={() => setSidebarOpen(false)}
                className="p-2 rounded-lg transition-colors mr-1"
                style={{ color: C.muted }}
                onMouseEnter={e => (e.currentTarget.style.background = "rgba(124,108,252,0.06)")}
                onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                <ChevronLeft className="w-4 h-4" />
              </button>
            )}
            <div className="w-7 h-7 rounded-lg flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #7c6cfc, #9d8dfd)", boxShadow: "0 2px 10px rgba(124,108,252,0.35)" }}>
              <Mic className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="font-bold tracking-tight" style={{ color: C.text }}>Lumen</span>
          </div>

          <div className="flex items-center gap-3">
            <StateBadge state={state} />
            <button
              disabled={chat.length === 0}
              onClick={() => {
                const lines = chat.map(e => ({ role: e.role, text: e.text }));
                triggerDownload(formatTranscript(lines, "Lumen Live Transcript"), `lumen-transcript-${Date.now()}.txt`);
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{
                background: chat.length === 0 ? "#f3f4f6" : "#f5f3ff",
                border: chat.length === 0 ? "1px solid #e5e7eb" : "1px solid #ede9fe",
                color: chat.length === 0 ? "#d1d5db" : C.accent,
                cursor: chat.length === 0 ? "not-allowed" : "pointer",
              }}
              title={chat.length === 0 ? "Start a conversation to download" : "Download transcript"}>
              <Download className="w-3.5 h-3.5" /> Download
            </button>
            <div className="flex items-center gap-2 pl-3" style={{ borderLeft: `1px solid ${C.surfaceBdr}` }}>
              <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white"
                style={{ background: "linear-gradient(135deg, #7c6cfc, #9d8dfd)" }}>
                {initials}
              </div>
              <span className="text-sm hidden sm:block truncate max-w-32" style={{ color: C.muted }}>
                {user?.full_name ?? user?.email ?? ""}
              </span>
              <button onClick={handleSignOut}
                className="p-2 rounded-lg transition-colors"
                style={{ color: C.muted }}
                onMouseEnter={e => (e.currentTarget.style.background = "rgba(124,108,252,0.06)")}
                onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          </div>
        </header>

        {/* Middle area — fixed height, switches between history view and live chat */}
        <div className="flex-1 min-h-0 relative">

          {/* History view */}
          {showMsgs && (
            <div className="absolute inset-0 flex flex-col" style={{ background: C.bg }}>
              <MessagesPanel messages={messages} loading={loadingMsgs} onClose={() => { setShowMsgs(false); setSelectedConvId(null); }} />
            </div>
          )}

          {/* Live chat */}
          <div className={`absolute inset-0 overflow-y-auto px-4 py-4 ${showMsgs ? "invisible pointer-events-none" : ""}`}>
            {chat.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full text-center px-8 gap-5">

                {/* Animated rings */}
                <div className="relative flex items-center justify-center">
                  <div className="absolute w-40 h-40 rounded-full animate-ping"
                    style={{ background: "rgba(124,108,252,0.04)", animationDuration: "3s" }} />
                  <div className="absolute w-28 h-28 rounded-full"
                    style={{ background: "rgba(124,108,252,0.06)", border: "1px solid rgba(124,108,252,0.15)" }} />
                  <div className="w-16 h-16 rounded-2xl flex items-center justify-center relative z-10"
                    style={{
                      background: "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
                      boxShadow: "0 4px 40px rgba(124,108,252,0.35), 0 0 80px rgba(124,108,252,0.12)",
                    }}>
                    <Mic className="w-8 h-8 text-white" />
                  </div>
                </div>

                {!connected ? (
                  <>
                    <div>
                      <p className="font-bold text-lg mb-1" style={{ color: C.text }}>Connect to start</p>
                      <p className="text-sm max-w-xs" style={{ color: C.muted }}>
                        Press <strong style={{ color: C.accent }}>Connect</strong> below to wake up your voice AI agent.
                      </p>
                    </div>
                    <div className="flex gap-6 mt-1">
                      {[
                        { label: "Speak naturally", sub: "No wake word needed" },
                        { label: "Instant reply",   sub: "Voice response in seconds" },
                        { label: "Remembers you",   sub: "Context across sessions" },
                      ].map(({ label, sub }) => (
                        <div key={label} className="text-center">
                          <p className="text-xs font-semibold mb-0.5" style={{ color: C.text }}>{label}</p>
                          <p className="text-xs" style={{ color: C.muted }}>{sub}</p>
                        </div>
                      ))}
                    </div>
                  </>
                ) : (
                  <>
                    <p className="font-bold text-lg mb-1" style={{ color: C.text }}>Ready to listen</p>
                    <p className="text-sm" style={{ color: C.muted }}>Tap the mic and start speaking.</p>
                  </>
                )}
              </div>
            ) : (
              <div className="max-w-2xl mx-auto space-y-4">
                {chat.map(entry => <ChatBubble key={entry.id} entry={entry} />)}
                <div ref={chatEndRef} />
              </div>
            )}
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="mx-4 mb-2 px-4 py-3 rounded-xl text-sm animate-fade-in relative z-10"
            style={{ background: C.errBg, border: `1px solid ${C.errBdr}`, color: C.errText }}>
            {error}
          </div>
        )}

        {/* Bottom controls */}
        <div className="shrink-0 px-4 pt-3 pb-4 flex flex-col items-center gap-3 relative z-10"
          style={{ borderTop: `1px solid ${C.headerBdr}`, background: C.bottomBar, backdropFilter: "blur(12px)" }}>

          {/* Status banner */}
          {connected && state === "LISTENING" && isMicActive && (
            <div className="flex items-center gap-2 px-5 py-2 rounded-full animate-fade-in"
              style={{ background: "rgba(22,163,74,0.10)", border: "1.5px solid rgba(22,163,74,0.3)" }}>
              <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: "#16a34a", boxShadow: "0 0 6px #16a34a" }} />
              <span className="text-sm font-semibold" style={{ color: "#16a34a" }}>Ready — speak now</span>
            </div>
          )}
          {connected && state === "LISTENING" && !isMicActive && (
            <div className="flex items-center gap-2 px-5 py-2 rounded-full">
              <span className="text-sm" style={{ color: C.muted }}>Starting microphone…</span>
            </div>
          )}
          {connected && state === "PROCESSING" && (
            <div className="flex items-center gap-2 px-5 py-2 rounded-full animate-fade-in"
              style={{ background: "rgba(217,119,6,0.10)", border: "1.5px solid rgba(217,119,6,0.25)" }}>
              <Loader2 className="w-3.5 h-3.5 animate-spin" style={{ color: "#d97706" }} />
              <span className="text-sm font-semibold" style={{ color: "#d97706" }}>
                Thinking{transcript ? `… "${transcript.slice(0,40)}${transcript.length>40?"…":""}"` : "…"}
              </span>
            </div>
          )}
          {connected && state === "SPEAKING" && (
            <div className="flex items-center gap-2 px-5 py-2 rounded-full animate-fade-in"
              style={{ background: "rgba(124,108,252,0.10)", border: "1.5px solid rgba(124,108,252,0.25)" }}>
              <Volume2 className="w-3.5 h-3.5" style={{ color: C.accent }} />
              <span className="text-sm font-semibold" style={{ color: C.accent }}>Speaking…</span>
            </div>
          )}
          {!connected && (
            <div className="text-xs" style={{ color: C.muted }}>Press Start Session to begin</div>
          )}

          {/* Text input row */}
          {connected && (
            <form className="w-full max-w-lg flex gap-2" onSubmit={e => {
              e.preventDefault();
              const t = textInput.trim();
              if (!t || state !== "LISTENING") return;
              sendTextMessage(t);
              setTextInput("");
            }}>
              <input
                type="text"
                value={textInput}
                onChange={e => setTextInput(e.target.value)}
                placeholder={state === "LISTENING" ? "Or type a message…" : state === "PROCESSING" ? "Thinking…" : "Speaking…"}
                disabled={state !== "LISTENING"}
                className="flex-1 px-4 py-2.5 rounded-xl text-sm outline-none transition-all"
                style={{
                  background: "#f9fafb",
                  border: `1.5px solid ${state === "LISTENING" ? "#e5e7eb" : "#f3f4f6"}`,
                  color: C.text,
                  opacity: state !== "LISTENING" ? 0.5 : 1,
                }}
              />
              <button type="submit" disabled={state !== "LISTENING" || !textInput.trim()}
                className="px-3 py-2.5 rounded-xl font-semibold transition-all flex items-center gap-1.5 text-sm"
                style={{
                  background: (state === "LISTENING" && textInput.trim()) ? "linear-gradient(135deg, #7c6cfc, #9d8dfd)" : "#f3f4f6",
                  color: (state === "LISTENING" && textInput.trim()) ? "white" : "#d1d5db",
                  border: "none",
                }}>
                <Send className="w-4 h-4" />
              </button>
            </form>
          )}

          {/* Orb + single toggle button */}
          <div className="flex flex-col items-center gap-3">
            <div className="flex items-center gap-4">
              <SoundWave active={state === "LISTENING" && isMicActive} />
              <MicOrb state={state} isMicActive={isMicActive} onClick={() => {}} />
              <SoundWave active={state === "LISTENING" && isMicActive} />
            </div>

            <button onClick={toggleConnection}
              className="px-8 py-2.5 rounded-2xl text-sm font-bold transition-all"
              style={{
                background: connected
                  ? "#fef2f2"
                  : "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
                border: connected ? "1px solid #fecaca" : "none",
                color: connected ? "#dc2626" : "white",
                boxShadow: connected ? "none" : "0 4px 20px rgba(124,108,252,0.4)",
                letterSpacing: "0.02em",
              }}>
              {connected ? "⏹ End Session" : "▶ Start Session"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
