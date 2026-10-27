"use client";
import { useState } from "react";
import { signInWithProvider, OAuthProvider } from "@/lib/auth";

interface SocialAuthButtonsProps {
  inputBg: string;
  inputBorder: string;
  textColor: string;
  mutedColor: string;
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M23.52 12.27c0-.85-.08-1.67-.22-2.45H12v4.64h6.48c-.28 1.5-1.13 2.77-2.42 3.62v3.01h3.91c2.28-2.1 3.55-5.2 3.55-8.82z" />
      <path fill="#34A853" d="M12 24c3.24 0 5.96-1.07 7.95-2.9l-3.91-3.01c-1.08.73-2.47 1.16-4.04 1.16-3.1 0-5.73-2.09-6.67-4.9H1.26v3.1C3.24 21.3 7.26 24 12 24z" />
      <path fill="#FBBC05" d="M5.33 14.35A7.19 7.19 0 0 1 4.9 12c0-.82.15-1.62.43-2.35v-3.1H1.26A11.99 11.99 0 0 0 0 12c0 1.93.46 3.76 1.26 5.45z" />
      <path fill="#EA4335" d="M12 4.75c1.77 0 3.35.61 4.6 1.8l3.45-3.45C17.95 1.19 15.24 0 12 0 7.26 0 3.24 2.7 1.26 6.55l4.07 3.1c.94-2.81 3.57-4.9 6.67-4.9z" />
    </svg>
  );
}

function MicrosoftIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 23 23" aria-hidden="true">
      <rect x="1" y="1" width="10" height="10" fill="#F25022" />
      <rect x="12" y="1" width="10" height="10" fill="#7FBA00" />
      <rect x="1" y="12" width="10" height="10" fill="#00A4EF" />
      <rect x="12" y="12" width="10" height="10" fill="#FFB900" />
    </svg>
  );
}

function GitHubIcon({ color }: { color: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill={color} aria-hidden="true">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.3 3.44 9.8 8.21 11.39.6.11.82-.26.82-.58 0-.29-.01-1.04-.02-2.04-3.34.72-4.04-1.61-4.04-1.61-.55-1.39-1.33-1.76-1.33-1.76-1.09-.74.08-.73.08-.73 1.2.08 1.84 1.24 1.84 1.24 1.07 1.83 2.81 1.3 3.49.99.11-.77.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6.02 0c2.29-1.55 3.3-1.23 3.3-1.23.66 1.66.24 2.88.12 3.18.77.84 1.24 1.91 1.24 3.22 0 4.61-2.81 5.63-5.49 5.93.43.37.81 1.1.81 2.22 0 1.6-.02 2.89-.02 3.28 0 .32.22.7.82.58A12.01 12.01 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
    </svg>
  );
}

const PROVIDERS: { id: OAuthProvider; label: string; icon: (color: string) => JSX.Element }[] = [
  { id: "google", label: "Google", icon: () => <GoogleIcon /> },
  { id: "azure", label: "Microsoft", icon: () => <MicrosoftIcon /> },
  { id: "github", label: "GitHub", icon: (color: string) => <GitHubIcon color={color} /> },
];

export default function SocialAuthButtons({ inputBg, inputBorder, textColor, mutedColor }: SocialAuthButtonsProps) {
  const [pending, setPending] = useState<OAuthProvider | null>(null);
  const [error, setError] = useState<string | null>(null);

  function handleClick(provider: OAuthProvider) {
    setError(null);
    setPending(provider);
    try {
      signInWithProvider(provider);
    } catch (err: unknown) {
      setPending(null);
      setError(err instanceof Error ? err.message : "Social sign-in failed");
    }
  }

  return (
    <div className="space-y-3 mb-5">
      <div className="grid grid-cols-3 gap-2">
        {PROVIDERS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            disabled={pending !== null}
            onClick={() => handleClick(id)}
            title={`Continue with ${label}`}
            aria-label={`Continue with ${label}`}
            className="flex items-center justify-center gap-2 py-2.5 rounded-xl text-sm font-medium transition-all hover:opacity-80 disabled:opacity-50"
            style={{ background: inputBg, border: `1.5px solid ${inputBorder}`, color: textColor }}
          >
            {Icon(textColor)}
          </button>
        ))}
      </div>

      {error && <p className="text-xs" style={{ color: "#dc2626" }}>{error}</p>}

      <div className="flex items-center gap-3">
        <div className="flex-1 h-px" style={{ background: inputBorder }} />
        <span className="text-xs whitespace-nowrap" style={{ color: mutedColor }}>or continue with email</span>
        <div className="flex-1 h-px" style={{ background: inputBorder }} />
      </div>
    </div>
  );
}
