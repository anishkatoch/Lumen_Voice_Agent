"use client";
import { useEffect, useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import { Mic, Loader2, Eye, EyeOff, CheckCircle } from "lucide-react";
import { API } from "@/lib/api";
import { setTokens, setUser } from "@/lib/auth";

export default function AuthCallback() {
  const router = useRouter();
  const [mode, setMode] = useState<"loading" | "reset" | "confirm" | "error">("loading");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);

  useEffect(() => {
    // Supabase puts tokens in the URL hash: #access_token=...&type=recovery
    const hash = window.location.hash.substring(1);
    const params = new URLSearchParams(hash);
    const token = params.get("access_token");
    const type = params.get("type");

    if (!token) {
      setMode("error");
      setError("Invalid or expired link. Please request a new one.");
      return;
    }

    if (type === "recovery") {
      setAccessToken(token);
      setMode("reset");
    } else if (type === "signup") {
      // Email confirmation — sign them in with the token
      const refreshToken = params.get("refresh_token");
      if (!refreshToken) {
        setMode("error");
        setError("Invalid confirmation link. Please request a new one.");
        return;
      }
      setTokens(token, refreshToken);
      API.me().then(user => {
        setUser(user);
        setMode("confirm");
        setTimeout(() => router.replace("/agent"), 2000);
      }).catch(() => {
        setMode("error");
        setError("Confirmation failed. Please try signing in.");
      });
    } else {
      setMode("error");
      setError("Unknown link type. Please try again.");
    }
  }, [router]);

  async function handleResetSubmit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) { setError("Passwords do not match"); return; }
    if (password.length < 6) { setError("Password must be at least 6 characters"); return; }
    setError(null);
    setLoading(true);
    try {
      // Use Supabase REST API directly with the recovery token
      const res = await fetch(`${process.env.NEXT_PUBLIC_SUPABASE_URL ?? ""}/auth/v1/user`, {
        method: "PUT",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${accessToken}`,
          "apikey": process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "",
        },
        body: JSON.stringify({ password }),
      });
      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.msg ?? "Password update failed");
      }
      setDone(true);
      setTimeout(() => router.replace("/signin"), 2500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update password");
    } finally {
      setLoading(false);
    }
  }

  // ── Loading ──────────────────────────────────────────────────────────
  if (mode === "loading") {
    return (
      <div className="min-h-screen flex items-center justify-center" style={{ background: "var(--bg)" }}>
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: "var(--accent)" }} />
      </div>
    );
  }

  // ── Email confirmed ──────────────────────────────────────────────────
  if (mode === "confirm") {
    return (
      <div className="min-h-screen flex items-center justify-center p-8" style={{ background: "var(--bg)" }}>
        <div className="text-center animate-fade-in">
          <CheckCircle className="w-16 h-16 mx-auto mb-4" style={{ color: "#22c55e" }} />
          <h2 className="text-2xl font-bold mb-2" style={{ color: "var(--text)" }}>Email confirmed!</h2>
          <p style={{ color: "var(--muted)" }}>Taking you to the app…</p>
        </div>
      </div>
    );
  }

  // ── Error ────────────────────────────────────────────────────────────
  if (mode === "error") {
    return (
      <div className="min-h-screen flex items-center justify-center p-8" style={{ background: "var(--bg)" }}>
        <div className="text-center max-w-sm animate-fade-in">
          <div className="w-16 h-16 rounded-full mx-auto mb-4 flex items-center justify-center"
            style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)" }}>
            <span className="text-2xl">✕</span>
          </div>
          <h2 className="text-2xl font-bold mb-3" style={{ color: "var(--text)" }}>Something went wrong</h2>
          <p className="mb-6" style={{ color: "var(--muted)" }}>{error}</p>
          <a href="/signin" className="inline-block px-6 py-3 rounded-xl font-semibold text-white"
            style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
            Go to sign in
          </a>
        </div>
      </div>
    );
  }

  // ── Password reset form ──────────────────────────────────────────────
  return (
    <div className="min-h-screen flex items-center justify-center p-8" style={{ background: "var(--bg)" }}>
      <div className="w-full max-w-md">
        <div className="flex items-center gap-3 mb-10">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
            <Mic className="w-5 h-5 text-white" />
          </div>
          <span className="text-xl font-bold" style={{ color: "var(--text)" }}>Lumen</span>
        </div>

        {done ? (
          <div className="text-center animate-fade-in">
            <CheckCircle className="w-16 h-16 mx-auto mb-4" style={{ color: "var(--accent)" }} />
            <h2 className="text-2xl font-bold mb-2" style={{ color: "var(--text)" }}>Password updated!</h2>
            <p style={{ color: "var(--muted)" }}>Redirecting you to sign in…</p>
          </div>
        ) : (
          <>
            <h2 className="text-3xl font-bold mb-2" style={{ color: "var(--text)" }}>Set new password</h2>
            <p className="mb-8" style={{ color: "var(--muted)" }}>Choose a strong password for your account.</p>

            <form onSubmit={handleResetSubmit} className="space-y-5">
              <div>
                <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>New password</label>
                <div className="relative">
                  <input
                    type={showPwd ? "text" : "password"}
                    required
                    minLength={6}
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    placeholder="Min. 6 characters"
                    className="w-full px-4 py-3 pr-12 rounded-xl outline-none transition-all text-sm"
                    style={{ background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--text)" }}
                    onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                    onBlur={e => (e.target.style.borderColor = "var(--border)")}
                  />
                  <button type="button" onClick={() => setShowPwd(v => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-1" style={{ color: "var(--muted)" }}>
                    {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>Confirm password</label>
                <input
                  type={showPwd ? "text" : "password"}
                  required
                  value={confirm}
                  onChange={e => setConfirm(e.target.value)}
                  placeholder="Repeat your password"
                  className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                  style={{ background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--text)" }}
                  onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                  onBlur={e => (e.target.style.borderColor = "var(--border)")}
                />
              </div>

              {error && (
                <div className="px-4 py-3 rounded-xl text-sm animate-fade-in"
                  style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#f87171" }}>
                  {error}
                </div>
              )}

              <button type="submit" disabled={loading}
                className="w-full py-3 rounded-xl font-semibold text-white transition-all flex items-center justify-center gap-2"
                style={{
                  background: loading ? "var(--surface2)" : "linear-gradient(135deg, #7c6cfc, #a78bfa)",
                  boxShadow: loading ? "none" : "0 0 20px rgba(124,108,252,0.3)",
                }}>
                {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
                {loading ? "Updating…" : "Update password"}
              </button>
            </form>
          </>
        )}
      </div>
    </div>
  );
}
