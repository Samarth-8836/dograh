# Dograh custom build — handoff

> **Start here.** This file is the single entry point for anyone (human or AI
> agent) picking up this project. It explains what this repository is, how it
> got here, the everyday commands, how to connect it to the other local
> services, how to change and test it, and what is still open.
>
> Code-level detail of the Soniox work lives in
> **[CHANGES-SONIOX-INTEGRATION.md](CHANGES-SONIOX-INTEGRATION.md)**.

---

## 1. What this is (30-second version)

- A **custom build of [Dograh](https://github.com/dograh-hq/dograh)** (open-source
  voice-agent platform: FastAPI backend + Next.js UI + Pipecat voice pipeline).
- We build our **own Docker images from this source tree** instead of pulling
  `ghcr.io/dograh-hq/dograh-*`, so we can add providers and tailor behaviour.
- First customisation: **Soniox** (https://soniox.com) as a first-class
  speech-to-text **and** text-to-speech provider, selectable from the normal
  provider dropdowns. Verified end-to-end (see §9).
- It runs **side by side** with the untouched stock install in the parent folder
  (`D:\Dograh Voice Pipeline`), on ports shifted by +10000.

| Item | Value |
| --- | --- |
| Repo path | `D:\Dograh Voice Pipeline\Dograh-Custom-Implementation` |
| Git branch | `soniox-integration` (work branch; `main` is pristine upstream) |
| Remotes | `origin` = https://github.com/Samarth-8836/dograh (our fork, **public**) · `upstream` = https://github.com/dograh-hq/dograh |
| Upstream base | `main` @ `058c540c` (2026-08-22), `pipecat` submodule @ `ca89ca3c` (v1.1.0) |
| App version | 1.45.0 (`GET /api/v1/health`) |
| Compose project | `dograh-custom` → containers `dograh-custom-api-1`, `dograh-custom-ui-1`, `dograh-custom-postgres-1`, `dograh-custom-redis-1`, `dograh-custom-minio`, `dograh-custom-cloudflared` |
| Images | `dograh-custom/dograh-api:latest` (≈2.5 GB), `dograh-custom/dograh-ui:latest` (≈450 MB) |
| Data volumes | `dograh-custom_postgres_data`, `dograh-custom_redis_data`, `dograh-custom_minio-data` |
| UI | http://localhost:13010 |
| API | http://localhost:18000 (`/api/v1/health`) |
| Secrets file | `.env` in the repo root — **gitignored, never commit** (fork is public) |
| Owner | Samarth Patel (GitHub `Samarth-8836`) |

---

## 2. Background — how we got here

1. The owner runs a stock Dograh install (`D:\Dograh Voice Pipeline`, prebuilt
   images, UI on :3010) with a local **Speaches** server (Whisper STT / Kokoro
   TTS, port 8100), a local **Langfuse** (port 3000) and **Sarvam** keys.
2. They wanted **Soniox** STT + TTS, which the stock images don't offer. Adding a
   provider needs backend code changes → a source build was required.
3. We cloned upstream into this folder, registered Soniox on the Dograh side
   (the Pipecat transport classes already existed in the submodule), built our
   own images, and set the stack up to coexist with the stock one.
4. We validated the integration headlessly with real keys: Soniox STT + Groq
   `openai/gpt-oss-120b` + Speaches Kokoro, and Soniox STT + Soniox TTS (16 kHz,
   8 kHz, mid-speech barge-in, Hindi with auto language ID).
5. Upstream context: dograh-hq PR #649 tried to add Soniox STT only and was
   closed because it missed validator registration, the config union, and
   round-trip tests. Our change covers all of that plus TTS.

Commits on `soniox-integration`:

| Commit | What |
| --- | --- |
| `e97887a0` | feat: Soniox Integration (STT + TTS provider, custom build setup) |
| `47c2835c` | chore: auto-restart the custom stack and document local-service wiring |
| (next) | docs: handoff + change log (this file) |

---

## 3. Architecture in one minute

```
browser / phone ──► ui (Next.js, :13010) ──► api (FastAPI, :18000) ──► Pipecat pipeline
                                                     │                      STT → LLM → TTS
                                                     ├── postgres (configs, workflows, runs)
                                                     ├── redis (queues / ARQ)
                                                     ├── minio (recordings)
                                                     └── cloudflared (public URL for telephony webhooks)
```

- **Providers are data-driven.** `api/services/configuration/registry.py`
  registers a pydantic config class per provider; the UI renders its form from
  the JSON schema (`GET /api/v1/organizations/model-configurations/v2/defaults`).
  Adding a provider = backend only.
- `api/services/pipecat/service_factory.py` turns a saved config into Pipecat
  service objects for each call.
- `pipecat/` is a **git submodule** (Dograh's fork of Pipecat) and is installed
  into the API image at build time.
- The only files we own are listed in
  [CHANGES-SONIOX-INTEGRATION.md](CHANGES-SONIOX-INTEGRATION.md); everything
  else is upstream.

---

## 4. Everyday commands

All commands run from the repo root in PowerShell (Git Bash works too).

```powershell
cd "D:\Dograh Voice Pipeline\Dograh-Custom-Implementation"
```

### Start / stop / status

| Task | Command |
| --- | --- |
| Is it running? | `docker compose ps` |
| Start (images already built) | `docker compose --profile tunnel up -d` |
| Start after code changes (rebuild what changed) | `docker compose --profile tunnel up -d --build` |
| Stop (keeps all data) | `docker compose --profile tunnel down` |
| Logs (backend) | `docker compose logs -f api` |
| Logs (everything) | `docker compose --profile tunnel logs -f` |
| Restart just the API | `docker compose restart api` |
| Health | `curl http://localhost:18000/api/v1/health` |

Every service has `restart: unless-stopped`, so after a reboot the stack comes
back on its own when Docker Desktop starts. `down` switches that off until the
next `up`.

### Rebuild after editing code

| You changed… | Run |
| --- | --- |
| anything under `api/` or `pipecat/` | `docker compose build api && docker compose --profile tunnel up -d api` (≈1–2 min; deps are cached unless `api/requirements.txt` or the submodule changed, then ≈10 min) |
| anything under `ui/` | `docker compose build ui && docker compose --profile tunnel up -d ui` (≈4–5 min) |
| `docker-compose*.yaml` / `.env` | `docker compose --profile tunnel up -d` |

If a build fails with DNS / "failed to fetch" errors, it is Docker Desktop's
build network hiccuping — just rerun (don't run the api and ui builds in
parallel).

### Git

```powershell
git status
git add -A && git commit -m "feat: ..."          # conventional-commit titles
git push                                          # branch tracks origin/soniox-integration

# pull upstream Dograh changes into our branch
git fetch upstream
git merge upstream/main
git submodule update --init --recursive
docker compose --profile tunnel up -d --build
```

Never commit `.env`, `api/.env`, `api/.env.test` or any API key — the fork is
public. Keys belong in the app (they're stored in Postgres per organisation).

### Destroy and recreate from scratch (data loss!)

```powershell
docker compose --profile tunnel down -v           # removes the dograh-custom_* volumes
docker compose --profile tunnel up -d --build
```

---

## 5. Using the app / configuring providers

1. Open http://localhost:13010 and sign up / log in (local OSS auth; signup is
   enabled). A test account `soniox-test@example.com` already exists in this
   instance's DB (the owner has the password; or just sign up a new user).
2. **AI Models Configuration** (`/model-configurations`, also linked from
   Overview) → choose **BYOK** (bring your own keys), pipeline mode (not
   "Speech to Speech").
3. Tabs: **LLM**, **Voice** (TTS), **Transcriber** (STT), **Embedding**. Each
   tab has a provider dropdown; the form below it is generated from the backend
   schema. **Save** runs live key checks and stores the config for the
   organisation.
4. Per-agent overrides: **Workflow → Settings → Model Overrides** (same forms).

Provider cheat-sheet for this deployment:

| Tab | Provider (dropdown) | Fields to set |
| --- | --- | --- |
| Transcriber | **Soniox** | API key · `model` `stt-rt-v5` · `language` `auto` (or `en`/`hi`/`gu`…) · `turn_detection` `soniox` · `max_endpoint_delay_ms` **1000–1200** recommended |
| Voice | **Soniox** | same API key · `model` `tts-rt-v2` · `voice` (e.g. `Priya`, `Arjun`, `Adrian`, or a cloned-voice UUID) · `language` · `speed` 0.7–1.3 |
| Voice | Local Models (Speaches) | `base_url` `http://host.docker.internal:8100/v1` · `model` `speaches-ai/Kokoro-82M-v1.0-ONNX` · `voice` `af_heart` (Hindi: `hf_alpha`) |
| Transcriber | Local Models (Speaches) | `base_url` `http://host.docker.internal:8100/v1` · a Whisper model id served by Speaches |
| LLM | Groq | `model` `openai/gpt-oss-120b` · API key can be a **list** (the UI multi-key input) — Dograh picks one at random per call, which spreads rate limits |
| LLM | Local Models (Speaches) | `base_url` `http://host.docker.internal:11434/v1` for Ollama (any OpenAI-compatible server) |
| Settings → Langfuse | — | host `http://host.docker.internal:3000` + the project's public/secret keys |

Current state of the instance (org 1): STT = Soniox (`auto`, 1200 ms), LLM =
Groq `gpt-oss-120b` with 4 keys, TTS = Speaches Kokoro `af_heart`.

---

## 6. Connecting to other local services

The stack has its own Docker network, so other containers are **not** reachable
by name. Anything published on the Windows host is reachable from inside the
containers as `host.docker.internal:<host port>`:

| Service | Where it runs | URL from Dograh |
| --- | --- | --- |
| Speaches (Whisper STT / Kokoro TTS / OpenAI-compatible) | container `speaches`, host port 8100 | `http://host.docker.internal:8100/v1` |
| Langfuse | `langfuse-local-*` compose project, host port 3000 | `http://host.docker.internal:3000` |
| Ollama / local llama | host / container on 11434 | `http://host.docker.internal:11434/v1` |
| Stock Dograh | `D:\Dograh Voice Pipeline`, UI :3010 / API :8000 | independent; not linked |

These settings live in **this instance's own Postgres**, so they must be entered
here once even though the stock install already has them. To carry everything
over from the stock install instead (users, agents, Langfuse/model/telephony
configs — replaces this instance's DB):

```powershell
docker exec dograhvoicepipeline-postgres-1 pg_dump -U postgres -d postgres --clean --if-exists > stock.sql
docker exec -i dograh-custom-postgres-1 psql -U postgres -d postgres -q < stock.sql
docker compose restart api      # applies the newer Alembic migrations to the copied data
```

Passwords carry over; sessions don't (different `OSS_JWT_SECRET`) — log in
again. MinIO recordings are a separate volume and are not copied.

Public reachability for telephony webhooks uses a Cloudflare **quick tunnel**
(`dograh-custom-cloudflared`, ephemeral `*.trycloudflare.com` URL discovered by
the API at runtime). For a stable hostname set `CLOUDFLARE_TUNNEL_TOKEN` and
`CLOUDFLARED_COMMAND="tunnel run"` in `.env` (see comments in
`docker-compose.yaml`).

---

## 7. Testing

### Backend unit tests (no DB needed for the Soniox suite)

The runtime image has no pytest; use an ephemeral container with the tree mounted:

```powershell
docker run -d --name dograh-custom-test --user root -e PYTHONPATH=/work `
  -v "D:\Dograh Voice Pipeline\Dograh-Custom-Implementation:/work" -w /work/api `
  --entrypoint sleep dograh-custom/dograh-api:latest infinity
docker exec dograh-custom-test sh -c "pip install -q pytest pytest-asyncio python-dotenv; cp -n .env.test.example .env.test"

docker exec -w /work/api dograh-custom-test python -m pytest tests/test_soniox_service_factory.py -q -p no:cacheprovider
# neighbouring suites worth running after touching the registry/factory:
docker exec -w /work/api dograh-custom-test python -m pytest tests/test_sarvam_service_factory.py tests/test_ai_model_configuration_v2.py tests/test_resolve_effective_config.py tests/test_user_configuration_validation.py -q -p no:cacheprovider

docker rm -f dograh-custom-test      # when done
```

Tests that use the `db_session` fixtures need a `test_db` reachable via
`DATABASE_URL` in `api/.env.test`; the Soniox suite does not.

### Headless end-to-end voice check (real providers, no microphone)

`scripts/e2e_voice_pipeline_check.py` loads the organisation's saved config from
the DB, builds STT/LLM/TTS through the real Dograh factories, streams a WAV into
the STT at real time, and prints a timeline + `RESULT {...}` (`ok: true` when a
transcript, an LLM reply and TTS audio all arrived).

```powershell
# 1. a test utterance from the local Speaches server (any mono 16-bit WAV works)
docker exec dograh-custom-api-1 python -c "import urllib.request,json;open('/tmp/u.wav','wb').write(urllib.request.urlopen(urllib.request.Request('http://host.docker.internal:8100/v1/audio/speech',data=json.dumps({'model':'speaches-ai/Kokoro-82M-v1.0-ONNX','voice':'af_heart','input':'Hello, this is a test call.','response_format':'wav'}).encode(),headers={'Content-Type':'application/json'})).read())"
# 2. run (org id 1, optional comma-separated keyterms)
docker cp scripts/e2e_voice_pipeline_check.py dograh-custom-api-1:/tmp/check.py
docker exec -w /app dograh-custom-api-1 python /tmp/check.py 1 /tmp/u.wav "Dograh,Soniox"
```

Switches (env vars on `docker exec -e`): `E2E_RATE=8000` (telephony rate),
`E2E_INTERRUPT=1` (barge in while the bot speaks), `E2E_LONG_REPLY=1` (long
reply so the barge-in lands mid-generation), `E2E_BARGE_DELAY=0.2`,
`E2E_DEBUG=1` (Soniox/TTS/aggregator debug logs).

### Manual check in the browser

Create an agent at http://localhost:13010 → **Test Agent → Test Audio** (needs a
microphone). This is the one path not yet exercised for Soniox TTS (see §10).

---

## 8. Modifying things

| Goal | Where |
| --- | --- |
| Change Soniox defaults / option lists (models, languages, voices) | `api/services/configuration/options/soniox.py`, field defaults in `SonioxSTTConfiguration` / `SonioxTTSConfiguration` (`api/services/configuration/registry.py`) |
| Change how Soniox services are constructed (endpointing, context, sample rate, region URL) | `api/services/pipecat/service_factory.py` (`SONIOX` branches, `soniox_stt_uses_soniox_turns`, `_resolve_soniox_language_hint`) |
| Change the Soniox key check | `api/services/configuration/check_validity.py::_check_soniox_api_key` |
| Add **another** provider | follow the 5-file checklist in [CHANGES-SONIOX-INTEGRATION.md §2](CHANGES-SONIOX-INTEGRATION.md) — options module, registry class + **union membership**, validator map entry, factory branch, tests |
| Change the Pipecat Soniox transport itself | `pipecat/src/pipecat/services/soniox/{stt,tts}.py` (submodule — commit inside `pipecat/`, then bump the submodule pointer here) |
| Ports / restart policy / build settings | `docker-compose.override.yaml` |
| Instance secrets & public endpoints | `.env` (`OSS_JWT_SECRET`, DB/Redis/MinIO passwords, `BACKEND_API_ENDPOINT`, `MINIO_PUBLIC_ENDPOINT`) |
| UI changes | `ui/src/...` then `docker compose build ui`. Provider forms need **no** UI change; the TTS voice picker falls back to a dropdown + free-text when the schema sets `allow_custom_input` |

Conventions to keep (from upstream `AGENTS.md`): route handlers stay thin,
business logic in `api/services/`, org-scoped queries always filter by
`organization_id`, run `ruff format` / `ruff check` (`scripts/format.sh`) before
committing, migrations via `scripts/makemigrate.sh`.

Windows-specific gotchas already handled (don't undo):

- `.gitattributes` forces **LF** — CRLF shell scripts made the container exit
  with code 127 (`bash\r: not found`). Editors must keep LF.
- The repo is checked out with `core.symlinks=false`; the two
  `api/native/rnnoise/librnnoise.so*` symlinks are 19-byte text files. Harmless
  (that loader path is unused) — see the note in the change log.

---

## 9. Verification evidence (2026-08-23)

| Scenario | Outcome |
| --- | --- |
| Config save via API with real keys | HTTP 200; live checks `GET api.soniox.com/v1/tts-models` → 200, `GET api.groq.com/openai/v1/models` → 200; config reloads from DB with 4 Groq keys |
| Soniox STT + Groq + Speaches Kokoro (English) | transcript correct except the proper noun "Dograh" → "Dagra"; Groq replied; 1.8 s audio |
| + keyterms `Dograh,Soniox` sent as Soniox `context.terms` | accepted (proper noun still phonetic — Kokoro's pronunciation limits it) |
| Hindi utterance, `language=auto` | tagged `lang=hi`, transcript correct, endpoint 0.3 s after the question |
| Soniox TTS @16 kHz and @8 kHz | first audio 0.35–0.6 s after the LLM's first sentence; word-level `TTSTextFrame`s present |
| Barge-in during a long reply | stream #1's audio stopped ≤0.1 s after the `InterruptionFrame`; next turn opened a fresh stream and finished |
| Unit tests | 32/32 Soniox tests pass; 178 neighbouring tests unchanged |

---

## 10. Open items / next steps

- **Real browser or phone call** with Soniox TTS selected (transport pacing,
  `BotStartedSpeaking`) — the only path not exercised headlessly.
- Regenerate the cosmetic artefacts `docs/api-reference/openapi.json`
  (`python -m scripts.dump_docs_openapi`, needs the dev DB env) and
  `ui/src/client/types.gen.ts` (`scripts/generate_sdk.sh`) so the TypeScript
  types know about `soniox`. No runtime effect.
- Optional: expose Soniox regional endpoints (EU/JP) as a config field; copy the
  stock database over (§6) if the owner wants their existing agents here.
- If contributing upstream: dograh-hq requires live-integration evidence per
  `CONTRIBUTING.md` ("AI Provider Integration Pull Requests"); §9 plus a
  screen-recorded call would satisfy it.

---

## 11. Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| `dograh-custom-api-1 exited (127)`, logs say `env: 'bash\r'` | CRLF line endings in `scripts/*.sh`. `git config core.autocrlf false`, re-checkout (`git rm -r --cached . && git reset --hard`), rebuild |
| Build fails at `uv pip install` / `npm ci` with DNS or "failed to fetch" | transient Docker Desktop build-network issue — rerun, one build at a time |
| `port is already allocated` | stock stack owns 8000/3010/5432/9000/9001/16379/2000; this stack uses +10000 (Redis 26379). Check `docker ps` for leftovers |
| UI loads but API calls fail in the browser | `BACKEND_API_ENDPOINT` in `.env` must be `http://localhost:18000` (reported by `/health`) |
| Recordings don't play | `MINIO_PUBLIC_ENDPOINT` must be `http://localhost:19000` |
| "Invalid soniox API key" on save | key rejected with 401/403 by Soniox; network errors surface as "Could not connect" and do not block |
| Telephony webhooks unreachable | quick-tunnel URL changed after restart — re-copy from `docker compose logs cloudflared` or set a named tunnel |
| Soniox turns feel slow | lower `max_endpoint_delay_ms` (Transcriber tab), 1000–1200 works well |
| After `git merge upstream/main` the API won't start | run `git submodule update --init --recursive` and rebuild; check `alembic` logs in `docker compose logs api` |

---

## 12. References

- Change log for our code: [CHANGES-SONIOX-INTEGRATION.md](CHANGES-SONIOX-INTEGRATION.md)
- Upstream docs: https://docs.dograh.com (deployment: `/deployment/docker`, contributing: `/contribution/setup`)
- Soniox docs: STT WebSocket https://soniox.com/docs/stt/api-reference/websocket-api · TTS WebSocket https://soniox.com/docs/api-reference/tts/websocket-api · models https://soniox.com/docs/stt/models, https://soniox.com/docs/tts/models · console https://console.soniox.com
- Our fork / branch: https://github.com/Samarth-8836/dograh/tree/soniox-integration
- Closed upstream attempt for context: https://github.com/dograh-hq/dograh/pull/649
