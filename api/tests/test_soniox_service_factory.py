"""Soniox STT/TTS provider: configuration, persistence round-trip and factory wiring."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pipecat.services.settings import NOT_GIVEN
from pipecat.services.soniox.stt import SonioxContextObject
from pipecat.transcriptions.language import Language

from api.schemas.ai_model_configuration import (
    BYOKPipelineAIModelConfiguration,
    EffectiveAIModelConfiguration,
)
from api.services.configuration.check_validity import UserConfigurationValidator
from api.services.configuration.options import (
    SONIOX_STT_LANGUAGES,
    SONIOX_STT_MODELS,
    SONIOX_TTS_LANGUAGES,
    SONIOX_TTS_MODELS,
    SONIOX_TTS_VOICES,
)
from api.services.configuration.registry import (
    REGISTRY,
    OpenAILLMService,
    ServiceProviders,
    ServiceType,
    SonioxSTTConfiguration,
    SonioxTTSConfiguration,
)
from api.services.pipecat.audio_config import AudioConfig
from api.services.pipecat.service_factory import (
    create_stt_service,
    create_tts_service,
    stt_uses_external_turns,
)


def _audio_config(rate: int = 16000) -> AudioConfig:
    return AudioConfig(transport_in_sample_rate=rate, transport_out_sample_rate=rate)


def _stt_config(**overrides) -> SimpleNamespace:
    fields = {
        "provider": ServiceProviders.SONIOX.value,
        "api_key": "test-key",
        "model": "stt-rt-v5",
        "language": "auto",
        "turn_detection": "soniox",
        "max_endpoint_delay_ms": 2000,
    }
    fields.update(overrides)
    return SimpleNamespace(stt=SimpleNamespace(**fields))


def _tts_config(**overrides) -> SimpleNamespace:
    fields = {
        "provider": ServiceProviders.SONIOX.value,
        "api_key": "test-key",
        "model": "tts-rt-v2",
        "voice": "Adrian",
        "language": "en",
        "speed": 1.0,
    }
    fields.update(overrides)
    return SimpleNamespace(tts=SimpleNamespace(**fields))


# ---------------------------------------------------------------------------
# Registry / schema
# ---------------------------------------------------------------------------


class TestSonioxConfiguration:
    def test_stt_defaults_and_catalog(self):
        config = SonioxSTTConfiguration(api_key="test-key")
        assert config.provider == ServiceProviders.SONIOX
        assert config.model == "stt-rt-v5"
        assert config.language == "auto"
        assert config.turn_detection == "soniox"
        assert config.max_endpoint_delay_ms == 2000
        assert SONIOX_STT_MODELS[0] == "stt-rt-v5"
        assert SONIOX_STT_LANGUAGES[0] == "auto"
        for code in ("en", "hi", "gu", "bn", "ta", "te"):
            assert code in SONIOX_STT_LANGUAGES

    def test_tts_defaults_and_catalog(self):
        config = SonioxTTSConfiguration(api_key="test-key")
        assert config.provider == ServiceProviders.SONIOX
        assert config.model == "tts-rt-v2"
        assert config.voice == "Adrian"
        assert config.language == "en"
        assert config.speed == 1.0
        assert SONIOX_TTS_MODELS == ("tts-rt-v2",)
        assert "Adrian" in SONIOX_TTS_VOICES
        assert "Priya" in SONIOX_TTS_VOICES
        assert "gu" in SONIOX_TTS_LANGUAGES

    def test_registered_for_stt_and_tts(self):
        assert REGISTRY[ServiceType.STT]["soniox"] is SonioxSTTConfiguration
        assert REGISTRY[ServiceType.TTS]["soniox"] is SonioxTTSConfiguration

    def test_schema_exposes_ui_options(self):
        stt_schema = SonioxSTTConfiguration.model_json_schema()
        assert stt_schema["title"] == "Soniox"
        assert stt_schema["provider_docs_url"].startswith("https://soniox.com")
        assert stt_schema["properties"]["language"]["allow_custom_input"] is True
        assert "auto" in stt_schema["properties"]["language"]["examples"]
        assert stt_schema["properties"]["turn_detection"]["enum"] == ["soniox", "vad"]

        tts_schema = SonioxTTSConfiguration.model_json_schema()
        voice_schema = tts_schema["properties"]["voice"]
        assert voice_schema["allow_custom_input"] is True
        assert "Adrian" in voice_schema["examples"]
        assert tts_schema["properties"]["speed"]["minimum"] == 0.7
        assert tts_schema["properties"]["speed"]["maximum"] == 1.3

    def test_tts_speed_is_range_checked(self):
        with pytest.raises(ValueError):
            SonioxTTSConfiguration(api_key="test-key", speed=2.0)

    def test_stt_turn_detection_is_restricted(self):
        with pytest.raises(ValueError):
            SonioxSTTConfiguration(api_key="test-key", turn_detection="magic")


# ---------------------------------------------------------------------------
# Persistence round-trip (the discriminated unions must know the provider)
# ---------------------------------------------------------------------------


def test_soniox_configuration_survives_save_and_reload():
    payload = {
        "llm": {"provider": "openai", "api_key": "sk-test", "model": "gpt-4o-mini"},
        "stt": {
            "provider": "soniox",
            "api_key": "sx-test",
            "model": "stt-rt-v5",
            "language": "hi",
            "turn_detection": "vad",
        },
        "tts": {
            "provider": "soniox",
            "api_key": "sx-test",
            "model": "tts-rt-v2",
            "voice": "Priya",
            "language": "hi",
            "speed": 1.1,
        },
    }

    byok = BYOKPipelineAIModelConfiguration.model_validate(payload)
    assert isinstance(byok.stt, SonioxSTTConfiguration)
    assert isinstance(byok.tts, SonioxTTSConfiguration)

    dumped = byok.model_dump(mode="json", exclude_none=True)
    reloaded = EffectiveAIModelConfiguration.model_validate(dumped)
    assert isinstance(reloaded.stt, SonioxSTTConfiguration)
    assert reloaded.stt.language == "hi"
    assert reloaded.stt.turn_detection == "vad"
    assert isinstance(reloaded.tts, SonioxTTSConfiguration)
    assert reloaded.tts.voice == "Priya"
    assert reloaded.tts.speed == 1.1

    # The reloaded effective config is what the factory consumes.
    with patch("api.services.pipecat.service_factory.SonioxSTTService") as stt_service:
        create_stt_service(reloaded, _audio_config())
    assert stt_service.call_args.kwargs["api_key"] == "sx-test"

    with patch("api.services.pipecat.service_factory.SonioxTTSService") as tts_service:
        create_tts_service(reloaded, _audio_config())
    assert tts_service.call_args.kwargs["settings"].voice == "Priya"


# ---------------------------------------------------------------------------
# Credential validation
# ---------------------------------------------------------------------------


class TestSonioxValidator:
    def test_validator_is_registered(self):
        validator = UserConfigurationValidator()
        assert ServiceProviders.SONIOX.value in validator._validator_map

    def test_accepts_key_when_soniox_answers_200(self):
        validator = UserConfigurationValidator()
        with patch("api.services.configuration.check_validity.httpx.get") as get:
            get.return_value = SimpleNamespace(status_code=200)
            assert validator._check_soniox_api_key("stt-rt-v5", "good-key") is True
        headers = get.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer good-key"

    def test_rejects_key_on_401(self):
        validator = UserConfigurationValidator()
        with patch("api.services.configuration.check_validity.httpx.get") as get:
            get.return_value = SimpleNamespace(status_code=401)
            with pytest.raises(ValueError, match="Invalid Soniox API key"):
                validator._check_soniox_api_key("stt-rt-v5", "bad-key")

    def test_inconclusive_statuses_do_not_block_save(self):
        validator = UserConfigurationValidator()
        with patch("api.services.configuration.check_validity.httpx.get") as get:
            get.return_value = SimpleNamespace(status_code=503)
            assert validator._check_soniox_api_key("stt-rt-v5", "some-key") is True

    def test_full_pipeline_validation_passes_for_soniox(self):
        validator = UserConfigurationValidator()
        config = EffectiveAIModelConfiguration(
            llm=OpenAILLMService(api_key="sk-test", model="gpt-4o-mini"),
            stt=SonioxSTTConfiguration(api_key="sx-test"),
            tts=SonioxTTSConfiguration(api_key="sx-test"),
        )
        # _validator_map holds bound methods captured at __init__, so stub the
        # HTTP layer rather than the method.
        with patch("api.services.configuration.check_validity.httpx.get") as get:
            get.return_value = SimpleNamespace(status_code=200)
            assert validator._validate_service(config.stt, "stt") == []
            assert validator._validate_service(config.tts, "tts") == []
        assert get.call_count == 2

        with patch("api.services.configuration.check_validity.httpx.get") as get:
            get.return_value = SimpleNamespace(status_code=401)
            errors = validator._validate_service(config.stt, "stt")
        assert errors and "Invalid Soniox API key" in errors[0]["message"]


# ---------------------------------------------------------------------------
# STT factory
# ---------------------------------------------------------------------------


class TestSonioxSTTServiceFactory:
    def test_soniox_turn_detection_is_external_by_default(self):
        assert stt_uses_external_turns(_stt_config()) is True
        assert stt_uses_external_turns(_stt_config(turn_detection="vad")) is False

    def test_auto_language_enables_identification_and_soniox_endpointing(self):
        with patch("api.services.pipecat.service_factory.SonioxSTTService") as svc:
            create_stt_service(_stt_config(), _audio_config(8000))

        svc.assert_called_once()
        kwargs = svc.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["sample_rate"] == 8000
        assert kwargs["vad_force_turn_endpoint"] is False
        assert kwargs["should_interrupt"] is False
        settings = kwargs["settings"]
        assert settings.model == "stt-rt-v5"
        assert settings.language_hints is None
        assert settings.enable_language_identification is True
        assert settings.max_endpoint_delay_ms == 2000
        assert settings.context is NOT_GIVEN

    @pytest.mark.parametrize(
        "language,expected_hint",
        [
            ("hi", Language.HI),
            ("gu", Language.GU),
            ("en", Language.EN),
            ("en-IN", Language.EN_IN),
        ],
    )
    def test_language_becomes_hint(self, language, expected_hint):
        with patch("api.services.pipecat.service_factory.SonioxSTTService") as svc:
            create_stt_service(_stt_config(language=language), _audio_config())

        settings = svc.call_args.kwargs["settings"]
        assert settings.language_hints == [expected_hint]
        assert settings.enable_language_identification is False

    @pytest.mark.parametrize(
        "language", ["auto", "unknown", "multi", "", None, "zz-not-a-code"]
    )
    def test_unhinted_languages_fall_back_to_identification(self, language):
        with patch("api.services.pipecat.service_factory.SonioxSTTService") as svc:
            create_stt_service(_stt_config(language=language), _audio_config())

        settings = svc.call_args.kwargs["settings"]
        assert settings.language_hints is None
        assert settings.enable_language_identification is True

    def test_vad_mode_disables_soniox_endpointing(self):
        with patch("api.services.pipecat.service_factory.SonioxSTTService") as svc:
            create_stt_service(
                _stt_config(turn_detection="vad", max_endpoint_delay_ms=900),
                _audio_config(),
            )

        kwargs = svc.call_args.kwargs
        assert kwargs["vad_force_turn_endpoint"] is True
        # Endpoint tuning is meaningless when Soniox endpointing is off.
        assert kwargs["settings"].max_endpoint_delay_ms is NOT_GIVEN

    def test_keyterms_are_sent_as_context_terms(self):
        with patch("api.services.pipecat.service_factory.SonioxSTTService") as svc:
            create_stt_service(
                _stt_config(), _audio_config(), keyterms=["Dograh", "Soniox"]
            )

        context = svc.call_args.kwargs["settings"].context
        assert isinstance(context, SonioxContextObject)
        assert context.terms == ["Dograh", "Soniox"]

    def test_config_without_turn_fields_uses_soniox_turns(self):
        # Older saved configs (or SimpleNamespace fixtures) may predate the
        # turn_detection / max_endpoint_delay_ms fields.
        user_config = SimpleNamespace(
            stt=SimpleNamespace(
                provider=ServiceProviders.SONIOX.value,
                api_key="test-key",
                model="stt-rt-v5",
                language="auto",
            )
        )
        assert stt_uses_external_turns(user_config) is True
        with patch("api.services.pipecat.service_factory.SonioxSTTService") as svc:
            create_stt_service(user_config, _audio_config())
        kwargs = svc.call_args.kwargs
        assert kwargs["vad_force_turn_endpoint"] is False
        assert kwargs["settings"].max_endpoint_delay_ms is NOT_GIVEN


# ---------------------------------------------------------------------------
# TTS factory
# ---------------------------------------------------------------------------


class TestSonioxTTSServiceFactory:
    def test_defaults(self):
        with patch("api.services.pipecat.service_factory.SonioxTTSService") as svc:
            create_tts_service(_tts_config(), _audio_config(8000))

        svc.assert_called_once()
        kwargs = svc.call_args.kwargs
        assert kwargs["api_key"] == "test-key"
        assert kwargs["sample_rate"] == 8000
        assert kwargs["silence_time_s"] == 1.0
        assert kwargs["skip_aggregator_types"] == ["recording_router", "recording"]
        assert len(kwargs["text_filters"]) == 1
        settings = kwargs["settings"]
        assert settings.model == "tts-rt-v2"
        assert settings.voice == "Adrian"
        assert settings.language == Language.EN
        assert settings.speed is NOT_GIVEN

    def test_indian_language_voice_and_speed(self):
        with patch("api.services.pipecat.service_factory.SonioxTTSService") as svc:
            create_tts_service(
                _tts_config(voice="  Priya ", language="gu", speed=1.2),
                _audio_config(16000),
            )

        kwargs = svc.call_args.kwargs
        assert kwargs["sample_rate"] == 16000
        settings = kwargs["settings"]
        assert settings.voice == "Priya"
        assert settings.language == Language.GU
        assert settings.speed == 1.2

    def test_blank_voice_and_language_fall_back_to_defaults(self):
        with patch("api.services.pipecat.service_factory.SonioxTTSService") as svc:
            create_tts_service(_tts_config(voice="   ", language=""), _audio_config())

        settings = svc.call_args.kwargs["settings"]
        assert settings.voice == "Adrian"
        assert settings.language == Language.EN

    def test_unknown_language_code_passes_through(self):
        with patch("api.services.pipecat.service_factory.SonioxTTSService") as svc:
            create_tts_service(_tts_config(language="xx-custom"), _audio_config())

        assert svc.call_args.kwargs["settings"].language == "xx-custom"

    def test_cloned_voice_uuid_passes_through(self):
        voice_id = "4b1f2c3d-1111-2222-3333-444455556666"
        with patch("api.services.pipecat.service_factory.SonioxTTSService") as svc:
            create_tts_service(_tts_config(voice=voice_id), _audio_config())

        assert svc.call_args.kwargs["settings"].voice == voice_id
