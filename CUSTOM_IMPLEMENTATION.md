# Dograh — custom implementation

This checkout is a fork-style copy of [dograh-hq/dograh](https://github.com/dograh-hq/dograh)
(`main` @ `058c540c`, 2026-08-22, `pipecat` submodule @ `ca89ca3c`) that we build
and run ourselves so we can add providers and tailor behaviour.

## What is customised so far

### Soniox STT + TTS provider (`soniox`)

Soniox (https://soniox.com) is wired in as a first-class BYOK provider for both
the Transcriber (STT) and Voice (TTS) tabs. The Pipecat transport classes
(`pipecat/src/pipecat/services/soniox/{stt,tts}.py`) already existed upstream;
the Dograh-side registration is ours:

| File | Change |
| --- | --- |
| `api/services/configuration/options/soniox.py` | Model / language / voice catalogs shown in the UI dropdowns. |
| `api/services/configuration/options/__init__.py` | Re-exports the above. |
| `api/services/configuration/registry.py` | `ServiceProviders.SONIOX`, `SonioxSTTConfiguration`, `SonioxTTSConfiguration`, added to the `STTConfig` / `TTSConfig` discriminated unions (required for saved configs to load). |
| `api/services/configuration/check_validity.py` | `_check_soniox_api_key` — live key check against `GET https://api.soniox.com/v1/tts-models`; 401/403 rejects the save. |
| `api/services/pipecat/service_factory.py` | Builds `SonioxSTTService` / `SonioxTTSService`; classifies Soniox as an external-turn STT when `turn_detection=soniox`; forwards workflow keyterms as Soniox `context.terms`. |
| `api/tests/test_soniox_service_factory.py` | 32 tests: defaults, schema, save→reload round-trip, validator, factory kwargs. |
| `docs/configurations/{transcriber,voice}.mdx` | Provider documentation. |

STT options: `model` (`stt-rt-v5`), `language` (`auto` or ISO 639-1 hint),
`turn_detection` (`soniox` = Soniox semantic endpointing, `vad` = Dograh VAD /
Smart Turn), `max_endpoint_delay_ms` (500–3000).
TTS options: `model` (`tts-rt-v2`), `voice` (28 built-in names or a cloned-voice
UUID), `language` (ISO 639-1), `speed` (0.7–1.3).

No UI changes were needed — the provider forms are rendered from the backend
JSON schema. Generated artefacts (`docs/api-reference/openapi.json`,
`ui/src/client/types.gen.ts`) are not regenerated yet; they are cosmetic for
runtime behaviour.

## Running this instance

`docker-compose.override.yaml` builds `api` and `ui` from this tree and shifts
every host port by +10000 (Redis +20000) so it can run **alongside** the stock
Dograh install in `D:\Dograh Voice Pipeline`. `.env` (gitignored) holds this
instance's secrets plus `BACKEND_API_ENDPOINT` / `MINIO_PUBLIC_ENDPOINT`
pointing at the shifted ports.

| Service | This instance | Stock instance |
| --- | --- | --- |
| UI | http://localhost:13010 | http://localhost:3010 |
| API | http://localhost:18000 | http://localhost:8000 |
| MinIO / console | 127.0.0.1:19000 / 19001 | 127.0.0.1:9000 / 9001 |
| Postgres | 127.0.0.1:15432 | 5432 |
| Redis | 127.0.0.1:26379 | 127.0.0.1:16379 |
| cloudflared metrics | 127.0.0.1:12000 | 2000 |

All services carry `restart: unless-stopped`, so once started the stack comes
back by itself whenever Docker Desktop starts. You only need the commands below
after a `down`, after pulling/editing code, or to stop it.

```powershell
cd "D:\Dograh Voice Pipeline\Dograh-Custom-Implementation"

# start (images already built) — e.g. after a `down`
docker compose --profile tunnel up -d

# start after code changes (rebuilds only what changed, then restarts)
docker compose --profile tunnel up -d --build

# stop (keeps data volumes; also disables auto-restart until the next `up`)
docker compose --profile tunnel down

# rebuild a single image
docker compose build api
docker compose build ui

# logs
docker compose logs -f api
```

Compose project name is `dograh-custom`, so containers are `dograh-custom-api-1`,
`dograh-custom-ui-1`, … and volumes are `dograh-custom_postgres_data` etc. —
fully separate from the stock stack's data.

## Talking to other local services (Speaches, Langfuse, Ollama, …)

The stack has its own Docker network, so other containers are **not** reachable
by container name. Anything published on the host is reachable from the api
container as `http://host.docker.internal:<host port>` — exactly like the stock
install:

| Service | URL to enter in Dograh |
| --- | --- |
| Speaches (STT/TTS/LLM, OpenAI-compatible) | `http://host.docker.internal:8100/v1` |
| Langfuse (Settings → Langfuse host) | `http://host.docker.internal:3000` |
| Ollama / local llama (LLM tab → "Local Models (Speaches)" provider) | `http://host.docker.internal:11434/v1` |

These are stored per organisation in this instance's own Postgres, so they must
be entered once here even if the stock install already has them (or copy the
stock database over — see below).

### Copying data from the stock install (optional, one-off)

```powershell
docker exec dograhvoicepipeline-postgres-1 pg_dump -U postgres -d postgres --clean --if-exists > stock.sql
docker exec -i dograh-custom-postgres-1 psql -U postgres -d postgres -q < stock.sql
docker compose restart api      # runs the pending Alembic migrations on the copied data
```

This replaces everything in the custom database (users, agents, model/Langfuse
/telephony configuration). Passwords carry over; sessions do not (different
`OSS_JWT_SECRET`), so log in again. MinIO recordings live in a separate volume
and are not copied.

## Running the backend tests

The runtime image has no pytest; the quickest loop is an ephemeral container
with this tree mounted:

```powershell
docker run -d --name dograh-custom-test --user root -e PYTHONPATH=/work `
  -v "D:\Dograh Voice Pipeline\Dograh-Custom-Implementation:/work" -w /work/api `
  --entrypoint sleep dograh-custom/dograh-api:latest infinity
docker exec dograh-custom-test sh -c "pip install -q pytest pytest-asyncio python-dotenv; cp -n .env.test.example .env.test"

docker exec -w /work/api dograh-custom-test python -m pytest tests/test_soniox_service_factory.py -q -p no:cacheprovider
```

(Tests that need Postgres use the `db_session` fixtures and expect a `test_db`
reachable via `DATABASE_URL` in `api/.env.test`; the Soniox suite does not.)

## Headless end-to-end voice check

`scripts/e2e_voice_pipeline_check.py` drives the real Dograh factories with the
organisation's saved configuration: it streams a WAV into the STT in real time,
lets the LLM answer and the TTS synthesise, and prints a timeline plus a
`RESULT {...}` JSON line (`ok: true` when transcript, reply and audio all
arrived). It was used to validate Soniox STT + Groq + Speaches/Kokoro on
2026-08-23 (English with and without keyterms, Hindi with `language=auto`).

Environment switches: `E2E_RATE=8000` (telephony rate instead of 16 kHz),
`E2E_INTERRUPT=1` (barge in while the bot speaks; passes only if stream #1 is
cut and a second stream completes), `E2E_LONG_REPLY=1` (long LLM reply so the
barge-in lands mid-generation), `E2E_BARGE_DELAY=0.2` (seconds after first bot
audio), `E2E_DEBUG=1` (Soniox / TTS / aggregator debug logs).

Verified 2026-08-23 with Soniox STT + Soniox TTS (`tts-rt-v2`, voice Priya):
16 kHz and 8 kHz both fine; word-level `TTSTextFrame`s arrive with the audio;
barge-in stops stream #1 within ~0.1 s of the `InterruptionFrame` (the base
`WebsocketTTSService` reconnects the socket, so an interrupted stream never
emits `TTSStoppedFrame` — normal) and the next turn opens a fresh stream.

```powershell
# 1. make a test utterance with the local Speaches server (any mono 16-bit WAV works)
docker exec dograh-custom-api-1 python -c "import urllib.request,json;open('/tmp/u.wav','wb').write(urllib.request.urlopen(urllib.request.Request('http://host.docker.internal:8100/v1/audio/speech',data=json.dumps({'model':'speaches-ai/Kokoro-82M-v1.0-ONNX','voice':'af_heart','input':'Hello, this is a test call.','response_format':'wav'}).encode(),headers={'Content-Type':'application/json'})).read())"
# 2. run against organisation 1, optionally with comma-separated keyterms
docker cp scripts/e2e_voice_pipeline_check.py dograh-custom-api-1:/tmp/check.py
docker exec -w /app dograh-custom-api-1 python /tmp/check.py 1 /tmp/u.wav "Dograh,Soniox"
```

## Keeping up with upstream

```powershell
git remote add upstream https://github.com/dograh-hq/dograh.git   # once
git fetch upstream
git merge upstream/main
git submodule update --init --recursive
```

Note: the repo was cloned with `core.symlinks=false` (Windows, no symlink
privilege); the two `api/native/rnnoise/librnnoise.so` / `.so.0` symlinks are
checked out as 19-byte text files and are copied into the image as such. Git
still treats them as unchanged. Today this is harmless — `librnnoise_path` in
`api/services/pipecat/audio_mixer.py` is defined but never loaded. If you ever
enable RNNoise, either enable Windows Developer Mode and re-checkout with
`git config core.symlinks true && git checkout -- api/native`, or point the
loader at `librnnoise.so.0.4.1` (the real binary).
