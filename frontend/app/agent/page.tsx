"use client";
import { useEffect, useState, useRef } from "react";
import { useRouter } from "next/navigation";
import { isLoggedIn, getUser, clearTokens } from "@/lib/auth";
import { API, Conversation, Message } from "@/lib/api";
import { useVoiceAgent, AgentState, ChatEntry } from "@/hooks/useVoiceAgent";
import {
  Mic, MicOff, LogOut, MessageSquare, ChevronLeft, ChevronRight,
  Loader2, Volume2, BrainCircuit, Radio, X, Clock
} from "lucide-react";

// ── State badge ─────────────────────────────────────────────────────────────
function StateBadge({ state }: { state: AgentState }) {
  const cfg: Record<AgentState, { label: string; color: string; icon: React.ReactNode }> = {
    IDLE:       { label: "Disconnected", color: "#6b6b8a", icon: <Radio className="w-3.5 h-3.5" /> },
    LISTENING:  { label: "Listening",    color: "#22c55e", icon: <Mic className="w-3.5 h-3.5" /> },
    PROCESSING: { label: "Thinking",     color: "#f59e0b", icon: <BrainCircuit className="w-3.5 h-3.5" /> },
    SPEAKING:   { label: "Speaking",     color: "#7c6cfc", icon: <Volume2 className="w-3.5 h-3.5" /> },
  };
  const { label, color, icon } = cfg[state];
  return (
    <div className="flex items-center gap-2 px-3 py-1.5 rounded-full text-xs font-medium"
      style={{ background: `${color}20`, border: `1px solid ${color}40`, color }}>
      {icon} {label}
    </div>
  );
}

// ── Sound wave visualizer ───────────────────────────────────────────────────
function SoundWave({ active }: { active: boolean }) {
  return (
    <div className="flex items-center gap-1 h-8">
      {[...Array(5)].map((_, i) => (
        <div key={i} className={`w-1.5 rounded-full transition-all ${active ? "wave-bar" : ""}`}
          style={{
            height: active ? undefined : "8px",
            background: active ? "var(--accent)" : "var(--border)",
            animationDelay: `${i * 0.1}s`,
          }} />
      ))}
    </div>
  );
}

// ── Big mic orb ─────────────────────────────────────────────────────────────
function MicOrb({ state, isMicActive, onClick }: { state: AgentState; isMicActive: boolean; onClick: () => void }) {
  const isListening = state === "LISTENING" && isMicActive;
  const isProcessing = state === "PROCESSING";
  const isSpeaking = state === "SPEAKING";

  return (
    <button onClick={onClick}
      className="relative w-28 h-28 rounded-full flex items-center justify-center transition-all duration-300 focus:outline-none"
      style={{
        background: isListening
          ? "linear-gradient(135deg, #22c55e, #16a34a)"
          : isSpeaking
          ? "linear-gradient(135deg, #7c6cfc, #a78bfa)"
          : isProcessing
          ? "linear-gradient(135deg, #f59e0b, #d97706)"
          : "linear-gradient(135deg, #7c6cfc, #a78bfa)",
        boxShadow: isListening
          ? "0 0 0 0 rgba(34,197,94,0.5), 0 0 40px rgba(34,197,94,0.3)"
          : isSpeaking
          ? "0 0 40px rgba(124,108,252,0.4)"
          : "0 0 24px rgba(124,108,252,0.25)",
        animation: isListening ? "pulse-ring 1.5s ease-out infinite" : undefined,
      }}>
      {isProcessing
        ? <Loader2 className="w-10 h-10 text-white animate-spin" />
        : isMicActive
        ? <MicOff className="w-10 h-10 text-white" />
        : <Mic className="w-10 h-10 text-white" />}
    </button>
  );
}

