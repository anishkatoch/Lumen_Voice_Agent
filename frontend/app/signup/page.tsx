"use client";
import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API } from "@/lib/api";
import { setTokens, setUser } from "@/lib/auth";
import { Mic, Eye, EyeOff, Loader2, CheckCircle } from "lucide-react";

export default function SignUp() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await API.signUp(email, password, name || undefined);
      if (data.message === "confirmation_required") {
        setConfirm(true);
      } else if (data.access_token && data.refresh_token && data.user) {
        setTokens(data.access_token, data.refresh_token);
        setUser(data.user);
        router.replace("/agent");
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign up failed");
    } finally {
      setLoading(false);
    }
  }

  if (confirm) {
    return (
      <div className="min-h-screen flex items-center justify-center p-8" style={{ background: "var(--bg)" }}>
        <div className="text-center max-w-sm animate-fade-in">
          <CheckCircle className="w-16 h-16 mx-auto mb-6" style={{ color: "var(--accent)" }} />
          <h2 className="text-2xl font-bold mb-3" style={{ color: "var(--text)" }}>Check your email</h2>
          <p style={{ color: "var(--muted)" }}>
            We sent a confirmation link to <strong style={{ color: "var(--text)" }}>{email}</strong>.
            Click it to activate your account, then sign in.
          </p>
          <Link href="/signin" className="inline-block mt-8 px-6 py-3 rounded-xl font-semibold text-white"
            style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
            Go to sign in
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex" style={{ background: "var(--bg)" }}>
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
            Start a conversation with your AI voice agent today.
          </p>
        </div>
      </div>

      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-md">
          <div className="lg:hidden flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
              <Mic className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold" style={{ color: "var(--text)" }}>Lumen</span>
          </div>

          <h2 className="text-3xl font-bold mb-2" style={{ color: "var(--text)" }}>Create account</h2>
          <p className="mb-8" style={{ color: "var(--muted)" }}>Get started with your voice AI agent</p>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>Full name (optional)</label>
              <input
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Jane Doe"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{ background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--text)" }}
                onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                onBlur={e => (e.target.style.borderColor = "var(--border)")}
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>Email address</label>
              <input
                type="email"
                required
                value={email}
                onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{ background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--text)" }}
                onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                onBlur={e => (e.target.style.borderColor = "var(--border)")}
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>Password</label>
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

            {error && (
              <div className="px-4 py-3 rounded-xl text-sm animate-fade-in"
                style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#f87171" }}>
                {error}
                {error.toLowerCase().includes("already exists") && (
                  <div className="mt-2">
                    <Link href="/signin" style={{ color: "var(--accent2)", textDecoration: "underline" }}>
                      Sign in to your account →
                    </Link>
                  </div>
                )}
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-3 rounded-xl font-semibold text-white transition-all flex items-center justify-center gap-2"
              style={{
                background: loading ? "var(--surface2)" : "linear-gradient(135deg, #7c6cfc, #a78bfa)",
                boxShadow: loading ? "none" : "0 0 20px rgba(124,108,252,0.3)",
              }}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {loading ? "Creating account…" : "Create account"}
            </button>
          </form>

          <p className="text-center mt-8 text-sm" style={{ color: "var(--muted)" }}>
            Already have an account?{" "}
            <Link href="/signin" className="font-medium" style={{ color: "var(--accent2)" }}>Sign in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
