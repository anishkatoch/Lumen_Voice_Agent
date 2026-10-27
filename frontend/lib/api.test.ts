import { describe, it, expect, vi, beforeEach } from "vitest";
import { API } from "./api";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("API.request", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("does not send an Authorization header when no token is stored", async () => {
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ ok: true }));

    await API.request("/api/me");

    const [, init] = fetchMock.mock.calls[0];
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("sends the stored access token as a Bearer header", async () => {
    localStorage.setItem("access_token", "token-123");
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ ok: true }));

    await API.request("/api/me");

    const [, init] = fetchMock.mock.calls[0];
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer token-123");
  });

  it("throws with the server's detail message on a non-ok response", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ detail: "Candidate not found" }, 404));

    await expect(API.request("/api/hr/candidates/xyz")).rejects.toThrow("Candidate not found");
  });

  it("falls back to a generic message when the error body isn't JSON", async () => {
    vi.spyOn(global, "fetch").mockResolvedValue(
      new Response("not json", { status: 500 })
    );

    await expect(API.request("/api/whatever")).rejects.toThrow("Request failed (500)");
  });

  it("on 401, refreshes the token and retries the request once", async () => {
    localStorage.setItem("access_token", "expired-token");
    localStorage.setItem("refresh_token", "refresh-abc");

    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(jsonResponse({ detail: "unauthorized" }, 401)) // first attempt
      .mockResolvedValueOnce(
        jsonResponse({ access_token: "new-token", refresh_token: "new-refresh" })
      ) // refresh call
      .mockResolvedValueOnce(jsonResponse({ id: "conv-1" })); // retried request

    const result = await API.request<{ id: string }>("/api/conversations");

    expect(result).toEqual({ id: "conv-1" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // the retried request must use the NEW token
    const retryInit = fetchMock.mock.calls[2][1];
    const retryHeaders = retryInit?.headers as Record<string, string>;
    expect(retryHeaders.Authorization).toBe("Bearer new-token");
    // localStorage should be updated with the refreshed tokens
    expect(localStorage.getItem("access_token")).toBe("new-token");
    expect(localStorage.getItem("refresh_token")).toBe("new-refresh");
  });

  it("on 401 with no refresh token available, throws session-expired without retrying", async () => {
    localStorage.setItem("access_token", "expired-token");
    // no refresh_token stored

    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({}, 401));

    await expect(API.request("/api/conversations")).rejects.toThrow("Session expired");
    // only the original failed request - no refresh call, no retry
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("on 401 where the refresh call itself fails, throws session-expired", async () => {
    localStorage.setItem("access_token", "expired-token");
    localStorage.setItem("refresh_token", "refresh-abc");

    vi.spyOn(global, "fetch")
      .mockResolvedValueOnce(jsonResponse({}, 401)) // original request
      .mockResolvedValueOnce(jsonResponse({ detail: "invalid refresh token" }, 401)); // refresh fails

    await expect(API.request("/api/conversations")).rejects.toThrow("Session expired");
  });
});

describe("API.upload", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("sends FormData without a Content-Type header (browser sets the multipart boundary)", async () => {
    localStorage.setItem("access_token", "token-123");
    const fetchMock = vi.spyOn(global, "fetch").mockResolvedValue(jsonResponse({ id: "doc-1" }));

    const fd = new FormData();
    fd.append("file", new Blob(["hello"]), "hello.txt");
    await API.upload("/api/user-kb/documents", fd);

    const [, init] = fetchMock.mock.calls[0];
    const headers = init?.headers as Record<string, string>;
    expect(headers["Content-Type"]).toBeUndefined();
    expect(headers.Authorization).toBe("Bearer token-123");
    expect(init?.body).toBe(fd);
  });

  it("retries with a refreshed token on 401, same as request()", async () => {
    localStorage.setItem("access_token", "expired");
    localStorage.setItem("refresh_token", "refresh-abc");

    const fetchMock = vi
      .spyOn(global, "fetch")
      .mockResolvedValueOnce(jsonResponse({}, 401))
      .mockResolvedValueOnce(jsonResponse({ access_token: "fresh", refresh_token: "fresh-r" }))
      .mockResolvedValueOnce(jsonResponse({ id: "doc-1" }));

    const fd = new FormData();
    const result = await API.upload<{ id: string }>("/api/user-kb/documents", fd);

    expect(result).toEqual({ id: "doc-1" });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});

describe("API base URL configuration", () => {
  it("derives the websocket base from the http(s) base", () => {
    expect(API.ws.startsWith("ws")).toBe(true);
    expect(API.ws).toBe(API.base.replace(/^http/, "ws"));
  });
});
