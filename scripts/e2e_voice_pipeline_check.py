"""Headless end-to-end check: Soniox STT -> Groq LLM -> Speaches TTS via Dograh's factories.

Runs inside the dograh-custom api container. Loads the organisation's saved v2
model configuration from the DB (the same path a real call uses), builds the
services through api.services.pipecat.service_factory, streams a pre-recorded
utterance into the STT in real time, and reports the transcript, the LLM reply
and the TTS audio produced.
"""

import asyncio
import json
import os
import sys
import time
import wave

from loguru import logger

logger.remove()
logger.add(
    sys.stderr,
    level="DEBUG",
    filter=lambda r: (
        r["level"].no >= 30
        or (
            os.environ.get("E2E_DEBUG") == "1"
            and any(
                k in r["name"]
                for k in ("soniox", "tts_service", "llm_response_universal")
            )
        )
    ),
)

from api.schemas.ai_model_configuration import compile_ai_model_configuration_v2  # noqa: E402
from api.services.configuration.ai_model_configuration import (  # noqa: E402
    get_organization_ai_model_configuration_v2,
)
from api.services.pipecat.audio_config import AudioConfig  # noqa: E402
from api.services.pipecat.service_factory import (  # noqa: E402
    create_llm_service,
    create_stt_service,
    create_tts_service,
    stt_uses_external_turns,
)
from pipecat.audio.resamplers.soxr_resampler import SOXRAudioResampler  # noqa: E402
from pipecat.frames.frames import (  # noqa: E402
    EndFrame,
    ErrorFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline  # noqa: E402
from pipecat.pipeline.runner import PipelineRunner  # noqa: E402
from pipecat.pipeline.task import PipelineParams, PipelineTask  # noqa: E402
from pipecat.processors.aggregators.llm_context import LLMContext  # noqa: E402
from pipecat.processors.aggregators.llm_response_universal import (  # noqa: E402
    LLMAssistantAggregatorParams,
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor  # noqa: E402
from pipecat.turns.user_start import ExternalUserTurnStartStrategy  # noqa: E402
from pipecat.turns.user_stop import ExternalUserTurnStopStrategy  # noqa: E402
from pipecat.turns.user_turn_strategies import UserTurnStrategies  # noqa: E402

ORG_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 1
WAV_PATH = sys.argv[2] if len(sys.argv) > 2 else "/tmp/test_utterance.wav"
IN_RATE = int(os.environ.get("E2E_RATE", "16000"))  # 16000 = WebRTC, 8000 = telephony
INTERRUPT = os.environ.get("E2E_INTERRUPT") == "1"  # barge in while the bot is speaking
KEYTERMS = [k for k in (sys.argv[3].split(",") if len(sys.argv) > 3 else []) if k]
SYSTEM_PROMPT = (
    "You are a voice assistant for a test call. Reply with four long, detailed "
    "sentences about the weather and mention the word 'confirmed'."
    if os.environ.get("E2E_LONG_REPLY") == "1"
    else "You are a concise voice assistant for a test call. Reply in one short sentence "
    "and mention the word 'confirmed'."
)


class State:
    """Shared recording state fed by the Tap processors."""

    def __init__(self):
        self.events: list[tuple[float, str]] = []
        self.transcripts: list[str] = []
        self.interims = 0
        self.llm_text: list[str] = []
        self.tts_bytes = 0
        self.tts_rate = None
        self.tts_words = 0
        self.tts_starts = 0
        self.tts_stops = 0
        self.tts_started = asyncio.Event()
        self.contexts: dict[
            str, dict
        ] = {}  # context_id -> {bytes, first, last, stopped}
        self.done = asyncio.Event()
        self.t0 = time.monotonic()

    def _mark(self, label: str):
        self.events.append((round(time.monotonic() - self.t0, 2), label))

    async def observe(self, frame: Frame, tap: str):
        if isinstance(frame, ErrorFrame):
            self._mark(f"[{tap}] ERROR: {frame.error}")
        elif isinstance(frame, InterruptionFrame):
            self._mark(f"[{tap}] InterruptionFrame")
        elif tap == "after_stt":
            if isinstance(frame, UserStartedSpeakingFrame):
                self._mark("UserStartedSpeaking (emitted by Soniox STT)")
            elif isinstance(frame, UserStoppedSpeakingFrame):
                self._mark("UserStoppedSpeaking (Soniox endpoint detected)")
            elif isinstance(frame, InterimTranscriptionFrame):
                self.interims += 1
            elif isinstance(frame, TranscriptionFrame):
                self.transcripts.append(frame.text)
                self._mark(
                    f"TranscriptionFrame finalized={frame.finalized} "
                    f"lang={frame.language}: {frame.text!r}"
                )
        elif tap == "after_llm":
            if isinstance(frame, LLMTextFrame):
                self.llm_text.append(frame.text)
            elif isinstance(frame, LLMFullResponseEndFrame):
                reply = "".join(self.llm_text).strip()
                self._mark(
                    f"LLM reply complete ({len(reply)} chars): {reply[:90]!r}..."
                )
        elif tap == "after_tts":
            if isinstance(frame, TTSStartedFrame):
                self.tts_starts += 1
                self._mark(f"TTSStarted #{self.tts_starts}")
            elif isinstance(frame, TTSAudioRawFrame):
                ctx = self.contexts.setdefault(
                    frame.context_id,
                    {"bytes": 0, "first": None, "last": None, "stopped": False},
                )
                now = round(time.monotonic() - self.t0, 2)
                if ctx["first"] is None:
                    ctx["first"] = now
                    self._mark(
                        f"first TTS audio frame for stream #{len(self.contexts)} "
                        f"({frame.sample_rate} Hz)"
                    )
                ctx["last"] = now
                ctx["bytes"] += len(frame.audio)
                self.tts_bytes += len(frame.audio)
                self.tts_rate = frame.sample_rate
                self.tts_started.set()
            elif isinstance(frame, TTSTextFrame):
                self.tts_words += 1
            elif isinstance(frame, TTSStoppedFrame):
                self.tts_stops += 1
                if frame.context_id in self.contexts:
                    self.contexts[frame.context_id]["stopped"] = True
                self._mark(
                    f"TTSStopped #{self.tts_stops}: {self.tts_bytes} bytes @ {self.tts_rate} Hz "
                    f"(~{self.tts_bytes / 2 / (self.tts_rate or 1):.1f}s audio so far), "
                    f"{self.tts_words} TTSTextFrames"
                )
                self.done.set()


class Tap(FrameProcessor):
    """Pass-through processor recording frames into a shared State."""

    def __init__(self, state: State, name: str):
        super().__init__(name=name)
        self.state = state

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.state.observe(frame, self.name)
        await self.push_frame(frame, direction)


async def load_audio_16k() -> bytes:
    with wave.open(WAV_PATH, "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, (
            "expected mono 16-bit PCM"
        )
        rate = w.getframerate()
        pcm = w.readframes(w.getnframes())
    if rate != IN_RATE:
        pcm = await SOXRAudioResampler().resample(pcm, rate, IN_RATE)
    return pcm


async def main():
    stored = await get_organization_ai_model_configuration_v2(ORG_ID)
    effective = compile_ai_model_configuration_v2(stored)
    print(
        f"config: stt={effective.stt.provider}/{effective.stt.model} "
        f"turn_detection={getattr(effective.stt, 'turn_detection', None)} | "
        f"llm={effective.llm.provider}/{effective.llm.model} "
        f"({len(effective.llm.get_all_api_keys())} keys) | "
        f"tts={effective.tts.provider}/{effective.tts.model} voice={effective.tts.voice}"
    )

    audio_config = AudioConfig(
        transport_in_sample_rate=IN_RATE, transport_out_sample_rate=IN_RATE
    )
    external = stt_uses_external_turns(effective)
    stt = create_stt_service(effective, audio_config, keyterms=KEYTERMS or None)
    print(f"keyterms -> Soniox context.terms: {KEYTERMS}")
    llm = create_llm_service(effective)
    tts = create_tts_service(effective, audio_config)
    print(
        f"services: {type(stt).__name__} (external_turns={external}), "
        f"{type(llm).__name__}, {type(tts).__name__}"
    )

    context = LLMContext(messages=[{"role": "system", "content": SYSTEM_PROMPT}])
    strategies = (
        UserTurnStrategies(
            start=[ExternalUserTurnStartStrategy(enable_interruptions=True)],
            stop=[ExternalUserTurnStopStrategy()],
        )
        if external
        else None
    )
    user_params = (
        LLMUserAggregatorParams(
            user_turn_strategies=strategies, user_turn_stop_timeout=30.0
        )
        if strategies
        else LLMUserAggregatorParams()
    )
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=user_params,
        assistant_params=LLMAssistantAggregatorParams(),
    )

    recorder = State()
    pipeline = Pipeline(
        [
            stt,
            Tap(recorder, "after_stt"),
            aggregators.user(),
            llm,
            Tap(recorder, "after_llm"),
            tts,
            Tap(recorder, "after_tts"),
            aggregators.assistant(),
        ]
    )
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=IN_RATE, audio_out_sample_rate=IN_RATE
        ),
        cancel_on_idle_timeout=False,
    )
    runner = PipelineRunner(handle_sigint=False)

    pcm = await load_audio_16k()
    chunk = int(IN_RATE * 0.02) * 2  # 20 ms of 16-bit mono
    silence = b"\x00" * chunk

    async def feed():
        await asyncio.sleep(1.0)  # let the websocket connect
        recorder.t0 = time.monotonic()
        recorder._mark(f"streaming {len(pcm) / 2 / IN_RATE:.2f}s of audio at real time")
        for i in range(0, len(pcm), chunk):
            await task.queue_frame(
                InputAudioRawFrame(
                    audio=pcm[i : i + chunk], sample_rate=IN_RATE, num_channels=1
                )
            )
            await asyncio.sleep(0.02)
        recorder._mark("audio finished; streaming silence")

        async def stream_silence_until(pred, label: str, limit: float = 40):
            deadline = time.monotonic() + limit
            while not pred() and time.monotonic() < deadline:
                await task.queue_frame(
                    InputAudioRawFrame(
                        audio=silence, sample_rate=IN_RATE, num_channels=1
                    )
                )
                await asyncio.sleep(0.02)
            if not pred():
                recorder._mark(f"TIMEOUT waiting for {label}")

        if INTERRUPT:
            await stream_silence_until(recorder.tts_started.is_set, "first TTS audio")
            await asyncio.sleep(float(os.environ.get("E2E_BARGE_DELAY", "0.4")))
            recorder._mark("BARGE-IN: user speaks again while bot audio is playing")
            for i in range(0, len(pcm), chunk):
                await task.queue_frame(
                    InputAudioRawFrame(
                        audio=pcm[i : i + chunk], sample_rate=IN_RATE, num_channels=1
                    )
                )
                await asyncio.sleep(0.02)
            recorder._mark("barge-in audio finished; streaming silence")
            # An interrupted stream never emits TTSStoppedFrame (the base class
            # reconnects the socket), so wait for a *second* stream to stop.
            await stream_silence_until(
                lambda: (
                    len(recorder.contexts) >= 2
                    and list(recorder.contexts.values())[-1]["stopped"]
                ),
                "second TTS stream",
                limit=90,
            )
        else:
            await stream_silence_until(recorder.done.is_set, "TTS")
        await asyncio.sleep(0.5)
        await task.queue_frame(EndFrame())

    feeder = asyncio.create_task(feed())
    await runner.run(task)
    await feeder

    print("\ntimeline:")
    for t, label in recorder.events:
        print(f"  {t:6.2f}s  {label}")
    print(
        f"\ninterim transcription frames: {recorder.interims} | TTS starts/stops: "
        f"{recorder.tts_starts}/{recorder.tts_stops} | word-level TTSTextFrames: "
        f"{recorder.tts_words}"
    )
    streams = []
    for i, (cid, c) in enumerate(recorder.contexts.items(), 1):
        secs = c["bytes"] / 2 / (recorder.tts_rate or 1)
        streams.append(
            {
                "stream": i,
                "audio_s": round(secs, 1),
                "first_audio_at": c["first"],
                "last_audio_at": c["last"],
                "stopped": c["stopped"],
            }
        )
        state = "terminated normally" if c["stopped"] else "no TTSStopped (interrupted)"
        print(
            f"stream #{i} ({cid[:8]}…): {secs:.1f}s audio, frames {c['first']}s → "
            f"{c['last']}s, {state}"
        )
    result = {
        "streams": streams,
        "transcript": " ".join(recorder.transcripts).strip(),
        "llm_reply": "".join(recorder.llm_text).strip(),
        "tts_bytes": recorder.tts_bytes,
        "tts_rate": recorder.tts_rate,
        "transcripts": len(recorder.transcripts),
        "tts_stops": recorder.tts_stops,
        "ok": bool(recorder.transcripts)
        and bool(recorder.llm_text)
        and recorder.tts_bytes > 0
        and (
            not INTERRUPT
            or (
                len(recorder.transcripts) >= 2
                and len(streams) >= 2
                and not streams[0]["stopped"]
                and streams[-1]["stopped"]
            )
        ),
    }
    print("\nRESULT " + json.dumps(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
