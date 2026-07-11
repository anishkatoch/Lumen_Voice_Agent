"use client";
import { useState, FormEvent, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { API } from "@/lib/api";
import { setTokens, setUser } from "@/lib/auth";
import { Mic, Eye, EyeOff, Loader2, CheckCircle, Lock, ShieldCheck, Zap, Brain, Sun, Moon } from "lucide-react";

function getTheme(dark: boolean) {
  if (dark) return {
    page:         "#0a0a0f",
    leftBg:       "linear-gradient(145deg, #07071a 0%, #0e0b2a 50%, #130d35 100%)",
    leftBorder:   "none",
    gridImg:      "linear-gradient(rgba(124,108,252,0.06) 1px, transparent 1px), linear-gradient(90deg, rgba(124,108,252,0.06) 1px, transparent 1px)",
    gridSize:     "48px 48px",
    glowBg:       "radial-gradient(circle, rgba(124,108,252,0.15) 0%, transparent 70%)",
    logoBtn:      "rgba(255,255,255,0.08)",
    logoBtnBorder:"1px solid rgba(255,255,255,0.12)",
    logoMicColor: "#a78bfa",
    logoText:     "#e2e2f0",
    heading:      "#e2e2f0",
    subtext:      "#6b6b8a",
    featureIcon:  "#a78bfa",
    featureBg:    "rgba(124,108,252,0.12)",
    featureBorder:"rgba(124,108,252,0.2)",
    featureTitle: "#e2e2f0",
    featureDesc:  "#6b6b8a",
    waveBg:       "rgba(124,108,252,0.5)",
    rightBg:      "#0a0a0f",
    label:        "#6b6b8a",
    title:        "#e2e2f0",
    subtitle:     "#6b6b8a",
    tabsBg:       "#1a1a24",
    tabActive:    "linear-gradient(135deg, #7c6cfc, #a78bfa)",
    tabActiveTxt: "#fff",
    tabInactiveTxt:"#6b6b8a",
    tabActiveShadow:"0 2px 8px rgba(124,108,252,0.3)",
    infoBg:       "rgba(124,108,252,0.08)",
    infoBorder:   "rgba(124,108,252,0.2)",
    infoText:     "#6b6b8a",
    inputBg:      "#1a1a24",
    inputBorder:  "#2a2a3a",
    inputFocus:   "#7c6cfc",
    inputText:    "#e2e2f0",
    inputShadow:  "none",
    eyeBtn:       "#6b6b8a",
    errBg:        "rgba(239,68,68,0.08)",
    errBorder:    "rgba(239,68,68,0.25)",
    errText:      "#f87171",
    errLink:      "#a78bfa",
    btnDisabled:  "#1a1a24",
    trustBg:      "rgba(124,108,252,0.08)",
    trustBorder:  "rgba(124,108,252,0.18)",
    trustText:    "#6b6b8a",
    trustStrong:  "#e2e2f0",
    trustIcon:    "#a78bfa",
    footerText:   "#6b6b8a",
    footerLink:   "#a78bfa",
    toggleBg:     "rgba(255,255,255,0.06)",
    toggleBorder: "rgba(255,255,255,0.1)",
    toggleIcon:   "#a78bfa",
    otpInputBg:   "#1a1a24",
    otpBorderFill:"#7c6cfc",
    otpBorderEmpty:"#2a2a3a",
    otpText:      "#e2e2f0",
    doneBg:       "#0a0a0f",
    doneTitle:    "#e2e2f0",
    doneSub:      "#6b6b8a",
    doneCheck:    "#22c55e",
  };
  return {
    page:         "#ffffff",
    leftBg:       "#f0eeff",
    leftBorder:   "1px solid #e5e0ff",
    gridImg:      "radial-gradient(circle, rgba(124,108,252,0.18) 1px, transparent 1px)",
    gridSize:     "28px 28px",
    glowBg:       "radial-gradient(circle, rgba(124,108,252,0.1) 0%, transparent 70%)",
    logoBtn:      "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
    logoBtnBorder:"none",
    logoMicColor: "#fff",
    logoText:     "#3b2f8f",
    heading:      "#1e1650",
    subtext:      "#6b5fc2",
    featureIcon:  "#7c6cfc",
    featureBg:    "rgba(124,108,252,0.10)",
    featureBorder:"rgba(124,108,252,0.2)",
    featureTitle: "#1e1650",
    featureDesc:  "#7c6cfc",
    waveBg:       "rgba(124,108,252,0.35)",
    rightBg:      "#ffffff",
    label:        "#374151",
    title:        "#111827",
    subtitle:     "#6b7280",
    tabsBg:       "#f0eeff",
    tabActive:    "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
    tabActiveTxt: "#fff",
    tabInactiveTxt:"#6b7280",
    tabActiveShadow:"0 2px 8px rgba(124,108,252,0.3)",
    infoBg:       "#f5f3ff",
    infoBorder:   "#ede9fe",
    infoText:     "#6b7280",
    inputBg:      "#f9fafb",
    inputBorder:  "#e5e7eb",
    inputFocus:   "#7c6cfc",
    inputText:    "#111827",
    inputShadow:  "0 1px 2px rgba(0,0,0,0.04)",
    eyeBtn:       "#9ca3af",
    errBg:        "#fef2f2",
    errBorder:    "#fecaca",
    errText:      "#dc2626",
    errLink:      "#7c6cfc",
    btnDisabled:  "#d1d5db",
    trustBg:      "#f5f3ff",
    trustBorder:  "#ede9fe",
    trustText:    "#6b7280",
    trustStrong:  "#374151",
    trustIcon:    "#7c6cfc",
    footerText:   "#6b7280",
    footerLink:   "#7c6cfc",
    toggleBg:     "#f3f0ff",
    toggleBorder: "#ddd6fe",
    toggleIcon:   "#7c6cfc",
    otpInputBg:   "#ffffff",
    otpBorderFill:"#7c6cfc",
    otpBorderEmpty:"#e5e7eb",
    otpText:      "#111827",
    doneBg:       "#ffffff",
    doneTitle:    "#111827",
    doneSub:      "#6b7280",
    doneCheck:    "#16a34a",
  };
}

export default function SignUp() {
  const router = useRouter();
  const [name, setName]           = useState("");
  const [email, setEmail]         = useState("");
  const [password, setPassword]   = useState("");
  const [showPwd, setShowPwd]     = useState(false);
  const [loading, setLoading]     = useState(false);
  const [error, setError]         = useState<string | null>(null);
  const [step, setStep]           = useState<"form" | "otp" | "done">("form");
  const [otp, setOtp]             = useState(["", "", "", "", "", ""]);
  const otpRefs                   = useRef<(HTMLInputElement | null)[]>([]);
  const [tab, setTab]             = useState<"account" | "guest">("account");
  const [guestName, setGuestName] = useState("");
  const [guestEmail, setGuestEmail] = useState("");
  const [dark, setDark]           = useState(false);

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
      const data = await API.signUp(email, password, name || undefined);
      if (data.message === "confirmation_required") {
        setStep("otp");
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

  async function handleOtpSubmit(e: FormEvent) {
    e.preventDefault();
    const token = otp.join("");
    if (token.length !== 6) { setError("Enter all 6 digits"); return; }
    setError(null);
    setLoading(true);
    try {
      const data = await API.verifySignupOtp(email, token);
      setTokens(data.access_token, data.refresh_token);
      setUser(data.user);
      setStep("done");
      setTimeout(() => router.replace("/agent"), 1500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Invalid OTP");
    } finally {
      setLoading(false);
    }
  }

  function handleOtpChange(i: number, val: string) {
    const digit = val.replace(/\D/g, "").slice(-1);
    const next = [...otp];
    next[i] = digit;
    setOtp(next);
    if (digit && i < 5) otpRefs.current[i + 1]?.focus();
  }

  function handleOtpKeyDown(i: number, e: React.KeyboardEvent) {
    if (e.key === "Backspace" && !otp[i] && i > 0) otpRefs.current[i - 1]?.focus();
  }

  function handleOtpPaste(e: React.ClipboardEvent) {
    e.preventDefault();
    const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6);
    if (pasted.length === 6) {
      setOtp(pasted.split(""));
      otpRefs.current[5]?.focus();
    }
  }

  async function handleGuestSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const data = await API.guestLogin(guestName, guestEmail);
      setTokens(data.access_token, data.refresh_token);
      setUser(data.user);
      router.replace("/agent");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Guest login failed");
    } finally {
      setLoading(false);
    }
  }

  // ── Toggle button (reused across steps) ────────────────────────────────
  const ThemeToggle = () => (
    <button onClick={toggleDark}
      className="absolute top-6 right-6 w-9 h-9 rounded-xl flex items-center justify-center transition-all"
      style={{ background: t.toggleBg, border: `1px solid ${t.toggleBorder}` }}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}>
      {dark
        ? <Sun className="w-4 h-4" style={{ color: t.toggleIcon }} />
        : <Moon className="w-4 h-4" style={{ color: t.toggleIcon }} />}
    </button>
  );

  // ── OTP step ────────────────────────────────────────────────────────────
  if (step === "otp") {
    return (
      <div className="min-h-screen flex items-center justify-center p-8 relative" style={{ background: t.page }}>
        <ThemeToggle />
        <div className="w-full max-w-md animate-fade-in">
          <div className="flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ background: "linear-gradient(135deg, #7c6cfc, #9d8dfd)", boxShadow: "0 4px 16px rgba(124,108,252,0.3)" }}>
              <Mic className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold" style={{ color: t.title }}>Lumen</span>
          </div>

          <h2 className="text-3xl font-bold mb-2" style={{ color: t.title }}>Verify your email</h2>
          <p className="mb-1 text-sm" style={{ color: t.subtitle }}>We sent a 6-digit code to</p>
          <p className="font-semibold mb-8" style={{ color: t.title }}>{email}</p>

          <form onSubmit={handleOtpSubmit} className="space-y-6">
            <div className="flex gap-3 justify-between" onPaste={handleOtpPaste}>
              {otp.map((digit, i) => (
                <input
                  key={i}
                  ref={el => { otpRefs.current[i] = el; }}
                  type="text" inputMode="numeric" maxLength={1}
                  value={digit}
                  onChange={e => handleOtpChange(i, e.target.value)}
                  onKeyDown={e => handleOtpKeyDown(i, e)}
                  className="w-12 h-14 text-center text-xl font-bold rounded-xl outline-none transition-all"
                  style={{
                    background: t.otpInputBg,
                    border: `1.5px solid ${digit ? t.otpBorderFill : t.otpBorderEmpty}`,
                    color: t.otpText,
                    boxShadow: t.inputShadow,
                  }}
                  onFocus={e => (e.target.style.borderColor = t.otpBorderFill)}
                  onBlur={e => (e.target.style.borderColor = digit ? t.otpBorderFill : t.otpBorderEmpty)}
                />
              ))}
            </div>

            {error && (
              <div className="px-4 py-3 rounded-xl text-sm"
                style={{ background: t.errBg, border: `1px solid ${t.errBorder}`, color: t.errText }}>
                {error}
              </div>
            )}

            <button type="submit" disabled={loading || otp.join("").length !== 6}
              className="w-full py-3 rounded-xl font-semibold text-white transition-all flex items-center justify-center gap-2"
              style={{
                background: (loading || otp.join("").length !== 6) ? t.btnDisabled : "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
                boxShadow: (loading || otp.join("").length !== 6) ? "none" : "0 4px 20px rgba(124,108,252,0.35)",
              }}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {loading ? "Verifying…" : "Verify & continue"}
            </button>

            <p className="text-center text-sm" style={{ color: t.footerText }}>
              Wrong email?{" "}
              <button type="button" onClick={() => { setStep("form"); setOtp(["","","","","",""]); setError(null); }}
                className="font-semibold" style={{ color: t.footerLink }}>
                Go back
              </button>
            </p>
          </form>
        </div>
      </div>
    );
  }

  // ── Done step ───────────────────────────────────────────────────────────
  if (step === "done") {
    return (
      <div className="min-h-screen flex items-center justify-center p-8" style={{ background: t.doneBg }}>
        <div className="text-center animate-fade-in">
          <CheckCircle className="w-16 h-16 mx-auto mb-4" style={{ color: t.doneCheck }} />
          <h2 className="text-2xl font-bold mb-2" style={{ color: t.doneTitle }}>Account created!</h2>
          <p style={{ color: t.doneSub }}>Taking you to the app…</p>
        </div>
      </div>
    );
  }

  // ── Main form ───────────────────────────────────────────────────────────
  return (
    <div className="min-h-screen flex" style={{ background: t.page }}>

      {/* ── Left branding panel ── */}
      <div className="hidden lg:flex flex-1 flex-col justify-between p-14 relative overflow-hidden"
        style={{ background: t.leftBg, borderRight: t.leftBorder }}>

        <div className="absolute inset-0" style={{ backgroundImage: t.gridImg, backgroundSize: t.gridSize }} />
        <div className="absolute -bottom-24 -left-24 w-96 h-96 rounded-full pointer-events-none"
          style={{ background: t.glowBg }} />

        {/* Logo */}
        <div className="relative z-10 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: t.logoBtn, border: t.logoBtnBorder, boxShadow: dark ? "none" : "0 4px 16px rgba(124,108,252,0.35)" }}>
            <Mic className="w-5 h-5" style={{ color: t.logoMicColor }} />
          </div>
          <span className="text-xl font-bold tracking-tight" style={{ color: t.logoText }}>Lumen</span>
        </div>

        {/* Headline */}
        <div className="relative z-10">
          <h2 className="text-5xl font-bold leading-tight mb-4" style={{ color: t.heading }}>
            Join thousands<br />already talking.
          </h2>
          <p className="text-lg mb-12 leading-relaxed max-w-xs" style={{ color: t.subtext }}>
            Set up your personal AI voice agent in under a minute. No configuration needed.
          </p>

          <div className="space-y-4">
            {[
              { icon: Zap,         title: "Live in 60 seconds",    desc: "No setup required" },
              { icon: Brain,       title: "Gets smarter with you", desc: "Remembers context across chats" },
              { icon: ShieldCheck, title: "Private by design",     desc: "Your data stays yours" },
            ].map(({ icon: Icon, title, desc }) => (
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

          <h2 className="text-3xl font-bold mb-2" style={{ color: t.title }}>Create account</h2>
          <p className="mb-6 text-sm" style={{ color: t.subtitle }}>Get started with your voice AI agent</p>

          {/* Tabs */}
          <div className="flex rounded-xl p-1 mb-6" style={{ background: t.tabsBg }}>
            {(["account", "guest"] as const).map(t2 => (
              <button key={t2} type="button" onClick={() => { setTab(t2); setError(null); }}
                className="flex-1 py-2 rounded-lg text-sm font-semibold transition-all"
                style={{
                  background: tab === t2 ? t.tabActive : "transparent",
                  color: tab === t2 ? t.tabActiveTxt : t.tabInactiveTxt,
                  boxShadow: tab === t2 ? t.tabActiveShadow : "none",
                }}>
                {t2 === "account" ? "Create Account" : "Guest"}
              </button>
            ))}
          </div>

          {/* Account form */}
          {tab === "account" && (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: t.label }}>Full name (optional)</label>
              <input
                type="text" value={name} onChange={e => setName(e.target.value)} placeholder="Jane Doe"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{ background: t.inputBg, border: `1.5px solid ${t.inputBorder}`, color: t.inputText, boxShadow: t.inputShadow }}
                onFocus={e => (e.target.style.borderColor = t.inputFocus)}
                onBlur={e => (e.target.style.borderColor = t.inputBorder)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: t.label }}>Email address</label>
              <input
                type="email" required value={email} onChange={e => setEmail(e.target.value)} placeholder="you@example.com"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{ background: t.inputBg, border: `1.5px solid ${t.inputBorder}`, color: t.inputText, boxShadow: t.inputShadow }}
                onFocus={e => (e.target.style.borderColor = t.inputFocus)}
                onBlur={e => (e.target.style.borderColor = t.inputBorder)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: t.label }}>Password</label>
              <div className="relative">
                <input
                  type={showPwd ? "text" : "password"} required minLength={6}
                  value={password} onChange={e => setPassword(e.target.value)} placeholder="Min. 6 characters"
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
                {error.toLowerCase().includes("already registered") && (
                  <div className="mt-2">
                    <Link href="/signin" style={{ color: t.errLink, textDecoration: "underline" }}>
                      Sign in to your account →
                    </Link>
                  </div>
                )}
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-3 rounded-xl font-semibold text-white transition-all flex items-center justify-center gap-2"
              style={{
                background: loading ? t.btnDisabled : "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
                boxShadow: loading ? "none" : "0 4px 20px rgba(124,108,252,0.35)",
              }}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {loading ? "Creating account…" : "Create account"}
            </button>
          </form>
          )}

          {/* Guest form */}
          {tab === "guest" && (
          <form onSubmit={handleGuestSubmit} className="space-y-4">
            <div className="px-4 py-3 rounded-xl text-sm"
              style={{ background: t.infoBg, border: `1px solid ${t.infoBorder}`, color: t.infoText }}>
              No password or verification needed. Just enter your name and email to start.
            </div>

            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: t.label }}>Your name</label>
              <input
                type="text" required value={guestName} onChange={e => setGuestName(e.target.value)} placeholder="Jane Doe"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{ background: t.inputBg, border: `1.5px solid ${t.inputBorder}`, color: t.inputText, boxShadow: t.inputShadow }}
                onFocus={e => (e.target.style.borderColor = t.inputFocus)}
                onBlur={e => (e.target.style.borderColor = t.inputBorder)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: t.label }}>Email address</label>
              <input
                type="email" required value={guestEmail} onChange={e => setGuestEmail(e.target.value)} placeholder="you@example.com"
                className="w-full px-4 py-3 rounded-xl outline-none transition-all text-sm"
                style={{ background: t.inputBg, border: `1.5px solid ${t.inputBorder}`, color: t.inputText, boxShadow: t.inputShadow }}
                onFocus={e => (e.target.style.borderColor = t.inputFocus)}
                onBlur={e => (e.target.style.borderColor = t.inputBorder)}
              />
            </div>

            {error && (
              <div className="px-4 py-3 rounded-xl text-sm animate-fade-in"
                style={{ background: t.errBg, border: `1px solid ${t.errBorder}`, color: t.errText }}>
                {error}
                {error.toLowerCase().includes("already registered") && (
                  <div className="mt-2">
                    <Link href="/signin" style={{ color: t.errLink, textDecoration: "underline" }}>
                      Sign in to your account →
                    </Link>
                  </div>
                )}
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-3 rounded-xl font-semibold text-white transition-all flex items-center justify-center gap-2"
              style={{
                background: loading ? t.btnDisabled : "linear-gradient(135deg, #7c6cfc, #9d8dfd)",
                boxShadow: loading ? "none" : "0 4px 20px rgba(124,108,252,0.35)",
              }}>
              {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              {loading ? "Entering…" : "Continue as Guest"}
            </button>
          </form>
          )}

          {/* Trust badge */}
          <div className="flex items-start gap-2.5 mt-5 px-3.5 py-3 rounded-xl"
            style={{ background: t.trustBg, border: `1px solid ${t.trustBorder}` }}>
            <Lock className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: t.trustIcon }} />
            <p className="text-xs leading-relaxed" style={{ color: t.trustText }}>
              Your password is <strong style={{ color: t.trustStrong }}>encrypted by Supabase</strong> — we never see or store it in plain text.
            </p>
          </div>

          <p className="text-center mt-6 text-sm" style={{ color: t.footerText }}>
            Already have an account?{" "}
            <Link href="/signin" className="font-semibold" style={{ color: t.footerLink }}>Sign in</Link>
          </p>
        </div>
      </div>
    </div>
  );
}
