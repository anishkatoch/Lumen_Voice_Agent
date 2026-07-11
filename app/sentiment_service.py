"""
Sentiment analysis via HuggingFace Inference API.
Model: j-hartmann/emotion-english-distilroberta-base
  - Trained on MELD (TV dialogues) + IEMOCAP (recorded conversations) +
    EmotionLines + ISEAR — specifically dialogue/call-like data.
  - Returns 7 emotions mapped to POSITIVE / NEUTRAL / NEGATIVE.

Concurrency: asyncio.Semaphore limits simultaneous HF API calls so
5-6 sessions ending at the same time never trigger rate limits.
"""
import asyncio
import httpx
from app.config import (
    HUGGINGFACE_API_KEY, SENTIMENT_MODEL,
    SENTIMENT_TIMEOUT, SENTIMENT_MAX_RETRIES,
    SENTIMENT_MAX_CONCURRENT, SENTIMENT_MAX_CHARS,
)

_HF_URL = f"https://api-inference.huggingface.co/models/{SENTIMENT_MODEL}"

# Shared semaphore — created lazily so it's bound to the running event loop
_semaphore: asyncio.Semaphore | None = None

def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(SENTIMENT_MAX_CONCURRENT)
    return _semaphore

# Map model's 7 emotions → 3-bucket sentiment
_EMOTION_SENTIMENT: dict[str, str] = {
    "joy":      "POSITIVE",
    "neutral":  "NEUTRAL",
    "surprise": "NEUTRAL",   # ambiguous — could be positive or negative
    "sadness":  "NEGATIVE",
    "anger":    "NEGATIVE",
    "fear":     "NEGATIVE",
    "disgust":  "NEGATIVE",
}


async def analyze_sentiment(text: str) -> dict:
    """
    Returns:
        {
            "sentiment":  "POSITIVE" | "NEUTRAL" | "NEGATIVE",
            "emotion":    "joy" | "neutral" | "sadness" | "anger" | "fear" | "disgust" | "surprise",
            "score":      float 0.0–1.0  (model confidence),
        }
    Falls back to NEUTRAL on any failure.
    """
    if not text or not text.strip():
        return _neutral()

    if not HUGGINGFACE_API_KEY:
        print("[Sentiment] HUGGINGFACE_API_KEY not set — skipping")
        return _neutral()

    text = text.strip()[:SENTIMENT_MAX_CHARS]

    sem = _get_semaphore()
    async with sem:                          # max SENTIMENT_MAX_CONCURRENT at once
        for attempt in range(SENTIMENT_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=SENTIMENT_TIMEOUT) as client:
                    resp = await client.post(
                        _HF_URL,
                        headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
                        json={"inputs": text},
                    )

                if resp.status_code == 503:
                    # Model is cold-starting — HF returns estimated wait time
                    wait = min(resp.json().get("estimated_time", 20), 30)
                    print(f"[Sentiment] Model loading, waiting {wait:.0f}s (attempt {attempt+1})")
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code == 429:
                    # Rate limited — exponential backoff
                    wait = 5 * (2 ** attempt)
                    print(f"[Sentiment] Rate limited, waiting {wait}s (attempt {attempt+1})")
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code != 200:
                    print(f"[Sentiment] HF API error {resp.status_code}: {resp.text[:200]}")
                    return _neutral()

                # Response: [[{"label": "joy", "score": 0.95}, ...]]
                data = resp.json()
                emotions: list[dict] = data[0] if isinstance(data[0], list) else data
                best = max(emotions, key=lambda x: x["score"])
                emotion = best["label"].lower()
                score = round(best["score"], 3)
                sentiment = _EMOTION_SENTIMENT.get(emotion, "NEUTRAL")

                print(f"[Sentiment] {sentiment} ({emotion}, {score:.3f})")
                return {"sentiment": sentiment, "emotion": emotion, "score": score}

            except httpx.TimeoutException:
                print(f"[Sentiment] Timeout (attempt {attempt+1}/{SENTIMENT_MAX_RETRIES})")
            except Exception as e:
                print(f"[Sentiment] Error (attempt {attempt+1}): {e}")

            if attempt < SENTIMENT_MAX_RETRIES - 1:
                await asyncio.sleep(2 ** attempt)

    print("[Sentiment] All retries exhausted — returning NEUTRAL")
    return _neutral()


def _neutral() -> dict:
    return {"sentiment": "NEUTRAL", "emotion": "neutral", "score": 0.0}
