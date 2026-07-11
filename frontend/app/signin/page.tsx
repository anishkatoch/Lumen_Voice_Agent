"use client";
import { useState, FormEvent, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API } from "@/lib/api";
import { setTokens, setUser } from "@/lib/auth";
import { Mic, Eye, EyeOff, Loader2, Zap, Brain, Lock, ShieldCheck, Sun, Moon } from "lucide-react";

const FEATURES = [
  { icon: Mic,         title: "Natural Voice",     desc: "Talk freely — no commands needed" },
  { icon: Brain,       title: "Remembers You",      desc: "Learns your preferences over time" },
  { icon: Zap,         title: "Instant Responses",  desc: "Sub-second AI replies out loud" },
  { icon: ShieldCheck, title: "Private by Design",  desc: "Your data stays yours" },
];

function getTheme(dark: boolean) {
  if (dark) return {
    page:        "#0a0a0f",
    leftBg:      "linear-gradient(145deg, #07071a 0%, #0e0b2a 50%, #130d35 100%)",
    leftBorder:  "none",
    gridImg:     "linear-gradient(rgba(124,108,252,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(124,108,252,0.06) 1px, transparent 1px)",
    gridSize:    "48px 48px",
    glowBg:      "radial-gradient(circle, rgba(124,108,252,0.15) 0%, transparent 70%)",
    logoBtn:     "rgba(255,255,255,0.08)",
    logoBtnBorder:"1px solid rgba(255,255,255,0.12)",
    logoText:    "#e2e2f0",
    heading:     "#e2e2f0",
    subtext:     "#6b6b8a",
    featureIcon: "#a78bfa",
    featureBg:   "rgba(124,108,252,0.12)",
    featureBorder:"rgba(124,108,252,0.2)",
    featureTitle:"#e2e2f0",
    featureDesc: "#6b6b8a",
    waveBg:      "rgba(124,108,252,0.5)",
    rightBg:     "#0a0a0f",
    label:       "#6b6b8a",
    title:       "#e2e2f0",
    subtitle:    "#6b6b8a",
    inputBg:     "#1a1a24",
    inputBorder: "#2a2a3a",
    inputFocus:  "#7c6cfc",
    inputText:   "#e2e2f0",
    inputShadow: "none",
    eyeBtn:      "#6b6b8a",
    errBg:       "rgba(239,68,68,0.08)",
    errBorder:   "rgba(239,68,68,0.25)",
    errText:     "#f87171",
    btnDisabled: "#1a1a24",
    trustBg:     "rgba(124,108,252,0.08)",
    trustBorder: "rgba(124,108,252,0.18)",
    trustText:   "#6b6b8a",
    trustStrong: "#e2e2f0",
    trustIcon:   "#a78bfa",
    footerText:  "#6b6b8a",
    footerLink:  "#a78bfa",
    toggleBg:    "rgba(255,255,255,0.06)",
    toggleBorder:"rgba(255,255,255,0.1)",
    toggleIcon:  "#a78bfa",
  };
  return {
    page:        "#ffffff",
    leftBg:      "#f0eeff",
    leftBorder:  "1px solid #e5e0ff",
    gridImg:     "radial-gradient(circle, rgba(124,108,252,0.18) 1px, transparent 1px)",
    gridSize:    "28px 28px",
    glowBg:      "radial-gradient(circle, rgba(124,108,252,0.1) 0%, transparent 70%)",
    logoBtn:     "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
    logoBtnBorder:"none",
    logoText:    "#3b2f8f",
    heading:     "#1e1650",
    subtext:     "#6b5fc2",
    featureIcon: "#7c6cfc",
    featureBg:   "rgba(124,108,252,0.10)",
    featureBorder:"rgba(124,108,252,0.2)",
    featureTitle:"#1e1650",
    featureDesc: "#7c6cfc",
    waveBg:      "rgba(124,108,252,0.35)",
    rightBg:     "#ffffff",
    label:       "#374151",
    title:       "#111827",
    subtitle:    "#6b7280",
    inputBg:     "#f9fafb",
    inputBorder: "#e5e7eb",
    inputFocus:  "#7c6cfc",
    inputText:   "#111827",
    inputShadow: "0 1px 2px rgba(0,0,0,0.04)",
    eyeBtn:      "#9ca3af",
    errBg:       "#fef2f2",
    errBorder:   "#fecaca",
    errText:     "#dc2626",
    btnDisabled: "#d1d5db",
    trustBg:     "#f5f3ff",
    trustBorder: "#ede9fe",
    trustText:   "#6b7280",
    trustStrong: "#374151",
    trustIcon:   "#7c6cfc",
    footerText:  "#6b7280",
    footerLink:  "#7c6cfc",
    toggleBg:    "#f3f0ff",
    toggleBorder:"#ddd6fe",
    toggleIcon:  "#7c6cfc",
  };
}

