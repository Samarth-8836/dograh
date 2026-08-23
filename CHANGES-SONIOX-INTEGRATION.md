# Change log — Soniox integration and custom build

Companion to [HANDOFF.md](HANDOFF.md). This document records **every file we
added or changed relative to upstream Dograh** (`main` @ `058c540c`), what the
code does, why it was done that way, and how it was verified. Anything not
listed here is untouched upstream code.

Commits: `e97887a0` (integration), `47c2835c` (auto-restart + docs), plus the
docs commit that added this file. Branch `soniox-integration` on
https://github.com/Samarth-8836/dograh.

---

## 1. Summary of the change

Soniox (https://soniox.com) becomes a selectable **STT** ("Transcriber") and
**TTS** ("Voice") provider in Dograh's BYOK configuration. The Pipecat transport
classes (`SonioxSTTService`, `SonioxTTSService`) already existed in the
`pipecat` submodule; what was missing was the Dograh-side registration. No UI
code changed — Dograh renders provider forms from the backend JSON schema.

```
UI dropdown ──► registry.py (pydantic config → JSON schema)
save ─────────► check_validity.py (live key check) ──► Postgres (org config v2)
call start ───► service_factory.py (config → SonioxSTTService / SonioxTTSService)
```

---

## 2. How a provider is registered in Dograh (the pattern we followed)

Five touch-points, all backend. Missing any of the first three silently breaks
the provider (this is exactly what sank upstream PR #649):

| # | File | Purpose |
| --- | --- | --- |
| 1 | `api/services/configuration/options/<provider>.py` + `options/__init__.py` | constant tuples for dropdowns (models, languages, voices) |
| 2 | `api/services/configuration/registry.py` | `ServiceProviders` enum value, the `provider` `Literal[...]` whitelist, a `provider_model_config(...)` (UI title/description/docs link), `@register_stt` / `@register_tts` pydantic classes, **and membership in the `STTConfig` / `TTSConfig` discriminated unions** (otherwise saved configs fail to load) |
| 3 | `api/services/configuration/check_validity.py` | entry in `UserConfigurationValidator._validator_map` (an unregistered provider always fails validation → save blocked) |
| 4 | `api/services/pipecat/service_factory.py` | `elif provider == ...` branch in `create_stt_service` / `create_tts_service`; add to `stt_uses_external_turns` if the STT emits its own turn boundaries |
| 5 | `api/tests/test_<provider>_service_factory.py` | schema, round-trip, validator and factory tests |

---

## 3. Files added / changed

### 3.1 `api/services/configuration/options/soniox.py` (new)

Constant catalogs used by the UI dropdowns:

- `SONIOX_STT_MODELS = ("stt-rt-v5",)` — real-time model only (async models are
  useless for live calls; `stt-rt-v4` is a server-side alias).
- `SONIOX_STT_TURN_DETECTION_MODES = ("soniox", "vad")`.
- `SONIOX_STT_LANGUAGES` — `"auto"` first, then English and the Indian
  languages (`hi gu bn kn ml mr pa ta te ur`), then the remaining ISO 639-1
  codes Soniox supports (~60). Any code can also be typed as a custom value.
- `SONIOX_TTS_MODELS = ("tts-rt-v2",)` — `tts-rt-v1` is deprecated (removed by
  Soniox on 2026-08-31) and deliberately not offered.
- `SONIOX_TTS_VOICES` — the 28 built-in voices from `GET /v1/tts-models`
  (`Adrian` first; Indian-accent voices `Arjun`, `Rohan`, `Priya`, `Meera`).
  Every voice speaks every language.
- `SONIOX_TTS_LANGUAGES` — the STT list without `auto`, plus `is`, `su`.

`options/__init__.py` re-exports all six names (alphabetical position between
`smallest` and `speechmatics`).

### 3.2 `api/services/configuration/registry.py`

- `ServiceProviders.SONIOX = "soniox"`; added to the base `provider`
  `Literal[...]` list.
- `SONIOX_PROVIDER_MODEL_CONFIG = provider_model_config("Soniox", description=…,
  provider_docs_url="https://soniox.com/docs")` — the dropdown label and the
  help text/link shown in the UI.
- `SonioxTTSConfiguration(BaseTTSConfiguration)` — `@register_tts`:

  | field | default | notes |
  | --- | --- | --- |
  | `model` | `tts-rt-v2` | examples + `allow_custom_input` |
  | `voice` | `Adrian` | examples = 28 voices, `allow_custom_input` (cloned-voice UUIDs) — this combination makes the UI show a dropdown **plus** free text and bypass the MPS voice-catalog picker, so no UI change was needed |
  | `language` | `en` | ISO 639-1, examples + custom |
  | `speed` | `1.0` | `ge=0.7, le=1.3` (Soniox's range) |

- `SonioxSTTConfiguration(BaseSTTConfiguration)` — `@register_stt`:

  | field | default | notes |
  | --- | --- | --- |
  | `model` | `stt-rt-v5` | examples + custom |
  | `language` | `auto` | language *hint*; `auto` = Soniox language identification |
  | `turn_detection` | `soniox` | `Literal["soniox","vad"]`; rendered as an enum dropdown |
  | `max_endpoint_delay_ms` | `2000` | `ge=500, le=3000`; only meaningful with `turn_detection=soniox` |

- Both classes appended to the `TTSConfig` and `STTConfig` unions
  (`Field(discriminator="provider")`) so `EffectiveAIModelConfiguration` /
  `BYOKPipelineAIModelConfiguration` can load a saved Soniox config.

### 3.3 `api/services/configuration/check_validity.py`

- `_validator_map[ServiceProviders.SONIOX.value] = self._check_soniox_api_key`.
- `_check_soniox_api_key(model, api_key)`: `GET https://api.soniox.com/v1/tts-models`
  with `Authorization: Bearer <key>` (cheap, read-only, accepts any project
  key, so it validates STT and TTS keys alike). `401`/`403` → `ValueError`
  with a user-facing message (blocks the save); connection errors → "Could not
  connect" `ValueError`; any other status → accepted (inconclusive, so a Soniox
  outage can't lock users out of settings). Mirrors the LMNT validator's
  best-effort style.

### 3.4 `api/services/pipecat/service_factory.py`

Imports: `SonioxContextObject, SonioxSTTService, SonioxSTTSettings` from
`pipecat.services.soniox.stt`; `SonioxTTSService, SonioxTTSSettings` from
`pipecat.services.soniox.tts`.

Helpers:

- `soniox_stt_uses_soniox_turns(stt_config)` → `getattr(stt_config,
  "turn_detection", "soniox") != "vad"`. Tolerates config objects that predate
  the field.
- `_resolve_soniox_language_hint(language)` → `None` for `auto` / `unknown` /
  `multi` / empty, else `Language(language)`; unknown codes log a warning and
  fall back to `None` instead of failing pipeline start.
- `stt_uses_external_turns()` now returns `True` for Soniox in `soniox` mode.
  This is the single switch that makes `run_pipeline.py` use
  `ExternalUserTurnStartStrategy(enable_interruptions=True)` /
  `ExternalUserTurnStopStrategy()` and a 30 s stop timeout — i.e. the
  aggregator follows the `UserStarted/StoppedSpeakingFrame`s the STT emits
  (same mechanism Dograh already uses for Deepgram Flux and Cartesia ink-2).

STT branch (`create_stt_service`):

```python
SonioxSTTService(
    api_key=user_config.stt.api_key,
    settings=SonioxSTTSettings(
        model=...,
        language_hints=[hint] or None,
        enable_language_identification=(hint is None),
        max_endpoint_delay_ms=<config value, only when turn_detection=soniox>,
        context=SonioxContextObject(terms=keyterms)  # only when the workflow has keyterms
    ),
    vad_force_turn_endpoint=not use_soniox_turns,   # False → Soniox endpoint detection on
    should_interrupt=False,                         # the user aggregator owns interruptions
    sample_rate=audio_config.transport_in_sample_rate,
)
```

Design notes:

- `vad_force_turn_endpoint=False` is what enables Soniox's semantic endpointing
  and makes the service emit turn frames; with `turn_detection=vad` it is
  `True`, Soniox endpointing is off, and Dograh's local VAD/Smart Turn drives
  finalisation (the same mode as Sarvam / Deepgram Nova).
- `should_interrupt=False` matches every other Dograh STT: interruptions are
  broadcast by the aggregator's turn-start strategy, not by the STT.
- Workflow **keyterms** (Deepgram's boosting list) are forwarded as Soniox
  `context.terms`, Soniox's equivalent feature.
- Soniox accepts raw PCM at any rate; we pass the transport's input rate (8 kHz
  telephony / 16 kHz WebRTC) with `audio_format="pcm_s16le"` (service default).

TTS branch (`create_tts_service`):

```python
SonioxTTSService(
    api_key=user_config.tts.api_key,
    sample_rate=audio_config.transport_out_sample_rate,   # 8k/16k are in Soniox's allowed set
    settings=SonioxTTSSettings(model="tts-rt-v2"|cfg, voice=cfg or "Adrian",
                               language=Language(cfg) or raw code, speed=cfg if != 1.0),
    text_filters=[xml_function_tag_filter],               # same three kwargs every Dograh TTS gets
    skip_aggregator_types=["recording_router", "recording"],
    silence_time_s=1.0,
)
```

Design notes:

- Passing `sample_rate` explicitly avoids any resampling: Soniox supports
  8000/16000/24000/44100/48000 Hz, which covers both Dograh transports.
- `language` goes through the base class, which maps `Language` enums to Soniox
  codes with `use_base_code=True` (`en-IN` → `en`, `gu` → `gu`); unknown strings
  pass through unchanged.
- Nothing else differs from other WebSocket TTS providers (ElevenLabs,
  Cartesia): same base class (`WebsocketTTSService`), so recording, cost
  metrics (`TTSUsageMetricsData`), failure attribution and interruption handling
  are inherited.

### 3.5 `api/tests/test_soniox_service_factory.py` (new, 32 tests)

- `TestSonioxConfiguration`: defaults, catalogs, registry membership, JSON
  schema exposes `title`, `provider_docs_url`, `allow_custom_input`, enum for
  `turn_detection`, `speed` bounds; out-of-range `speed` and invalid
  `turn_detection` rejected.
- `test_soniox_configuration_survives_save_and_reload`: a BYOK payload with
  Soniox STT+TTS validates as `BYOKPipelineAIModelConfiguration`, dumps, reloads
  as `EffectiveAIModelConfiguration`, and the reloaded object drives both
  factories.
- `TestSonioxValidator`: map entry exists; 200 accepts; 401 rejects with the
  Soniox message; 503 is inconclusive; `_validate_service` passes for STT and
  TTS with a stubbed 200 and fails with a stubbed 401 (stub `httpx.get` — the
  validator map holds bound methods, so patching the method doesn't work).
- `TestSonioxSTTServiceFactory`: external-turn classification; `auto` →
  identification on, hints off, endpoint delay forwarded; `hi`/`gu`/`en`/`en-IN`
  → hints; `auto|unknown|multi|""|None|bad-code` → identification; `vad` mode
  → `vad_force_turn_endpoint=True` and no endpoint delay; keyterms → context;
  configs lacking the new fields default to Soniox turns.
- `TestSonioxTTSServiceFactory`: defaults and shared kwargs; Indian-language +
  voice + speed; blank voice/language fallbacks; unknown language passthrough;
  cloned-voice UUID passthrough.

Run: see HANDOFF.md §7. Result on 2026-08-23: 32 passed; 178 tests in the
neighbouring configuration/factory suites unchanged.

### 3.6 Documentation

- `docs/configurations/transcriber.mdx`, `docs/configurations/voice.mdx`:
  Soniox added to the provider lists plus a settings table each.
- `HANDOFF.md`, this file. (`CUSTOM_IMPLEMENTATION.md` was the first draft of
  the runbook and has been folded into `HANDOFF.md`.)

### 3.7 Build / run infrastructure

| File | What |
| --- | --- |
| `docker-compose.override.yaml` (new) | auto-merged by Compose: builds `api` (`api/Dockerfile`, context `.`) and `ui` (`ui/Dockerfile`) from this tree as `dograh-custom/dograh-{api,ui}:latest`; overrides host ports (+10000, Redis +20000) and container names so it coexists with the stock install; `restart: unless-stopped` everywhere |
| `.env` (new, **gitignored**) | `COMPOSE_PROJECT_NAME=dograh-custom`, fresh `OSS_JWT_SECRET` / DB / Redis / MinIO secrets, `BACKEND_API_ENDPOINT=http://localhost:18000`, `MINIO_PUBLIC_ENDPOINT=http://localhost:19000` |
| `.gitattributes` (new) | `* text=auto eol=lf` (+ binary rules). The initial Windows checkout had CRLF everywhere, which turned `#!/usr/bin/env bash` into `bash\r` inside the image and made the API container exit 127. Repo also has `core.autocrlf=false`, `core.eol=lf` |
| `scripts/e2e_voice_pipeline_check.py` (new) | headless STT→LLM→TTS check through the real factories and the organisation's saved DB config; per-stream TTS accounting; switches `E2E_RATE`, `E2E_INTERRUPT`, `E2E_LONG_REPLY`, `E2E_BARGE_DELAY`, `E2E_DEBUG` |

Windows note: the clone uses `core.symlinks=false` (no symlink privilege), so
`api/native/rnnoise/librnnoise.so` and `.so.0` are 19-byte text files pointing
at `librnnoise.so.0.4.1`. Git still sees them as unchanged. `librnnoise_path`
in `api/services/pipecat/audio_mixer.py` is defined but never loaded, so this is
harmless; if RNNoise is ever enabled, re-checkout with symlinks or point the
loader at the real `.so.0.4.1`.

---

## 4. Behavioural notes and precautions (Soniox specifics)

- **Turn detection.** With `turn_detection=soniox`, Soniox's semantic
  endpointing decides the end of the caller's turn. Questions close in ~0.3 s;
  statements close at `max_endpoint_delay_ms` (measured 2.7 s at 2000, 1.9 s at
  1200). 1000–1200 is a good default for agents. `vad` hands control back to
  Dograh's VAD / Smart Turn.
- **Language.** STT `auto` enables Soniox language identification (tokens are
  tagged, transcripts carry `language`); a code becomes a *hint*. TTS
  `language` is the primary language; mixed-language text switches naturally
  (a Hindi reply was spoken correctly with `language=en`).
- **TTS is a persistent WebSocket** with one multiplexed stream per turn
  (`stream_id` = Pipecat audio context). Streams are pre-opened when a turn
  starts, cancelled on barge-in, and an interrupted stream emits **no**
  `TTSStoppedFrame` (the base class reconnects the socket) — this is the same
  as ElevenLabs and is expected. Limits: 5 streams per connection (Dograh uses
  one per turn), idle connections closed after ~3 min (keepalives every 20 s;
  lazy reconnect on the next turn).
- **Word timestamps.** The TTS emits word-level `TTSTextFrame`s from Soniox
  character timestamps, so transcripts and interruption truncation are
  accurate.
- **Audio tags.** `tts-rt-v2` interprets bracketed performance tags (e.g.
  `[excited]`) — prompts that make the LLM emit bracketed text may be *acted*
  rather than read.
- **Key validation** hits a real endpoint; only 401/403 blocks a save.
- **Regions.** EU/JP endpoints exist (`stt-rt.eu.soniox.com`,
  `tts-rt.eu.soniox.com`) but are not exposed; add a `url=` kwarg in the factory
  branches if needed.

---

## 5. Verification record (2026-08-23, instance org 1)

| # | Setup | Result |
| --- | --- | --- |
| 1 | PUT `/organizations/model-configurations/v2` with Soniox STT, Groq (4 keys), Speaches TTS | 200; API log shows `api.soniox.com/v1/tts-models → 200`, `api.groq.com/openai/v1/models → 200`; GET returns the config with masked keys |
| 2 | e2e English (Kokoro WAV → Soniox STT → Groq → Kokoro) | transcript `"…Soniox speech-to-text integration with Dagra."`, reply `"Test call received and confirmed."`, 1.8 s audio |
| 3 | e2e English + keyterms `Dograh,Soniox` | context accepted; proper noun still phonetic (`Dogra`) — Kokoro pronunciation |
| 4 | e2e Hindi, `language=auto` | `lang=hi`, transcript correct, endpoint 0.32 s after speech |
| 5 | e2e Soniox STT + **Soniox TTS** (Priya) @16 kHz | first audio 0.58 s after LLM end; 4 word frames; `TTSStopped` via `terminated` |
| 6 | same @8 kHz | first audio 0.34 s; 5 word frames; 2.0 s audio |
| 7 | barge-in, long reply (`E2E_INTERRUPT=1 E2E_LONG_REPLY=1 E2E_BARGE_DELAY=0.2`) | `InterruptionFrame` at 9.85 s; stream #1 last audio 9.74 s (2.1 s total, no stop frame = cancelled); stream #2 first audio 0.35 s after `TTSStarted`, 72.6 s, terminated normally; `ok: true` |
| 8 | unit tests | 32 passed (Soniox) + 178 passed (neighbouring suites) |

Not yet verified: a live browser/phone call with Soniox TTS selected (transport
pacing and `BotStartedSpeaking` come from the output transport, which the
headless harness doesn't have).
