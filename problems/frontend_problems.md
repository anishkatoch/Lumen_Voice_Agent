# Frontend Problems & Fixes

---

## 1. `localStorage is not defined` During Build

**Problem:**
`npm run build` failed with `ReferenceError: localStorage is not defined` on the `/agent` page.

**Root Cause:**
Next.js with `output: "export"` pre-renders all pages on the server at build time (SSR/SSG). The agent page called `getUser()` at the top level of the component, which reads from `localStorage`. `localStorage` does not exist in the Node.js server environment — only in the browser.

**Fix:**
Moved `getUser()` call inside a `useEffect` so it only runs client-side after hydration. Changed `const user = getUser()` at component level to `const [user, setUserState] = useState(null)` with `setUserState(getUser())` inside `useEffect`.

---

## 2. "Failed to Fetch" — CORS Origin Mismatch

**Problem:**
The signup and signin forms showed "Failed to fetch" when submitting. The browser console showed CORS errors.

**Root Cause:**
The browser sends an `Origin` header matching the URL in the address bar. When accessing via `http://localhost:8003`, the origin is `http://localhost:8003`. When accessing via `http://0.0.0.0:8003`, the origin is `http://0.0.0.0:8003`. Only `http://localhost:3000` was in the CORS whitelist.

**Fix:**
Added `http://localhost:8003` and `http://0.0.0.0:8003` to `cors.allowed_origins` in `config.yaml`. Always use `http://localhost:8003` in the browser — not `http://0.0.0.0:8003` — since `getUserMedia` (microphone) also requires a secure or localhost origin.

---

## 3. `getUserMedia` Not Available

**Problem:**
Clicking the mic button threw "Cannot read properties of undefined (reading 'getUserMedia')".

**Root Cause:**
`navigator.mediaDevices.getUserMedia` is only available on secure origins: `https://` or `http://localhost`. Accessing the app via `http://0.0.0.0:8003` is treated as an insecure non-localhost origin by the browser, so `mediaDevices` is undefined.

**Fix:**
Always access the app via `http://localhost:8003` in the browser, never via the `0.0.0.0` address.

---

## 4. AI Audio Glitchy / Network-Like Crackling

**Problem:**
The AI voice sounded choppy, stuttery, and had crackling artifacts — like a bad network connection.

**Root Cause:**
The initial implementation decoded and played each incoming MP3 chunk individually as it arrived from the WebSocket. MP3 is a streaming format — individual small chunks (~1-2KB) don't contain enough header/frame information to decode cleanly in isolation. The browser's `decodeAudioData` was trying to decode incomplete MP3 frames, resulting in gaps and artifacts between each chunk.

**Fix:**
Switched to a collect-then-play approach: all MP3 chunks are buffered silently as they arrive. When the backend sends `audio_end` (all chunks sent), the frontend merges all chunks into one complete MP3 buffer and decodes + plays it in a single `decodeAudioData` call. This produces perfectly clean audio with no gaps or artifacts.

---

## 5. Barge-In Frontend Not Stopping Audio

**Problem:**
When the backend sent `stop_audio` during barge-in, the audio didn't actually stop. The AI continued speaking its full previous answer.

**Root Cause (initial attempt):**
The first streaming implementation tried to schedule each MP3 chunk as a separate `AudioBufferSourceNode` on the Web Audio API timeline. When `stop_audio` arrived, calling `.stop()` on the active sources sometimes didn't work cleanly because the sources were queued for future playback times and the timing coordination was broken.

