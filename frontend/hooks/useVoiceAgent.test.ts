import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useVoiceAgent } from "./useVoiceAgent";

/**
 * Minimal WebSocket stub matching the subset of the real API this hook
 * uses (onopen/onmessage/onclose/onerror, readyState, send, close).
 * Lets tests drive the connection lifecycle deterministically without a
 * real server or jsdom's (nonexistent) WebSocket implementation.
 */
class MockWebSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static instances: MockWebSocket[] = [];

  url: string;
  readyState = MockWebSocket.CONNECTING;
  binaryType = "blob";
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: { code: number; reason: string }) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  send(data: unknown) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.({ code: 1000, reason: "" });
  }

  // test helpers
  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(payload: object) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

beforeEach(() => {
  localStorage.clear();
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  // AudioContext isn't implemented in jsdom; connect() wraps its prewarm in
  // try/catch and continues regardless, so leaving it undefined is fine -
  // but define a harmless stub so nothing throws unexpectedly elsewhere.
  vi.stubGlobal("AudioContext", vi.fn().mockImplementation(() => ({
    state: "running",
    resume: vi.fn(),
    close: vi.fn(),
  })));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useVoiceAgent.connect", () => {
  it("sets an error and does not open a socket when no token is stored", async () => {
    const { result } = renderHook(() => useVoiceAgent());

    await act(async () => {
      await result.current.connect();
    });

    expect(result.current.error).toMatch(/not signed in/i);
    expect(MockWebSocket.instances).toHaveLength(0);
  });

  it("opens a WebSocket to the expected URL when a token is present", async () => {
    // A JWT with a far-future exp so the token-refresh branch is skipped.
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }));
    localStorage.setItem("access_token", `header.${payload}.sig`);

    const { result } = renderHook(() => useVoiceAgent());

    await act(async () => {
      await result.current.connect();
    });

    expect(MockWebSocket.instances).toHaveLength(1);
    expect(MockWebSocket.instances[0].url).toContain("/ws/");
  });

  it("transitions to LISTENING and stores conversationId on the 'ready' message", async () => {
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }));
    localStorage.setItem("access_token", `header.${payload}.sig`);

    const { result } = renderHook(() => useVoiceAgent());
    await act(async () => {
      await result.current.connect();
    });

    const ws = MockWebSocket.instances[0];
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "ready", conversation_id: "conv-42" }));

    await waitFor(() => {
      expect(result.current.state).toBe("LISTENING");
      expect(result.current.conversationId).toBe("conv-42");
    });
  });

  it("records a user chat entry on 'transcript' and an assistant entry on 'reply'", async () => {
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }));
    localStorage.setItem("access_token", `header.${payload}.sig`);

    const { result } = renderHook(() => useVoiceAgent());
    await act(async () => {
      await result.current.connect();
    });
    const ws = MockWebSocket.instances[0];
    act(() => ws.simulateOpen());

    act(() => ws.simulateMessage({ type: "transcript", text: "What's the weather?" }));
    act(() => ws.simulateMessage({ type: "reply", text: "It's sunny." }));

    await waitFor(() => {
      expect(result.current.chat.map(c => [c.role, c.text])).toEqual([
        ["user", "What's the weather?"],
        ["assistant", "It's sunny."],
      ]);
    });
  });

  it("sets connected=false and resets state on close with an expired-session code", async () => {
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }));
    localStorage.setItem("access_token", `header.${payload}.sig`);

    const { result } = renderHook(() => useVoiceAgent());
    await act(async () => {
      await result.current.connect();
    });
    const ws = MockWebSocket.instances[0];
    act(() => ws.simulateOpen());

    act(() => {
      ws.onclose?.({ code: 4001, reason: "expired" });
    });

    await waitFor(() => {
      expect(result.current.connected).toBe(false);
      expect(result.current.state).toBe("IDLE");
      expect(result.current.error).toMatch(/session expired/i);
    });
  });
});

describe("useVoiceAgent.disconnect", () => {
  it("clears chat, transcript and conversationId", async () => {
    const payload = btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 }));
    localStorage.setItem("access_token", `header.${payload}.sig`);

    const { result } = renderHook(() => useVoiceAgent());
    await act(async () => {
      await result.current.connect();
    });
    const ws = MockWebSocket.instances[0];
    act(() => ws.simulateOpen());
    act(() => ws.simulateMessage({ type: "ready", conversation_id: "conv-1" }));
    act(() => ws.simulateMessage({ type: "transcript", text: "hi" }));

    act(() => result.current.disconnect());

    await waitFor(() => {
      expect(result.current.chat).toEqual([]);
      expect(result.current.transcript).toBeNull();
      expect(result.current.conversationId).toBeNull();
    });
  });
});
