"""Soniox speech-to-text and text-to-speech options.

Soniox exposes one real-time STT model and one real-time TTS model, both over
WebSocket, covering 60+ languages with automatic language identification on the
STT side and a single voice catalog that works across every language on the
TTS side.

References:
  - STT models:      https://soniox.com/docs/stt/models
  - STT languages:   https://soniox.com/docs/stt/concepts/supported-languages
  - TTS models:      https://soniox.com/docs/tts/models
  - TTS voices:      https://soniox.com/docs/tts/concepts/voices
  - TTS model list:  GET https://api.soniox.com/v1/tts-models
"""

# Real-time (rt) STT models only — Dograh transcribes live call audio, so the
# async models are intentionally excluded. stt-rt-v5 is the current model;
# older names (stt-rt-v4) are server-side aliases for it.
SONIOX_STT_MODELS = ("stt-rt-v5",)

# Turn-detection modes for the Soniox STT integration.
#   soniox — Soniox's built-in semantic endpoint detection decides when the
#            caller has finished speaking (external-turn mode, lowest latency).
#   vad    — Dograh's local VAD / turn strategies drive finalization; Soniox
#            endpoint detection is disabled (same mode as Sarvam/Deepgram Nova).
SONIOX_STT_TURN_DETECTION_MODES = ("soniox", "vad")

# ISO 639-1 codes accepted as language hints. "auto" enables Soniox's automatic
# language identification (no hint is sent). English and the Indian languages
# are listed first; the remainder is alphabetical. Any code Soniox accepts can
# be typed in as a custom value.
SONIOX_STT_LANGUAGES = (
    "auto",
    "en",
    "hi",
    "gu",
    "bn",
    "kn",
    "ml",
    "mr",
    "pa",
    "ta",
    "te",
    "ur",
    "af",
    "ar",
    "az",
    "be",
    "bg",
    "bs",
    "ca",
    "cs",
    "cy",
    "da",
    "de",
    "el",
    "es",
    "et",
    "eu",
    "fa",
    "fi",
    "fr",
    "gl",
    "he",
    "hr",
    "hu",
    "id",
    "it",
    "ja",
    "kk",
    "ko",
    "lt",
    "lv",
    "mk",
    "ms",
    "nl",
    "no",
    "pl",
    "pt",
    "ro",
    "ru",
    "sk",
    "sl",
    "sq",
    "sr",
    "sv",
    "sw",
    "th",
    "tl",
    "tr",
    "uk",
    "vi",
    "zh",
)

# tts-rt-v2 is the current model (GA Aug 2026); tts-rt-v1 is deprecated and
# scheduled for removal, so it is deliberately not offered here.
SONIOX_TTS_MODELS = ("tts-rt-v2",)

# Built-in voices as returned by GET /v1/tts-models. Every voice works with all
# supported languages; the accent groups are: neutral/US (Maya … Kenji),
# Spanish (Rafael … Sofia), British (Oliver … Victoria), Australian
# (Cooper … Elise) and Indian (Arjun … Meera). Cloned-voice UUIDs are also
# accepted via custom input.
SONIOX_TTS_VOICES = (
    "Adrian",
    "Maya",
    "Daniel",
    "Noah",
    "Nina",
    "Emma",
    "Jack",
    "Claire",
    "Grace",
    "Owen",
    "Mina",
    "Kenji",
    "Rafael",
    "Mateo",
    "Lucia",
    "Sofia",
    "Oliver",
    "Arthur",
    "Isla",
    "Victoria",
    "Cooper",
    "Mason",
    "Ruby",
    "Elise",
    "Arjun",
    "Rohan",
    "Priya",
    "Meera",
)

# TTS language codes (ISO 639-1). Same ordering convention as the STT list.
SONIOX_TTS_LANGUAGES = tuple(
    code for code in SONIOX_STT_LANGUAGES if code != "auto"
) + ("is", "su")
