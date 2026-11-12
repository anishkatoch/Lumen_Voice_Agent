/* Privacy Policy - required by the Google OAuth consent screen and good
   practice for a platform handling call audio and calendar data. */

const S = {
  page: { maxWidth: 820, margin: "0 auto", padding: "56px 24px 80px", fontFamily: "system-ui, -apple-system, Segoe UI, sans-serif", color: "#1e2026", background: "#fffcf5", minHeight: "100vh" } as const,
  h1: { fontSize: 34, fontWeight: 800, marginBottom: 4 } as const,
  updated: { fontSize: 13, color: "#8a8578", marginBottom: 36 } as const,
  h2: { fontSize: 20, fontWeight: 700, marginTop: 36, marginBottom: 10 } as const,
  p: { fontSize: 15, lineHeight: 1.7, color: "#3a3c44", marginBottom: 12 } as const,
  li: { fontSize: 15, lineHeight: 1.7, color: "#3a3c44", marginBottom: 6 } as const,
};

export default function PrivacyPage() {
  return (
    <main style={S.page}>
      <h1 style={S.h1}>Privacy Policy</h1>
      <div style={S.updated}>Lumen Voice Agents · Last updated: November 12, 2026</div>

      <p style={S.p}>
        Lumen Voice Agents (&quot;Lumen&quot;, &quot;we&quot;, &quot;us&quot;) is a platform that lets you build and run
        AI voice agents that handle phone and browser voice conversations on your behalf. This policy
        explains what information we collect, how we use it, and the choices you have.
      </p>

      <h2 style={S.h2}>Information we collect</h2>
      <ul>
        <li style={S.li}><strong>Account information:</strong> your name, email address, and password (stored as a secure hash) when you create an account.</li>
        <li style={S.li}><strong>Agent configuration:</strong> the prompts, settings, knowledge base documents, and phone numbers you configure for your agents.</li>
        <li style={S.li}><strong>Call and session data:</strong> transcripts, call metadata (numbers, duration, status), and summaries of conversations your agents handle, so you can review them in your dashboard.</li>
        <li style={S.li}><strong>Telephony credentials:</strong> Twilio or Plivo credentials you connect, used only to place and receive calls you configure.</li>
      </ul>

      <h2 style={S.h2}>Google Calendar data</h2>
      <p style={S.p}>
        If you connect your Google Calendar, we request access to two scopes: free/busy lookups and
        calendar events. We use this access only to (1) check your availability when a caller asks to
        book a meeting with you, and (2) create the calendar event you asked the agent to book,
        including an optional invite to the caller. We do not read the contents of your other events,
        and we never use Google user data for advertising, profiling, or training machine learning models.
      </p>
      <p style={S.p}>
        OAuth tokens are stored encrypted at rest and are used only to serve your own agents. You can
        disconnect Google Calendar at any time from the Integrations page, which revokes and deletes the
        stored tokens. Lumen&apos;s use of information received from Google APIs adheres to the{" "}
        <a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data Policy</a>,
        including the Limited Use requirements.
      </p>

      <h2 style={S.h2}>How we use information</h2>
      <ul>
        <li style={S.li}>To operate your voice agents: transcribing speech, generating responses, speaking replies, and performing the actions you configured (such as booking meetings).</li>
        <li style={S.li}>To show you call history, analytics, and booked meetings in your dashboard.</li>
        <li style={S.li}>To secure the service and prevent abuse.</li>
      </ul>
      <p style={S.p}>We do not sell your data, and we do not share it with third parties for their own marketing.</p>

      <h2 style={S.h2}>Service providers</h2>
      <p style={S.p}>
        To provide the service, data is processed by infrastructure and AI providers acting on our
        behalf: database and authentication hosting, speech-to-text, language model, and text-to-speech
        providers, and the telephony carriers you connect (Twilio or Plivo). Each receives only what is
        needed to perform its function.
      </p>

      <h2 style={S.h2}>Retention and deletion</h2>
      <p style={S.p}>
        Your data is retained while your account is active. You can delete agents, documents,
        conversations, and calendar connections from the dashboard at any time. If you want your entire
        account and its data deleted, contact us and we will remove it.
      </p>

      <h2 style={S.h2}>Contact</h2>
      <p style={S.p}>
        For privacy questions or deletion requests, contact us at the support email listed on our
        website and app listing.
      </p>
    </main>
  );
}