export default function SignIn() {
  const router = useRouter();
  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd]   = useState(false);
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState<string | null>(null);
  const [dark, setDark]         = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("lumen_theme");
    if (saved === "dark") setDark(true);
  }, []);

  function toggleDark() {
    const next = !dark;
    setDark(next);
    localStorage.setItem("lumen_theme", next ? "dark" : "light");
  }

  const t = getTheme(dark);

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
    <div className="min-h-screen flex" style={{ background: t.page }}>

      {/* ── Left branding panel ── */}
      <div className="hidden lg:flex flex-1 flex-col justify-between p-14 relative overflow-hidden"
        style={{ background: t.leftBg, borderRight: t.leftBorder }}>

        <div className="absolute inset-0" style={{ backgroundImage: t.gridImg, backgroundSize: t.gridSize }} />
        <div className="absolute -bottom-24 -right-24 w-96 h-96 rounded-full pointer-events-none"
          style={{ background: t.glowBg }} />

        {/* Logo */}
        <div className="relative z-10 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: t.logoBtn, border: t.logoBtnBorder, boxShadow: dark ? "none" : "0 4px 16px rgba(124,108,252,0.35)" }}>
            <Mic className="w-5 h-5" style={{ color: dark ? "#a78bfa" : "#fff" }} />
          </div>
          <span className="text-xl font-bold tracking-tight" style={{ color: t.logoText }}>Lumen</span>
        </div>

        {/* Headline */}
        <div className="relative z-10">
          <h2 className="text-5xl font-bold leading-tight mb-4" style={{ color: t.heading }}>
            Your AI voice<br />agent awaits.
          </h2>
          <p className="text-lg mb-12 leading-relaxed max-w-xs" style={{ color: t.subtext }}>
            Talk naturally. Get instant, intelligent answers spoken back to you in real time.
          </p>

          <div className="space-y-4">
            {FEATURES.map(({ icon: Icon, title, desc }) => (
              <div key={title} className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
                  style={{ background: t.featureBg, border: `1px solid ${t.featureBorder}` }}>
                  <Icon className="w-4 h-4" style={{ color: t.featureIcon }} />
                </div>
                <div>
                  <span className="text-sm font-semibold" style={{ color: t.featureTitle }}>{title}</span>
                  <span className="text-sm" style={{ color: t.featureDesc }}> — {desc}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Sound wave */}
        <div className="relative z-10 flex gap-1 items-end h-8">
          {[3,6,10,16,22,28,22,16,10,6,3,6,10,16,10,6,3].map((h, i) => (
            <div key={i} className="w-1 rounded-full wave-bar"
              style={{ height: `${h}px`, background: t.waveBg, animationDelay: `${i * 0.07}s` }} />
          ))}
        </div>
      </div>

      {/* ── Right form panel ── */}
      <div className="flex-1 flex items-center justify-center p-8 relative" style={{ background: t.rightBg }}>

        {/* Dark mode toggle */}
        <button onClick={toggleDark}
          className="absolute top-6 right-6 w-9 h-9 rounded-xl flex items-center justify-center transition-all"
          style={{ background: t.toggleBg, border: `1px solid ${t.toggleBorder}` }}
          title={dark ? "Switch to light mode" : "Switch to dark mode"}>
          {dark
            ? <Sun className="w-4 h-4" style={{ color: t.toggleIcon }} />
            : <Moon className="w-4 h-4" style={{ color: t.toggleIcon }} />}
        </button>

        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #7c6cfc, #9d8dfd)" }}>
              <Mic className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold" style={{ color: t.title }}>Lumen</span>
          </div>

          <h2 className="text-3xl font-bold mb-1" style={{ color: t.title }}>Welcome back</h2>
          <p className="mb-8 text-sm" style={{ color: t.subtitle }}>Sign in to continue to your voice agent</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: t.label }}>Email address</label>
              <input
                type="email" required value={email} onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{ background: t.inputBg, border: `1.5px solid ${t.inputBorder}`, color: t.inputText, boxShadow: t.inputShadow }}
                onFocus={e => (e.target.style.borderColor = t.inputFocus)}
                onBlur={e => (e.target.style.borderColor = t.inputBorder)}
              />
            </div>

            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-sm font-medium" style={{ color: t.label }}>Password</label>
                <Link href="/forgot-password" className="text-xs font-medium" style={{ color: t.footerLink }}>Forgot password?</Link>
              </div>
              <div className="relative">
                <input
                  type={showPwd ? "text" : "password"} required
                  value={password} onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full px-4 py-3 pr-12 rounded-xl outline-none transition-all text-sm"
                  style={{ background: t.inputBg, border: `1.5px solid ${t.inputBorder}`, color: t.inputText, boxShadow: t.inputShadow }}
                  onFocus={e => (e.target.style.borderColor = t.inputFocus)}
                  onBlur={e => (e.target.style.borderColor = t.inputBorder)}
                />
                <button type="button" onClick={() => setShowPwd(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 p-1" style={{ color: t.eyeBtn }}>
                  {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {error && (
              <div className="px-4 py-3 rounded-xl text-sm animate-fade-in"
                style={{ background: t.errBg, border: `1px solid ${t.errBorder}`, color: t.errText }}>
                {error}
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-3 rounded-xl font-semibold text-white transition-all flex items-center justify-center gap-2 mt-1"
              style={{
                background: loading ? t.btnDisabled : "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
                boxShadow: loading ? "none" : "0 4px 20px rgba(124,108,252,0.35)",
              }}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {loading ? "Signing in…" : "Sign in"}
            </button>
          </form>

          {/* Trust badge */}
          <div className="flex items-start gap-2.5 mt-5 px-3.5 py-3 rounded-xl"
            style={{ background: t.trustBg, border: `1px solid ${t.trustBorder}` }}>
            <Lock className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: t.trustIcon }} />
            <p className="text-xs leading-relaxed" style={{ color: t.trustText }}>
              Your password is <strong style={{ color: t.trustStrong }}>encrypted by Supabase</strong> — we never see or store it in plain text.
            </p>
          </div>

          <p className="text-center mt-6 text-sm" style={{ color: t.footerText }}>
            Don&apos;t have an account?{" "}
            <Link href="/signup" className="font-semibold" style={{ color: t.footerLink }}>Create one free</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
