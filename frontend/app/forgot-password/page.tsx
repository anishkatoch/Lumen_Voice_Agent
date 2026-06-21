"use client";
import { useState, FormEvent, useRef, KeyboardEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { API } from "@/lib/api";
import { Mic, Loader2, CheckCircle, ArrowLeft, Eye, EyeOff } from "lucide-react";

type Step = "email" | "otp" | "done";

export default function ForgotPassword() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("email");
  const [email, setEmail] = useState("");
  const [otp, setOtp] = useState(["", "", "", "", "", ""]);
  const [password, setPassword] = useState("");
  const [showPwd, setShowPwd] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const otpRefs = useRef<(HTMLInputElement | null)[]>([]);

  // ── Step 1: send OTP ─────────────────────────────────────────────────
  async function handleSendOtp(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await API.forgotPassword(email);
      setStep("otp");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  // ── OTP input handling ───────────────────────────────────────────────
  function handleOtpChange(index: number, value: string) {
    if (!/^\d*$/.test(value)) return;
    const next = [...otp];
    next[index] = value.slice(-1);
    setOtp(next);
    if (value && index < 5) otpRefs.current[index + 1]?.focus();
  }

  function handleOtpKeyDown(index: number, e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Backspace" && !otp[index] && index > 0) {
      otpRefs.current[index - 1]?.focus();
    }
  }

  function handleOtpPaste(e: React.ClipboardEvent) {
    const pasted = e.clipboardData.getData("text").replace(/\D/g, "").slice(0, 6);
    if (pasted.length === 6) {
      setOtp(pasted.split(""));
      otpRefs.current[5]?.focus();
    }
    e.preventDefault();
  }

  // ── Step 2: verify OTP + set new password ───────────────────────────
  async function handleVerify(e: FormEvent) {
    e.preventDefault();
    const code = otp.join("");
    if (code.length < 6) { setError("Enter all 6 digits"); return; }
    if (password.length < 6) { setError("Password must be at least 6 characters"); return; }
    setError(null);
    setLoading(true);
    try {
      await API.verifyOtp(email, code, password);
      setStep("done");
      setTimeout(() => router.replace("/signin"), 2500);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Invalid OTP or password update failed");
    } finally {
      setLoading(false);
    }
  }

  const inputClass = "w-full px-4 py-3 rounded-xl outline-none transition-all text-sm";
  const inputStyle = { background: "var(--surface2)", border: "1px solid var(--border)", color: "var(--text)" };

  return (
    <div className="min-h-screen flex items-center justify-center p-8" style={{ background: "var(--bg)" }}>
      <div className="w-full max-w-md">

        {/* Logo */}
        <div className="flex items-center gap-3 mb-10">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center"
            style={{ background: "linear-gradient(135deg, #7c6cfc, #a78bfa)" }}>
            <Mic className="w-5 h-5 text-white" />
          </div>
          <span className="text-xl font-bold" style={{ color: "var(--text)" }}>Lumen</span>
        </div>

        {/* ── Step: email ── */}
        {step === "email" && (
          <div className="animate-fade-in">
            <h2 className="text-3xl font-bold mb-2" style={{ color: "var(--text)" }}>Forgot password?</h2>
            <p className="mb-8" style={{ color: "var(--muted)" }}>
              Enter your email and we&apos;ll send you a 6-digit code.
            </p>
            <form onSubmit={handleSendOtp} className="space-y-5">
              <div>
                <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>Email address</label>
                <input
                  type="email" required value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@example.com"
                  className={inputClass} style={inputStyle}
                  onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                  onBlur={e => (e.target.style.borderColor = "var(--border)")}
                />
              </div>
              {error && <ErrorBox>{error}</ErrorBox>}
              <SubmitBtn loading={loading}>{loading ? "Sending…" : "Send code"}</SubmitBtn>
            </form>
            <BackLink />
          </div>
        )}

        {/* ── Step: otp + new password ── */}
        {step === "otp" && (
          <div className="animate-fade-in">
            <h2 className="text-3xl font-bold mb-2" style={{ color: "var(--text)" }}>Check your email</h2>
            <p className="mb-1" style={{ color: "var(--muted)" }}>
              We sent a 6-digit code to
            </p>
            <p className="font-semibold mb-8" style={{ color: "var(--text)" }}>{email}</p>

            <form onSubmit={handleVerify} className="space-y-6">
              {/* OTP boxes */}
              <div>
                <label className="block text-sm font-medium mb-3" style={{ color: "var(--muted)" }}>Enter code</label>
                <div className="flex gap-2 justify-between" onPaste={handleOtpPaste}>
                  {otp.map((digit, i) => (
                    <input
                      key={i}
                      ref={el => { otpRefs.current[i] = el; }}
                      type="text"
                      inputMode="numeric"
                      maxLength={1}
                      value={digit}
                      onChange={e => handleOtpChange(i, e.target.value)}
                      onKeyDown={e => handleOtpKeyDown(i, e)}
                      className="w-12 h-14 text-center text-xl font-bold rounded-xl outline-none transition-all"
                      style={{
                        background: "var(--surface2)",
                        border: `1px solid ${digit ? "var(--accent)" : "var(--border)"}`,
                        color: "var(--text)",
                      }}
                      onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                      onBlur={e => (e.target.style.borderColor = digit ? "var(--accent)" : "var(--border)")}
                    />
                  ))}
                </div>
              </div>

              {/* New password */}
              <div>
                <label className="block text-sm font-medium mb-2" style={{ color: "var(--muted)" }}>New password</label>
                <div className="relative">
                  <input
                    type={showPwd ? "text" : "password"}
                    required minLength={6}
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    placeholder="Min. 6 characters"
                    className={`${inputClass} pr-12`} style={inputStyle}
                    onFocus={e => (e.target.style.borderColor = "var(--accent)")}
                    onBlur={e => (e.target.style.borderColor = "var(--border)")}
                  />
                  <button type="button" onClick={() => setShowPwd(v => !v)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-1" style={{ color: "var(--muted)" }}>
                    {showPwd ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                  </button>
                </div>
              </div>

              {error && <ErrorBox>{error}</ErrorBox>}
              <SubmitBtn loading={loading}>{loading ? "Verifying…" : "Reset password"}</SubmitBtn>
            </form>

            <div className="mt-4 text-center text-sm" style={{ color: "var(--muted)" }}>
              Didn&apos;t receive it?{" "}
              <button onClick={() => { setStep("email"); setOtp(["","","","","",""]); setError(null); }}
                style={{ color: "var(--accent2)" }}>
                Resend code
              </button>
            </div>
            <BackLink />
          </div>
        )}

        {/* ── Step: done ── */}
        {step === "done" && (
          <div className="text-center animate-fade-in">
            <CheckCircle className="w-16 h-16 mx-auto mb-4" style={{ color: "var(--accent)" }} />
            <h2 className="text-2xl font-bold mb-2" style={{ color: "var(--text)" }}>Password updated!</h2>
            <p style={{ color: "var(--muted)" }}>Redirecting you to sign in…</p>
          </div>
        )}
      </div>
    </div>
  );
}

function ErrorBox({ children }: { children: string }) {
  return (
    <div className="px-4 py-3 rounded-xl text-sm animate-fade-in"
      style={{ background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", color: "#f87171" }}>
      {children}
    </div>
  );
}

function SubmitBtn({ loading, children }: { loading: boolean; children: React.ReactNode }) {
  return (
    <button type="submit" disabled={loading}
      className="w-full py-3 rounded-xl font-semibold text-white transition-all flex items-center justify-center gap-2"
      style={{
        background: loading ? "var(--surface2)" : "linear-gradient(135deg, #7c6cfc, #a78bfa)",
        boxShadow: loading ? "none" : "0 0 20px rgba(124,108,252,0.3)",
      }}>
      {loading && <Loader2 className="w-4 h-4 animate-spin" />}
      {children}
    </button>
  );
}

function BackLink() {
  return (
    <div className="mt-8 text-center">
      <Link href="/signin" className="inline-flex items-center gap-2 text-sm" style={{ color: "var(--muted)" }}>
        <ArrowLeft className="w-3.5 h-3.5" /> Back to sign in
      </Link>
    </div>
  );
}
