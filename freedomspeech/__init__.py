"""freedomspeech: Python SDK для API Freedom Speech (казахская, русская и английская речь).

Python SDK for the Freedom Speech API: speech recognition, synthesis, voice cloning, translation.

    from freedomspeech import FreedomSpeech
    fs = FreedomSpeech()                       # ключ из FS_API_KEY
    print(fs.transcribe("call.mp3").text)
    open("hi.wav", "wb").write(fs.speak("Сәлем", voice="tomiris"))

Документация API: https://freedomspeech.kz/docs
"""
from ._version import __version__
from .audio import (ASR_SAMPLE_RATE, TTS_SAMPLE_RATE, pcm16_duration, pcm16_frames, pcm16_to_wav,
                    silence, wav_duration, wav_to_pcm16)
from .client import DEFAULT_BASE_URL, ENV_API_KEY, FreedomSpeech
from .errors import AuthenticationError, ConnectionFailed, FreedomSpeechError, StreamError
from .models import (CloneVariant, Detection, Segment, Transcription, TranscriptEvent, Translation,
                     Voice, VoiceDesign)
from .streaming import ASRStream, TTSStream

__all__ = [
    "__version__", "FreedomSpeech", "DEFAULT_BASE_URL", "ENV_API_KEY",
    "FreedomSpeechError", "AuthenticationError", "StreamError", "ConnectionFailed",
    "Transcription", "Segment", "TranscriptEvent", "Voice", "CloneVariant", "VoiceDesign",
    "Translation", "Detection", "ASRStream", "TTSStream",
    "ASR_SAMPLE_RATE", "TTS_SAMPLE_RATE", "pcm16_to_wav", "wav_to_pcm16", "pcm16_frames",
    "pcm16_duration", "wav_duration", "silence",
]
