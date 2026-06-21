"use client";
import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API } from "@/lib/api";
import { setTokens, setUser } from "@/lib/auth";
import { Mic, Eye, EyeOff, Loader2 } from "lucide-react";

export default function SignIn() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await API.signIn(email, password);
      setTokens(data.access_token, data.refresh_token);
      setUser(data.user);
      router.replace("/agent");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex" style={{ background: "var(--bg)" }}>
      {/* Left branding panel */}
      <div className="hidden lg:flex flex-1 flex-col items-center justify-center p-16 relative overflow-hidden"
        style={{ background: "linear-gradient(135deg, #0d0d1a 0%, #13102a 100%)" }}>
        <div className="absolute inset-0 opacity-20"
          style={{ backgroundImage: "radial-gradient(circle at 30% 40%, #7c6cfc 0%, transparent 60%), radial-gradient(circle at 70% 80%, #a78bfa 0%, transparent 50%)" }} />
        <div className="relative z-10 text-center max-w-sm">
          <div className="w-20 h-20 rounded-2xl mx-auto mb-8 flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)", boxShadow: "0 0 40px rgba(124,108,252,0.4)" }}>
            <Mic className="w-10 h-10 text-white" />
          </div>
          <h1 className="text-4xl font-bold mb-4" style={{ color: "var(--text)" }}>Lumen</h1>
          <p className="text-lg" style={{ color: "var(--muted)" }}>
            Your intelligent voice AI agent. Talk naturally, get instant answers.
          </p>
          <div className="mt-12 flex gap-2 justify-center">
            {[...Array(5)].map((_, i) => (
              <div key={i} className="w-1.5 rounded-full wave-bar"
                style={{ background: "var(--accent)", animationDelay: `${i * 0.1}s` }} />
            ))}
          </div>
        </div>
      </div>

      {/* Right form panel */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
              <Mic className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold" style={{ color: "var(--text)" }}>Lumen</span>
          </div>

          <h2 className="text-3xl font-bold mb-2" style={{ color: "var(--text)" }}>Welcome back</h2>
          <p className="mb-8" style={{ color: "var(--muted)" }}>Sign in to your account to continue</p>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>
                Email address
              </label>
              <input
                type="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{
                  background: "var(--surface2)",
                  border: "1px solid var(--border)",
                  color: "var(--text)",
                }}
                onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                onBlur={e => (e.target.style.borderColor = "var(--border)")}
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-2">
                <label className="text-sm font-medium" style={{ color: "var(--muted)" }}>Password</label>
                <Link href="/forgot-password" className="text-xs" style={{ color: "var(--accent2)" }}>
                  Forgot password?
                </Link>
              </div>
              <div className="relative">
                <input
                  type={showPwd ? "text" : "password"}
                  required
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full px-4 py-3 pr-12 rounded-xl outline-none transition-all text-sm"
                  style={{
                    background: "var(--surface2)",
                    border: "1px solid var(--border)",
                    color: "var(--text)",
                  }}
                  onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                  onBlur={e => (e.target.style.borderColor = "var(--border)")}
                />
                <button type="button" onClick={() => setShowPwd(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-1 rounded"
                  style={{ color: "var(--muted)" }}>
                  {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
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
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>

          <p className="text-center mt-8 text-sm" style={{ color: "var(--muted)" }}>
            Don&apos;t have an account?{" "}
            <Link href="/signup" className="font-medium" style={{ color: "var(--accent2)" }}>
              Create one
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
