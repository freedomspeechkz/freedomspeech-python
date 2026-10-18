"""Команда ``fs``: распознать, озвучить, клонировать, перевести из терминала. / The ``fs`` command.

    fs transcribe call.mp3
    fs transcribe call.mp3 --diarize --names Agent Client --json
    fs transcribe mic.wav --stream              # WAV 16 кГц моно через WS /ws/asr
    fs speak "Сәлем" --voice tomiris -o out.wav
    fs speak "Сәлем" --voice tomiris --stream -o out.wav   # WS /ws/tts, freedomspeech-flash
    fs clone "Текст" --ref my_voice.wav -o cloned.wav
    fs voices
    fs translate "Привет" --to kk
    fs health

Ключ: ``--api-key`` или переменная окружения ``FS_API_KEY``.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Optional

from ._version import __version__
from .audio import pcm16_frames, pcm16_to_wav, wav_to_pcm16
from .client import DEFAULT_BASE_URL, FreedomSpeech
from .errors import FreedomSpeechError


def _client(args: argparse.Namespace) -> FreedomSpeech:
    return FreedomSpeech(api_key=args.api_key, base_url=args.base_url, timeout=args.timeout,
                         bearer_auth=args.bearer)


def _write(path: Optional[str], data: bytes) -> None:
    if path and path != "-":
        with open(path, "wb") as f:
            f.write(data)
        print("%s: %d bytes" % (path, len(data)), file=sys.stderr)
    else:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


def cmd_transcribe(args: argparse.Namespace) -> int:
    fs = _client(args)
    if args.stream:
        with open(args.file, "rb") as f:
            data = f.read()
        try:
            pcm = wav_to_pcm16(data)
        except Exception as exc:  # noqa: BLE001
            print("Для --stream нужен WAV 16 кГц, моно, 16 бит: %s\n"
                  "Конвертация: ffmpeg -i in.mp3 -ar 16000 -ac 1 out.wav" % exc, file=sys.stderr)
            return 2
        finals: List[str] = []
        for ev in fs.stream_transcribe(pcm16_frames(pcm), prettify=not args.raw, realtime=args.realtime):
            if args.json:
                print(json.dumps(ev.raw, ensure_ascii=False))
            elif ev.is_final:
                finals.append(ev.text)
                print(ev.text)
            else:
                print("  ... " + ev.text, file=sys.stderr)
        return 0
    result = fs.transcribe(args.file, prettify=not args.raw, diarize=args.diarize or bool(args.names),
                           speaker_names=args.names, language=args.language)
    if args.json:
        print(json.dumps(result.raw, ensure_ascii=False, indent=2))
    elif result.segments:
        for seg in result.segments:
            who = (seg.speaker + ": ") if seg.speaker else ""
            print("%s%s" % (who, seg.text))
    else:
        print(result.text)
    return 0


def cmd_speak(args: argparse.Namespace) -> int:
    fs = _client(args)
    if args.stream:
        model = args.model or "freedomspeech-flash"
        chunks = fs.stream_speech(args.text, args.voice, model=model, language=args.language,
                                  emotion=args.emotion)
        data = pcm16_to_wav(b"".join(chunks))
    else:
        data = fs.speak(args.text, args.voice, language=args.language, emotion=args.emotion, model=args.model)
    _write(args.output, data)
    return 0


def cmd_clone(args: argparse.Namespace) -> int:
    fs = _client(args)
    if args.variants:
        variants = fs.clone_voice_variants(args.text, reference_audio=args.ref, voice_id=args.voice_id,
                                           language=args.language, speed=args.speed, duration=args.duration)
        base = args.output or "cloned.wav"
        stem = base[:-4] if base.lower().endswith(".wav") else base
        for v in variants:
            _write("%s-%s.wav" % (stem, v.engine), v.wav)
        return 0
    data = fs.clone_voice(args.text, reference_audio=args.ref, voice_id=args.voice_id, language=args.language,
                          speed=args.speed, duration=args.duration)
    _write(args.output, data)
    return 0


def cmd_voices(args: argparse.Namespace) -> int:
    fs = _client(args)
    voices = fs.voices()
    if args.json:
        print(json.dumps([v.raw for v in voices], ensure_ascii=False, indent=2))
    else:
        for v in voices:
            print("%-32s %-28s %s" % (v.id, v.name, v.type or ""))
    return 0


def cmd_translate(args: argparse.Namespace) -> int:
    fs = _client(args)
    for t in fs.translate(args.text, args.to, source_lang=args.source):
        print(t.text)
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    fs = _client(args)
    print(json.dumps(fs.health(), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fs", description="Freedom Speech API: распознавание, синтез, клон голоса, перевод.")
    p.add_argument("--version", action="version", version="freedomspeech " + __version__)
    p.add_argument("--api-key", help="ключ API (по умолчанию переменная окружения FS_API_KEY)")
    p.add_argument("--base-url", default=None, help="адрес API, по умолчанию " + DEFAULT_BASE_URL)
    p.add_argument("--timeout", type=float, default=60.0, help="таймаут, секунды (60)")
    p.add_argument("--bearer", action="store_true", help="дополнительно слать Authorization: Bearer")
    sub = p.add_subparsers(dest="command", required=True)

    t = sub.add_parser("transcribe", help="распознать файл (POST /v1/audio/transcriptions)")
    t.add_argument("file")
    t.add_argument("--raw", action="store_true", help="prettify=false: сырой выход без форматирования чисел и дат")
    t.add_argument("--diarize", action="store_true", help="response_format=diarized_json")
    t.add_argument("--names", nargs="+", help="имена говорящих для диаризации")
    t.add_argument("--language", help="подсказка языка kk, ru, en (не описана в документации)")
    t.add_argument("--stream", action="store_true", help="гнать WAV 16 кГц моно через WS /ws/asr")
    t.add_argument("--realtime", action="store_true", help="со --stream: выдерживать темп живой речи")
    t.add_argument("--json", action="store_true", help="печатать JSON ответа")
    t.set_defaults(func=cmd_transcribe)

    s = sub.add_parser("speak", help="озвучить текст (POST /v1/audio/speech или WS /ws/tts)")
    s.add_argument("text")
    s.add_argument("--voice", default="tomiris", help="tomiris, taymas или id из fs voices")
    s.add_argument("--language", help="kk, ru, en")
    s.add_argument("--emotion", help="только tomiris: neutral, happy, sad, surprised, angry, fearful")
    s.add_argument("--model", help="freedomspeech или freedomspeech-flash (flash только со --stream)")
    s.add_argument("--stream", action="store_true", help="через WS /ws/tts, по умолчанию модель freedomspeech-flash")
    s.add_argument("-o", "--output", help="файл WAV (по умолчанию stdout)")
    s.set_defaults(func=cmd_speak)

    c = sub.add_parser("clone", help="озвучить текст голосом из записи (POST /api/voice-clone)")
    c.add_argument("text")
    c.add_argument("--ref", help="запись-референс, до 20 с")
    c.add_argument("--voice-id", help="вместо записи: id голоса каталога")
    c.add_argument("--language", help="kk, ru, en")
    c.add_argument("--speed", type=float, help="множитель темпа, 1.0 по умолчанию")
    c.add_argument("--duration", type=float, help="целевая длительность, с")
    c.add_argument("--variants", action="store_true", help="?variants=all: файл на каждый движок")
    c.add_argument("-o", "--output", help="файл WAV (по умолчанию stdout)")
    c.set_defaults(func=cmd_clone)

    v = sub.add_parser("voices", help="список голосов (GET /api/voices)")
    v.add_argument("--json", action="store_true")
    v.set_defaults(func=cmd_voices)

    tr = sub.add_parser("translate", help="перевести (POST /v2/translate)")
    tr.add_argument("text", nargs="+")
    tr.add_argument("--to", required=True, help="kk, ru, en")
    tr.add_argument("--source", help="язык источника, иначе автоопределение")
    tr.set_defaults(func=cmd_translate)

    h = sub.add_parser("health", help="состояние распознавания (GET /api/asr/health)")
    h.set_defaults(func=cmd_health)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FreedomSpeechError as exc:
        print("fs: %s" % exc, file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print("fs: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