// ── Chat bubble ─────────────────────────────────────────────────────────────
function ChatBubble({ entry }: { entry: ChatEntry }) {
  const isUser = entry.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} animate-fade-in`}>
      <div className="max-w-xs lg:max-w-sm xl:max-w-md px-4 py-3 rounded-2xl text-sm leading-relaxed"
        style={{
          background: isUser
            ? "linear-gradient(135deg, #7c6cfc, #a78bfa)"
            : "var(--surface2)",
          border: isUser ? "none" : "1px solid var(--border)",
          color: isUser ? "white" : "var(--text)",
          borderRadius: isUser ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
        }}>
        {entry.text}
      </div>
    </div>
  );
}

// ── Conversation history panel ──────────────────────────────────────────────
function HistoryPanel({
  conversations, onSelect, selectedId, onClose
}: {
  conversations: Conversation[];
  onSelect: (id: string) => void;
  selectedId: string | null;
  onClose: () => void;
}) {
  return (
    <div className="flex flex-col h-full" style={{ background: "var(--surface)" }}>
      <div className="flex items-center justify-between px-5 py-4 border-b" style={{ borderColor: "var(--border)" }}>
        <div className="flex items-center gap-2">
          <MessageSquare className="w-4 h-4" style={{ color: "var(--accent)" }} />
          <span className="font-semibold text-sm" style={{ color: "var(--text)" }}>History</span>
        </div>
        <button onClick={onClose} className="p-1 rounded-lg" style={{ color: "var(--muted)" }}>
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-2">
        {conversations.length === 0 ? (
          <div className="px-5 py-8 text-center text-sm" style={{ color: "var(--muted)" }}>
            No conversations yet.<br />Start talking to create one.
          </div>
        ) : (
          conversations.map(c => (
            <button key={c.id} onClick={() => onSelect(c.id)}
              className="w-full text-left px-5 py-3 transition-colors hover:bg-white/5"
              style={{
                background: selectedId === c.id ? "rgba(124,108,252,0.1)" : undefined,
                borderLeft: selectedId === c.id ? "2px solid var(--accent)" : "2px solid transparent",
              }}>
              <div className="text-sm font-medium truncate" style={{ color: "var(--text)" }}>{c.title}</div>
              <div className="flex items-center gap-1 mt-1 text-xs" style={{ color: "var(--muted)" }}>
                <Clock className="w-3 h-3" />
                {new Date(c.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

// ── Messages viewer ─────────────────────────────────────────────────────────
function MessagesPanel({ messages, loading, onClose }: { messages: Message[]; loading: boolean; onClose: () => void }) {
  return (
    <div className="absolute inset-0 z-10 flex flex-col animate-fade-in"
      style={{ background: "var(--bg)" }}>
      <div className="flex items-center justify-between px-5 py-4 border-b glass sticky top-0 z-10"
        style={{ borderColor: "var(--border)" }}>
        <span className="font-semibold" style={{ color: "var(--text)" }}>Conversation</span>
        <button onClick={onClose} className="p-1 rounded-lg" style={{ color: "var(--muted)" }}>
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {loading ? (
          <div className="flex justify-center py-12">
            <Loader2 className="w-6 h-6 animate-spin" style={{ color: "var(--accent)" }} />
          </div>
        ) : messages.length === 0 ? (
          <div className="text-center py-12 text-sm" style={{ color: "var(--muted)" }}>No messages in this conversation.</div>
        ) : (
          messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className="max-w-xs lg:max-w-sm px-4 py-3 rounded-2xl text-sm leading-relaxed"
                style={{
                  background: m.role === "user" ? "linear-gradient(135deg, #7c6cfc, #a78bfa)" : "var(--surface2)",
                  border: m.role === "user" ? "none" : "1px solid var(--border)",
                  color: m.role === "user" ? "white" : "var(--text)",
                  borderRadius: m.role === "user" ? "18px 18px 4px 18px" : "18px 18px 18px 4px",
                }}>
                {m.content}
                <div className="text-xs mt-1 opacity-50">
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

// ── Main page ───────────────────────────────────────────────────────────────
export default function AgentPage() {
  const router = useRouter();
  const [user, setUserState] = useState<ReturnType<typeof getUser>>(null);
  const {
    state, connected, transcript, reply, chat,
    error, isMicActive, connect, disconnect, startMic,
  } = useVoiceAgent();

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConvId, setSelectedConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loadingMsgs, setLoadingMsgs] = useState(false);
  const [showMsgs, setShowMsgs] = useState(false);

  const chatEndRef = useRef<HTMLDivElement>(null);

  // Guard: redirect to signin if not authenticated (client-side only)
  useEffect(() => {
    if (!isLoggedIn()) router.replace("/signin");
    else setUserState(getUser());
  }, [router]);

  // Load conversations
  useEffect(() => {
    API.conversations().then(setConversations).catch(() => {});
  }, []);

  // Refresh conversations list after each new connection cycle
  useEffect(() => {
    if (state === "LISTENING") {
      API.conversations().then(setConversations).catch(() => {});
    }
  }, [state]);

  // Scroll chat to bottom
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chat]);

  async function loadMessages(id: string) {
    setSelectedConvId(id);
    setShowMsgs(true);
    setLoadingMsgs(true);
    try {
      const msgs = await API.messages(id);
      setMessages(msgs);
    } catch {
      setMessages([]);
    } finally {
      setLoadingMsgs(false);
    }
  }

  async function handleSignOut() {
    try { await API.signOut(); } catch { /* ignore */ }
    clearTokens();
    disconnect();
    router.replace("/signin");
  }

  function toggleConnection() {
    if (connected) {
      disconnect();
    } else {
      connect();
    }
  }

  const initials = user?.full_name
    ? user.full_name.split(" ").map(w => w[0]).join("").toUpperCase().slice(0, 2)
    : user?.email?.[0]?.toUpperCase() ?? "?";

  return (
    <div className="flex h-screen overflow-hidden" style={{ background: "var(--bg)" }}>

      {/* ── Sidebar ── */}
      {sidebarOpen && (
        <div className="w-64 shrink-0 border-r flex flex-col animate-slide-in"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
          <HistoryPanel
            conversations={conversations}
            onSelect={loadMessages}
            selectedId={selectedConvId}
            onClose={() => setSidebarOpen(false)}
          />
        </div>
      )}

      {/* ── Main area ── */}
      <div className="flex-1 flex flex-col min-w-0 relative">

        {/* Messages overlay */}
        {showMsgs && (
          <MessagesPanel messages={messages} loading={loadingMsgs} onClose={() => setShowMsgs(false)} />
        )}

        {/* Top bar */}
        <header className="flex items-center justify-between px-5 py-3 border-b glass shrink-0"
          style={{ borderColor: "var(--border)" }}>
          <div className="flex items-center gap-3">
            {!sidebarOpen && (
              <button onClick={() => setSidebarOpen(true)}
                className="p-2 rounded-lg hover:bg-white/5 transition-colors mr-1"
                style={{ color: "var(--muted)" }}>
                <ChevronRight className="w-4 h-4" />
              </button>
            )}
            {sidebarOpen && (
              <button onClick={() => setSidebarOpen(false)}
                className="p-2 rounded-lg hover:bg-white/5 transition-colors mr-1"
                style={{ color: "var(--muted)" }}>
                <ChevronLeft className="w-4 h-4" />
              </button>
            )}
            <div className="w-7 h-7 rounded-lg flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
              <Mic className="w-4 h-4 text-white" />
            </div>
            <span className="font-bold" style={{ color: "var(--text)" }}>Lumen</span>
          </div>

          <div className="flex items-center gap-3">
            <StateBadge state={state} />
            <div className="flex items-center gap-2 pl-3 border-l" style={{ borderColor: "var(--border)" }}>
              <div className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white"
                style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
                {initials}
              </div>
              <span className="text-sm hidden sm:block truncate max-w-32" style={{ color: "var(--muted)" }}>
                {user?.full_name ?? user?.email ?? ""}
              </span>
              <button onClick={handleSignOut}
                className="p-2 rounded-lg hover:bg-white/5 transition-colors"
                style={{ color: "var(--muted)" }}>
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          </div>
        </header>

        {/* Chat area */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {chat.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center px-8 gap-4">
              <div className="w-16 h-16 rounded-2xl flex items-center justify-center opacity-30"
                style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
                <Mic className="w-8 h-8 text-white" />
              </div>
              {!connected ? (
                <>
                  <p className="font-semibold" style={{ color: "var(--text)" }}>Connect to start</p>
                  <p className="text-sm" style={{ color: "var(--muted)" }}>Press the button below to connect to your voice AI agent.</p>
                </>
              ) : (
                <>
                  <p className="font-semibold" style={{ color: "var(--text)" }}>Ready to listen</p>
                  <p className="text-sm" style={{ color: "var(--muted)" }}>Press the mic button and start speaking.</p>
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

        {/* Error banner */}
        {error && (
          <div className="mx-4 mb-2 px-4 py-3 rounded-xl text-sm animate-fade-in"
            style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#f87171" }}>
            {error}
          </div>
        )}

        {/* Bottom controls */}
        <div className="shrink-0 px-4 py-6 flex flex-col items-center gap-4"
          style={{ borderTop: "1px solid var(--border)" }}>

          {/* Live transcript / reply display */}
          {(transcript || reply) && (
            <div className="w-full max-w-md text-center text-sm animate-fade-in px-4">
              {transcript && state === "PROCESSING" && (
                <p style={{ color: "var(--muted)" }}>
                  <span style={{ color: "var(--accent2)" }}>You said:</span> {transcript}
                </p>
              )}
              {reply && state === "SPEAKING" && (
                <p style={{ color: "var(--text)" }}>
                  <span style={{ color: "var(--accent2)" }}>Lumen:</span> {reply}
                </p>
              )}
            </div>
          )}

          {/* Waveform + orb */}
          <div className="flex flex-col items-center gap-4">
            <SoundWave active={state === "LISTENING" && isMicActive} />
            <MicOrb state={state} isMicActive={isMicActive} onClick={connected ? startMic : () => {}} />
          </div>

          {/* Connect / mic hint */}
          <div className="flex flex-col items-center gap-2">
            <button onClick={toggleConnection}
              className="px-5 py-2 rounded-xl text-sm font-medium transition-all"
              style={{
                background: connected ? "rgba(239,68,68,0.1)" : "rgba(124,108,252,0.15)",
                border: connected ? "1px solid rgba(239,68,68,0.3)" : "1px solid rgba(124,108,252,0.3)",
                color: connected ? "#f87171" : "var(--accent2)",
              }}>
              {connected ? "Disconnect" : "Connect"}
            </button>
            {connected && (
              <p className="text-xs" style={{ color: "var(--muted)" }}>
                {isMicActive ? "Tap orb to mute" : "Tap orb to speak"}
              </p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
