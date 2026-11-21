"""Text-to-speech subsystem with NVDA Controller Client DLL as primary backend."""

from audiofutbol.engine.tts.base import NullSpeechBackend, SpeechBackend
from audiofutbol.engine.tts.nvda import NvdaDllBackend
from audiofutbol.engine.tts.sapi import Sapi5Backend
from audiofutbol.engine.tts.service import SpeechService, create_default_speech_service

__all__ = [
    "NullSpeechBackend",
    "NvdaDllBackend",
    "Sapi5Backend",
    "SpeechBackend",
    "SpeechService",
    "create_default_speech_service",
]