**Root Cause (after collect-then-play fix):**
After switching to collect-then-play, `stopAudio()` correctly called `activeSourceRef.current.stop()`. However, the backend was already back in LISTENING state before the frontend even started playing (see Backend Problem #8). So `stop_audio` was never sent in the first place — the backend thought speaking was over.

**Fix:**
Frontend now sends two new messages to the backend:
- `playback_started` — sent when `src.start()` is called, so the backend stays in SPEAKING state
- `playback_ended` — sent in `src.onended`, so the backend switches to LISTENING only after audio actually finishes

This keeps the backend's SPEAKING state in sync with actual browser playback time, enabling true barge-in detection.

---

## 6. Forgot Password Redirect to Wrong URL

**Problem:**
Clicking the password reset link in the email redirected to `https://lumen-agent-phi.vercel.app/auth` instead of the local app.

**Root Cause:**
Supabase uses the project's configured "Site URL" for all auth redirect emails. It was set to the production Vercel URL, so all reset links pointed there even during local development.

**Fix:**
Replaced the link-based reset flow entirely with an OTP flow. The `/forgot-password` page now has a 3-step flow:
1. Enter email → backend sends 6-digit OTP via Supabase
2. Enter the 6-digit code (with individual digit boxes, auto-advance, paste support) + new password
3. Backend verifies OTP and updates password in one round trip

No redirect links, no Supabase dashboard configuration needed, works on any environment.

---

## 7. Signup Confirmation Email Never Clicked — Account Stuck

**Problem:**
User created an account, never clicked the confirmation email, and then couldn't sign in. Subsequent signup attempts with the same email showed confusing errors.

**Root Cause:**
Supabase requires email confirmation by default. Until confirmed, the account exists but `identities` array is empty and sign-in is rejected. The frontend had no way to detect this state.

**Fix (short term):**
Used the admin API (service role key) to force-confirm the email and reset the password directly, bypassing the email confirmation requirement. This is only possible because we own the Supabase project.

**Fix (proper):**
The signup page already handles `confirmation_required` from the backend and shows a "Check your email" screen. Users should click the confirmation email before attempting to sign in.

---

## 8. Auth Callback Page for Password Reset Links

**Problem:**
There was no `/auth` route to handle Supabase's password reset redirect. Clicking the link resulted in a 404 or redirect to Vercel.

**Root Cause:**
Supabase puts the recovery token in the URL hash (`#access_token=...&type=recovery`) and redirects to the configured Site URL + `/auth`. There was no page to handle this.

**Fix:**
Created `/app/auth/page.tsx` that:
- Reads the URL hash on mount
- Detects `type=recovery` (password reset) vs `type=signup` (email confirmation)
- For recovery: shows a "Set new password" form that calls Supabase directly with the recovery token
- For signup confirmation: stores the tokens, fetches user info, redirects to `/agent`
- Also added FastAPI route to serve `auth/index.html` from the static export

Note: This page is now superseded by the OTP flow for password resets, but remains useful for email confirmation links.

---

## 9. History Panel + Voice Session Z-Index Bugs

**Problem 1:** After closing the history sidebar (X button), it could not be reopened — the ChevronRight button in the header was unreachable.
**Problem 2:** Clicking a past conversation hid the live voice session controls including the mic.

**Root Cause:**
`MessagesPanel` rendered as `absolute inset-0 z-50` — a full-screen overlay. This covered both the header (hiding the ChevronRight reopen button) and the bottom mic controls.

**Fix:**
Removed the overlay approach entirely. Introduced a `flex-1 min-h-0 relative` middle area with `absolute inset-0` inside — switches between history view and live chat within the same fixed-height slot. Header always stays above, mic + session button always anchored below. Neither can ever be covered.
**Files:** `frontend/app/agent/page.tsx`

---

## 10. Performance — PCM Audio (No MP3 Codec Overhead)

**Problem:**
ElevenLabs was returning MP3 audio (`mp3_44100_128`). The browser had to decode MP3 → PCM via `decodeAudioData` on every sentence, adding latency and codec processing overhead.

**Fix:**
Switched ElevenLabs output to `pcm_16000` (raw 16-bit PCM, 16kHz mono). Frontend now constructs an `AudioBuffer` directly from the raw samples — no codec, no `decodeAudioData`, instant playback.
```ts
const pcm = new Int16Array(merged.buffer);
const audioBuffer = ctx.createBuffer(1, pcm.length, 16000);
const channelData = audioBuffer.getChannelData(0);
for (let i = 0; i < pcm.length; i++) channelData[i] = pcm[i] / 32768.0;
```
**Files:** `frontend/hooks/useVoiceAgent.ts`, `config.yaml`

---

## 11. Two-Tab Layout (Candidates / Scheduled Calls) Required Double Navigation

**Problem:**
The HR section had two separate tabs — "Candidates" and "Scheduled Calls". To schedule a call for a candidate, HR had to:
1. Open the Candidates tab, find the candidate
2. Switch to Scheduled Calls tab
3. Pick the same candidate from a dropdown again
4. Fill in all the scheduling fields

This was repetitive and confusing — the same candidate data had to be referenced in two different places.

**Root Cause:**
The UI was designed with calls as a separate concern from candidates, leading to a two-tab flow that required context switching and duplicate candidate selection.

**Fix:**
Removed `hrTab` state and the tab toggle buttons entirely. Each candidate card now shows its scheduled calls inline directly beneath it. To view calls for a candidate, HR simply looks at the candidate's own card — no tab switching needed. The "Schedule Call" button is also on each candidate card and pre-selects that candidate automatically.
**Files:** `frontend/app/agents/page.tsx`

---

## 12. Add Candidate Modal Missing Interview Scheduling Fields

**Problem:**
When adding a new candidate, there was no way to schedule the interview call at the same time. HR had to:
1. Fill and submit the Add Candidate form
2. Open a second "Schedule Call" modal
3. Re-select the same candidate from a dropdown
4. Fill in the interview date/time, lead minutes, and questions

The two-modal flow was tedious and the candidate had to be found and selected twice.

**Root Cause:**
The `CandidateForm` state only included `name`, `phone`, `email`, `role`, `notes`. Interview scheduling was entirely in a separate `IntForm` with its own modal and its own submit flow.

**Fix:**
Extended `cForm` to include `scheduled_at`, `call_lead_minutes`, and `specific_questions` fields. The Add Candidate modal now has an optional "Schedule Interview Call" section at the bottom. When `scheduled_at` is filled:
- `saveCand()` creates the candidate first
- Then immediately calls `API.createInterview()` with the candidate's new ID
- The interview appears under the candidate's card instantly

If `scheduled_at` is left blank, only the candidate is created — no interview scheduled. Fully optional, zero extra steps for the common case.
**Files:** `frontend/app/agents/page.tsx`

---

## 13. `NEXT_PUBLIC_API_URL` Baked in as `localhost:8004` — Deployed Frontend Couldn't Reach the Backend

**Problem:**
After deploying frontend + backend together in one Docker container, every API call from the deployed site would have tried to reach `http://localhost:8004` — the *visitor's own machine*, not the server — since `NEXT_PUBLIC_*` env vars are baked into the static bundle at build time, and none were set during the Docker build.

**Root Cause:**
`lib/api.ts` defaults to `process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8004"`. Since frontend and backend are served from the same origin in this deployment, the correct value is empty/relative — but the Docker build never set this env var at all, so every request would have silently gone nowhere.

**Fix:**
Set `NEXT_PUBLIC_API_URL=""` (relative path) as a Docker `ARG`/`ENV` during the frontend build stage, so `fetch()` calls resolve against the current origin instead of localhost.
**Files:** `Dockerfile`

---

## 14. Supabase Env Vars Missing at Docker Build Time — Auth Callback Would Break

**Problem:**
`NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` are read directly in `app/auth/page.tsx` for the post-magic-link callback, but `.env.local` (where they're normally set) is gitignored — meaning a fresh clone/deploy would never have them, silently baking in empty strings and breaking sign-in completion.

**Root Cause:**
Next.js only bakes `NEXT_PUBLIC_*` vars into the static bundle from whatever's present at `next build` time. Since `.env.local` never reaches the Docker build context, these were undefined there.

**Fix:**
Passed them as Docker `ARG`s with the actual project values baked in as defaults. This is safe specifically because these are *not secrets* — the Supabase anon key and project URL are public identifiers, protected by Supabase's row-level security, not by being hidden.
**Files:** `Dockerfile`

---

## 15. Bloated Docker Build Context — `node_modules` Not Excluded

**Problem:**
Docker builds were slow and occasionally timed out, uploading far more data than necessary.

**Root Cause:**
`.dockerignore` didn't exclude `frontend/node_modules/`, `frontend/.next/`, or `frontend/out/` — meaning the entire local `node_modules` tree (reinstalled fresh inside the container anyway via `npm install`) was being sent to the Docker daemon on every build.

**Fix:**
Added `frontend/node_modules/`, `frontend/.next/`, and `frontend/out/` to `.dockerignore`.
**Files:** `.dockerignore`

---

## 16. Zero Test Infrastructure — No Way to Catch Regressions

**Problem:**
The frontend had no test framework, no test files, and no `npm test` script at all — any regression in `lib/api.ts`'s auth/refresh logic or `useVoiceAgent`'s WebSocket state machine could only be caught by manually clicking through the app.

**Root Cause:**
Never set up.

**Fix:**
Added Vitest + jsdom + React Testing Library (versions chosen for Node 18 compatibility — Vitest 4.x/latest `@testing-library/react` require Node 20+). Wrote:
- `lib/api.test.ts` — auth header injection, error-message extraction from non-ok responses, and the 401-refresh-retry flow (refresh succeeds → retries with new token and persists it; refresh fails or no refresh token stored → session-expired error with no infinite retry loop).
- `hooks/useVoiceAgent.test.ts` — connection lifecycle against a mock `WebSocket` (no real server needed): no-token guard, `ready`/`transcript`/`reply` message → state transitions, expired-session close code handling, `disconnect()` cleanup.

Run via `npm test` in `frontend/`.
**Files:** `frontend/vitest.config.ts`, `frontend/lib/api.test.ts`, `frontend/hooks/useVoiceAgent.test.ts`, `frontend/package.json`

---

## 17. Admin Panel Went Permanently Blank for Anyone With a Stale Token

**Problem:**
The admin panel stayed blank forever for a user with a stale auth token — no error, no redirect, just a blank page.

**Root Cause:**
`checkDone` was reset to `false` inside the `finally()` block that ran after a successful auth check, instead of only while the check was still pending — so the component looped back into its own loading state right after validating the token, and never rendered.

**Fix:**
Fixed alongside a rebuild of the admin panel onto the shared Lumen design system (`.lumenc` rail/card/button components, lucide icons, responsive rail/tabbar) replacing the old hand-rolled layout with no responsive breakpoints.
**Files:** `frontend/app/admin/page.tsx`

---

## 18. Long-Form Text Fields Unreadable — Too Wide or Too Short

**Problem:**
Long-form fields (the agent system prompt, HR instructions) ran past a readable line length on wide screens, and their textareas defaulted to a cramped ~120px box, far too short for multi-paragraph text.

**Fix:**
Capped textarea/input/view widths in `globals.css` to a readable measure; bumped system-prompt/HR-instructions textareas (`rows=6/8`) to a 280px min-height via an attribute selector, leaving smaller textareas (filler phrases, function descriptions) unaffected.
**Files:** `frontend/app/globals.css`

---

## 19. Call-Detail Page Layout Collapsed on Sparse Sessions

**Problem:**
The call-detail page's Conversation card sized to its own content, so a session with few or no messages left a large empty area below the cards instead of filling the viewport.

**Fix:**
Gave the session layout a min-height and let the last card in the main column flex to fill it.
**Files:** `frontend/app/globals.css`
