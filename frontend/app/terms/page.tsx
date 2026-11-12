/* Terms of Service - linked from the Google OAuth consent screen. */

const S = {
  page: { maxWidth: 820, margin: "0 auto", padding: "56px 24px 80px", fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", color: "#1e2026", background: "#fffcf5", minHeight: "100vh" } as const,
  h1: { fontSize: 34, fontWeight: 800, marginBottom: 4 } as const,
  updated: { fontSize: 13, color: "#8a8578", marginBottom: 36 } as const,
  h2: { fontSize: 20, fontWeight: 700, marginTop: 36, marginBottom: 10 } as const,
  p: { fontSize: 15, lineHeight: 1.7, color: "#3a3c44", marginBottom: 12 } as const,
  li: { fontSize: 15, lineHeight: 1.7, color: "#3a3c44", marginBottom: 6 } as const,
};

export default function TermsPage() {
  return (
    <main style={S.page}>
      <h1 style={S.h1}>Terms of Service</h1>
      <div style={S.updated}>Lumen Voice Agents · Last updated: November 12, 2026</div>

      <p style={S.p}>
        These terms govern your use of Lumen Voice Agents (&quot;Lumen&quot;, the &quot;Service&quot;).
        By creating an account or using the Service you agree to them.
      </p>

      <h2 style={S.h2}>The service</h2>
      <p style={S.p}>
        Lumen lets you create AI voice agents that hold phone and browser voice conversations, answer
        questions from documents you provide, run interview and campaign calls, and book meetings on
        calendars you connect. AI-generated responses can be inaccurate; you are responsible for
        reviewing how your agents are configured and what they say on your behalf.
      </p>

      <h2 style={S.h2}>Your responsibilities</h2>
      <ul>
        <li style={S.li}>Only place calls that you are legally allowed to make, including complying with consent, recording, and telemarketing laws that apply in your and the recipient&apos;s jurisdiction.</li>
        <li style={S.li}>Do not use the Service for spam, harassment, deception (including impersonating a human where disclosure is required), or any unlawful purpose.</li>
        <li style={S.li}>Only upload documents and connect accounts (telephony, calendar) that you have the right to use.</li>
        <li style={S.li}>Keep your account credentials secure. Activity under your account is your responsibility.</li>
      </ul>

      <h2 style={S.h2}>Connected services</h2>
      <p style={S.p}>
        Telephony (Twilio, Plivo), Google Calendar, and AI providers are third-party services with
        their own terms and fees. You are responsible for your accounts with them, including carrier
        charges for calls your agents make.
      </p>

      <h2 style={S.h2}>Your content</h2>
      <p style={S.p}>
        You retain ownership of the prompts, documents, and data you bring to the Service. You grant us
        the right to process that content solely to operate the Service for you.
      </p>

      <h2 style={S.h2}>Availability and changes</h2>
      <p style={S.p}>
        The Service is provided on an &quot;as is&quot; and &quot;as available&quot; basis without
        warranties of any kind. We may modify or discontinue features, and we may suspend accounts that
        violate these terms.
      </p>

      <h2 style={S.h2}>Limitation of liability</h2>
      <p style={S.p}>
        To the maximum extent permitted by law, Lumen is not liable for indirect, incidental, or
        consequential damages, or for the actions, statements, or bookings made by agents you configure.
      </p>

      <h2 style={S.h2}>Contact</h2>
      <p style={S.p}>
        Questions about these terms: contact us at the support email listed on our website and app
        listing.
      </p>
    </main>
  );
}
