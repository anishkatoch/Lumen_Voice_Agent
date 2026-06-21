"use client";
import { useRef, useState, useCallback, useEffect } from "react";
import { API } from "@/lib/api";
import { getToken } from "@/lib/auth";

export type AgentState = "IDLE" | "LISTENING" | "PROCESSING" | "SPEAKING";

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

  // Mic
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // Playback — collect all chunks, decode + play the full MP3 on audio_end
  const playCtxRef = useRef<AudioContext | null>(null);
  const mp3ChunksRef = useRef<Uint8Array[]>([]);
  const activeSourceRef = useRef<AudioBufferSourceNode | null>(null);

  const addChat = useCallback((role: "user" | "assistant", text: string) => {
    setChat(prev => [...prev, { id: crypto.randomUUID(), role, text, ts: Date.now() }]);
  }, []);

  // Stop playback immediately (barge-in)
  const stopAudio = useCallback(() => {
    if (activeSourceRef.current) {
      try { activeSourceRef.current.stop(); } catch { /* already ended */ }
      activeSourceRef.current = null;
    }
    mp3ChunksRef.current = [];
  }, []);  // wsRef intentionally excluded — we use it directly without dep

  // Called on audio_end — merge all chunks into one MP3 and play it
  const playCollected = useCallback(async () => {
    const chunks = mp3ChunksRef.current;
    if (chunks.length === 0) return;
    mp3ChunksRef.current = [];

    // Merge into single ArrayBuffer
    const totalLen = chunks.reduce((n, c) => n + c.byteLength, 0);
    const merged = new Uint8Array(totalLen);
    let offset = 0;
    for (const c of chunks) { merged.set(c, offset); offset += c.byteLength; }

    try {
      if (!playCtxRef.current || playCtxRef.current.state === "closed") {
        playCtxRef.current = new AudioContext();
      }
      const ctx = playCtxRef.current;
      const decoded = await ctx.decodeAudioData(merged.buffer);

      // If barge-in already fired while decoding, don't play
      if (activeSourceRef.current === null && mp3ChunksRef.current.length === 0) {
        const src = ctx.createBufferSource();
        src.buffer = decoded;
        src.connect(ctx.destination);
        activeSourceRef.current = src;

        // Tell backend playback started — stay in SPEAKING for barge-in
        wsRef.current?.send(JSON.stringify({ type: "playback_started" }));

        src.start();
        src.onended = () => {
          activeSourceRef.current = null;
          // Tell backend playback finished — switch to LISTENING
          wsRef.current?.send(JSON.stringify({ type: "playback_ended" }));
        };
      }
    } catch {
      // decode failed — notify backend so it doesn't stay stuck in SPEAKING
      wsRef.current?.send(JSON.stringify({ type: "playback_ended" }));
    }
  }, []);

  const connect = useCallback(async () => {
    const token = getToken();
    if (!token) { setError("Not authenticated"); return; }
    if (wsRef.current) return;

    const ws = new WebSocket(`${API.ws}/ws/${token}`);
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError(null);
      ws.send(JSON.stringify({ type: "sample_rate", value: SAMPLE_RATE }));
    };

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        // Collect MP3 chunk — don't play yet
        mp3ChunksRef.current.push(new Uint8Array(ev.data));
        return;
      }
      try {
        const msg = JSON.parse(ev.data as string);
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
          case "audio_end":
            // All chunks received — decode full MP3 and play cleanly
            playCollected();
            break;
          case "stop_audio":
            // Barge-in — stop whatever is playing right now
            stopAudio();
            break;
        }
      } catch { /* non-JSON */ }
    };

    ws.onclose = () => {
      setConnected(false);
      setState("IDLE");
      wsRef.current = null;
      stopMicInternal();
    };

    ws.onerror = () => {
      setError("WebSocket connection error");
      ws.close();
    };
  }, [addChat, playCollected, stopAudio]);

  const disconnect = useCallback(() => {
    stopMicInternal();
    stopAudio();
    wsRef.current?.close();
    wsRef.current = null;
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

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { sampleRate: SAMPLE_RATE, channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;

      const ctx = new AudioContext({ sampleRate: SAMPLE_RATE });
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
      const blobUrl = URL.createObjectURL(blob);
      await ctx.audioWorklet.addModule(blobUrl);
      URL.revokeObjectURL(blobUrl);

      const source = ctx.createMediaStreamSource(stream);
      const worklet = new AudioWorkletNode(ctx, "chunk-processor");
      workletRef.current = worklet;

      worklet.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.send(e.data);
        }
      };

      source.connect(worklet);
      worklet.connect(ctx.destination);
      setIsMicActive(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Microphone access denied");
    }
  }, [isMicActive]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => {
    stopMicInternal();
    wsRef.current?.close();
    playCtxRef.current?.close();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    state, connected, transcript, reply, chat, conversationId, error, isMicActive,
    connect, disconnect, startMic, stopMic,
  };
}
