"use client";
import { useRef, useState, useCallback, useEffect } from "react";
import { API } from "@/lib/api";
import { getToken } from "@/lib/auth";

export type AgentState = "IDLE" | "LISTENING" | "PROCESSING" | "SPEAKING";
export type RagScope = "personal" | "global" | "both";

export interface ChatEntry {
  id: string;
  role: "user" | "assistant";
  text: string;
  ts: number;
}

const SAMPLE_RATE = 16000;
const CHUNK_MS = 250;
const CHUNK_SAMPLES = SAMPLE_RATE * (CHUNK_MS / 1000);


export function useVoiceAgent() {
  const [state, setState] = useState<AgentState>("IDLE");
  const [connected, setConnected] = useState(false);
  const [transcript, setTranscript] = useState<string | null>(null);
  const [reply, setReply] = useState<string | null>(null);
  const [chat, setChat] = useState<ChatEntry[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isMicActive, setIsMicActive] = useState(false);
  const [ragScope, setRagScopeState] = useState<RagScope>("both");
  const ragScopeRef = useRef<RagScope>("both");

  // Mic
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // Playback
  const playCtxRef = useRef<AudioContext | null>(null);
  const activeSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const mp3ChunksRef = useRef<Uint8Array[]>([]);       // current batch of chunks
  const playQueueRef = useRef<Uint8Array[][]>([]);      // queue of batches to play
  const isPlayingRef = useRef(false);

  const addChat = useCallback((role: "user" | "assistant", text: string) => {
    setChat(prev => [...prev, { id: crypto.randomUUID(), role, text, ts: Date.now() }]);
  }, []);

  // ── Stop all audio immediately ──────────────────────────────────────
  const stopAudio = useCallback(() => {
    if (activeSourceRef.current) {
      try { activeSourceRef.current.stop(); } catch { /* already ended */ }
      activeSourceRef.current = null;
    }
    mp3ChunksRef.current = [];
    playQueueRef.current = [];
    isPlayingRef.current = false;
  }, []);

  // ── Play one batch (one sentence worth of raw PCM chunks) ─────────
  const playNextBatch = useCallback(async () => {
    if (isPlayingRef.current) return;
    isPlayingRef.current = true;

    const batch = playQueueRef.current.shift();
    if (!batch || batch.length === 0) {
      isPlayingRef.current = false;
      return;
    }

    // Merge all chunks into one buffer
    const totalLen = batch.reduce((n, c) => n + c.byteLength, 0);
    const merged = new Uint8Array(totalLen);
    let offset = 0;
    for (const c of batch) { merged.set(c, offset); offset += c.byteLength; }

    try {
      if (!playCtxRef.current || playCtxRef.current.state === "closed") {
        playCtxRef.current = new AudioContext({ sampleRate: 16000 });
      }
      const ctx = playCtxRef.current;
      if (ctx.state === "suspended") await ctx.resume();

      // PCM is raw 16-bit signed integers at 16kHz — decode manually, no codec needed
      const pcm = new Int16Array(merged.buffer);
      const audioBuffer = ctx.createBuffer(1, pcm.length, 16000);
      const channelData = audioBuffer.getChannelData(0);
      for (let i = 0; i < pcm.length; i++) {
        channelData[i] = pcm[i] / 32768.0;
      }
      console.log("[Audio] PCM decoded:", audioBuffer.duration.toFixed(2), "s", totalLen, "bytes");

      const src = ctx.createBufferSource();
      src.buffer = audioBuffer;
      src.connect(ctx.destination);
      activeSourceRef.current = src;

      wsRef.current?.send(JSON.stringify({ type: "playback_started" }));

      src.start();
      src.onended = () => {
        activeSourceRef.current = null;
        isPlayingRef.current = false;
        if (playQueueRef.current.length > 0) {
          playNextBatch();
        } else {
          wsRef.current?.send(JSON.stringify({ type: "playback_ended" }));
        }
      };
    } catch (err) {
      console.error("[Playback] PCM decode error:", err);
      isPlayingRef.current = false;
      wsRef.current?.send(JSON.stringify({ type: "playback_ended" }));
      setError("Failed to play audio response");
    }
  }, []);

  // ── Flush current chunk buffer as a sentence batch ─────────────────
  const flushCurrentBatch = useCallback(() => {
    if (mp3ChunksRef.current.length === 0) return;
    const batch = [...mp3ChunksRef.current];
    mp3ChunksRef.current = [];
    playQueueRef.current.push(batch);
    playNextBatch();
  }, [playNextBatch]);

  const setRagScope = useCallback((scope: RagScope) => {
    setRagScopeState(scope);
    ragScopeRef.current = scope;
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "set_rag_scope", scope }));
    }
  }, []);

  const connect = useCallback(async (opts?: { agentId?: string; docIds?: string[] }) => {
    let token = getToken();
    console.log("[WS] connect() called, token present:", !!token);
    if (!token) {
      setError("Not signed in. Please sign in again.");
      return;
    }
    if (wsRef.current) { console.log("[WS] Already have a socket, skipping"); return; }

    // Fix base64url → base64 before atob (JWT uses base64url with - and _)
    function b64urlDecode(s: string) {
      return atob(s.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(s.length / 4) * 4, "="));
    }

    // Refresh token if it's about to expire or already expired
    try {
      const payload = JSON.parse(b64urlDecode(token.split(".")[1]));
      const expiresIn = payload.exp * 1000 - Date.now();
      console.log("[WS] Token expires in:", Math.round(expiresIn / 1000), "s");
      if (expiresIn < 60_000) {
        console.log("[WS] Token near expiry, refreshing...");
        const { tryRefresh } = await import("@/lib/auth");
        const ok = await tryRefresh();
        console.log("[WS] Refresh result:", ok);
        if (!ok) {
          setError("Session expired. Please sign in again.");
          return;
        }
        token = getToken()!;
      }
    } catch (e) { console.warn("[WS] Token decode error:", e); }

    // Pre-warm AudioContext during this user gesture so Chrome allows
    // audio playback later (browsers block audio outside user gestures)
    try {
      if (!playCtxRef.current || playCtxRef.current.state === "closed") {
        playCtxRef.current = new AudioContext({ sampleRate: 16000 });
      }
      if (playCtxRef.current.state === "suspended") {
        await playCtxRef.current.resume();
      }
    } catch { /* ignore — will retry at play time */ }

    const wsUrl = opts?.agentId
      ? (() => {
          const base = `${API.ws}/ws/agent/${opts.agentId}/${token}`;
          return opts.docIds?.length ? `${base}?docs=${opts.docIds.join(",")}` : base;
        })()
      : `${API.ws}/ws/${token}`;
    console.log("[WS] Connecting to:", wsUrl.replace(token, token.slice(0,20)+"..."));
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[WS] Connected ✓");
      setConnected(true);
      setError(null);
      ws.send(JSON.stringify({ type: "sample_rate", value: SAMPLE_RATE }));
      // Sync any pre-connection scope selection (backend defaults to "both")
      const currentScope = ragScopeRef.current;
      if (currentScope !== "both") {
        ws.send(JSON.stringify({ type: "set_rag_scope", scope: currentScope }));
      }
    };

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        mp3ChunksRef.current.push(new Uint8Array(ev.data));
        return;
      }

      try {
        const msg = JSON.parse(ev.data as string);
        console.log("[WS] msg:", msg.type, msg.state ?? msg.text?.slice?.(0,40) ?? "");
        switch (msg.type) {
          case "ready":
            setConversationId(msg.conversation_id);
            setState("LISTENING");
            break;
          case "state":
            setState(msg.state as AgentState);
            break;
          case "transcript":
            setTranscript(msg.text);
            setReply(null);
            addChat("user", msg.text);
            break;
          case "reply":
            setReply(msg.text);
            addChat("assistant", msg.text);
            break;
          case "sentence_end":
            flushCurrentBatch();
            break;
          case "audio_end":
            flushCurrentBatch();
            break;
          case "stop_audio":
            stopAudio();
            break;
        }
      } catch { /* non-JSON */ }
    };

    ws.onclose = (ev) => {
      console.log("[WS] Closed — code:", ev.code, "reason:", ev.reason);
      setConnected(false);
      setState("IDLE");
      wsRef.current = null;
      stopMicInternal();
      // Reset playback state so next session can play audio
      mp3ChunksRef.current = [];
      playQueueRef.current = [];
      isPlayingRef.current = false;
      if (ev.code === 4001) {
        setError("Session expired. Please sign in again.");
      }
    };

    ws.onerror = (ev) => {
      console.error("[WS] Error:", ev);
      wsRef.current = null;
      setConnected(false);
      setState("IDLE");
      setError("Could not connect to server. Please try again.");
    };
  }, [addChat, flushCurrentBatch, stopAudio]);

  const disconnect = useCallback(() => {
    stopMicInternal();
    stopAudio();
    wsRef.current?.close();
    wsRef.current = null;
    setChat([]);
    setTranscript(null);
    setReply(null);
    setConversationId(null);
  }, [stopAudio]); // eslint-disable-line react-hooks/exhaustive-deps

  function stopMicInternal() {
    workletRef.current?.disconnect();
    workletRef.current = null;
    streamRef.current?.getTracks().forEach(t => t.stop());
    streamRef.current = null;
    audioCtxRef.current?.close();
    audioCtxRef.current = null;
    setIsMicActive(false);
  }

  const stopMic = useCallback(stopMicInternal, []); // eslint-disable-line react-hooks/exhaustive-deps

  const startMic = useCallback(async () => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    if (isMicActive) { stopMicInternal(); return; }

    let stream: MediaStream | null = null;
    let ctx: AudioContext | null = null;
    let blobUrl: string | null = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;

      ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
      audioCtxRef.current = ctx;

      const workletCode = `
        class ChunkProcessor extends AudioWorkletProcessor {
          constructor() { super(); this._buf = []; this._size = ${CHUNK_SAMPLES}; }
          process(inputs) {
            const ch = inputs[0]?.[0];
            if (!ch) return true;
            this._buf.push(...ch);
            while (this._buf.length >= this._size) {
              const slice = this._buf.splice(0, this._size);
              const int16 = new Int16Array(slice.map(s => Math.max(-1, Math.min(1, s)) * 0x7fff));
              this.port.postMessage(int16.buffer, [int16.buffer]);
            }
            return true;
          }
        }
        registerProcessor('chunk-processor', ChunkProcessor);
      `;
      const blob = new Blob([workletCode], { type: "application/javascript" });
      blobUrl = URL.createObjectURL(blob);
      try {
        await ctx.audioWorklet.addModule(blobUrl);
      } finally {
        // Always revoke — even if addModule throws
        URL.revokeObjectURL(blobUrl);
        blobUrl = null;
      }

      const source = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, "chunk-processor");
      workletRef.current = worklet;

      worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(e.data);
        } else if (wsRef.current && wsRef.current.readyState !== WebSocket.CONNECTING) {
          // WebSocket died — stop mic automatically
          stopMicInternal();
          setError("Connection lost. Please reconnect.");
        }
      };

      // source → worklet only; do NOT connect worklet to destination (would cause feedback)
      source.connect(worklet);
      setIsMicActive(true);
    } catch (err) {
      // Clean up on any failure
      stream?.getTracks().forEach(t => t.stop());
      ctx?.close();
      setError(err instanceof Error ? err.message : "Microphone access denied");
    }
  }, [isMicActive]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    stopMicInternal();
    wsRef.current?.close();
    playCtxRef.current?.close();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sendTextMessage = useCallback((text: string) => {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: "text_input", text }));
  }, []);

  // Auto-start mic the first time state becomes LISTENING after connecting.
  // This makes connect() a single one-button action — no separate mic tap needed.
  const micAutoStartedRef = useRef(false);
  useEffect(() => {
    if (connected && state === "LISTENING" && !isMicActive && !micAutoStartedRef.current) {
      micAutoStartedRef.current = true;
      startMic();
    }
    if (!connected) micAutoStartedRef.current = false;
  }); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    state, connected, transcript, reply, chat, conversationId, error, isMicActive,
    ragScope, setRagScope,
    connect, disconnect, startMic, stopMic, sendTextMessage,
  };
}
