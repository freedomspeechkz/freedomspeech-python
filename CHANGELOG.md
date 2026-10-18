# Changelog

## 0.1.1 (2026-10-18)

- Адрес API по умолчанию: `https://api.freedomspeech.kz`. На нём работают ключи из кабинета console.freedomspeech.kz (раньше по умолчанию был `https://freedomspeech.kz`, где такие ключи получали 401). / Default base URL is now `https://api.freedomspeech.kz`, where console keys work.
- На новом адресе опубликованы распознавание, синтез и оба WebSocket. Клонирование, voice design, перевод, каталог голосов и health там пока не опубликованы (404), ключам кабинета недоступны; пометки в докстрингах. `base_url` и `FS_BASE_URL` переопределяют адрес. / Cloning, voice design, translation, voice catalog and health are not yet published on the new host; override with `base_url` or `FS_BASE_URL`.

## 0.1.0 (2026-10-17)

Первый выпуск. / First release.

- `FreedomSpeech` client: `X-API-Key` auth, key from `FS_API_KEY`, optional `Authorization: Bearer`.
- `transcribe()`: `POST /v1/audio/transcriptions` with `prettify`, `response_format=diarized_json`, `known_speaker_names[]`.
- `stream_transcribe()` and `asr_stream()`: `WS /ws/asr`, PCM16 16 kHz frames, `partial` / `final` events, `stop`.
- `speak()`: `POST /v1/audio/speech`, WAV 24 kHz.
- `stream_speech()` and `tts_stream()`: `WS /ws/tts`, PCM16 chunks, `freedomspeech-flash`, queue and cancel.
- `clone_voice()`, `clone_voice_variants()`: `POST /api/voice-clone` (reference audio or `voice_id`, `speed`, `duration`, `?variants=all`).
- `design_voice()`: `POST /api/voice-design`.
- `voices()`, `voice_preview()`: `GET /api/voices`, `GET /api/voices/{id}/preview`.
- `translate()`, `detect_language()`, `languages()`: `/v2/translate`, `/v2/detect`, `/v2/languages`.
- `health()`: `GET /api/asr/health`.
- Audio helpers: `pcm16_to_wav`, `wav_to_pcm16`, `pcm16_frames`.
- CLI `fs`: `transcribe`, `speak`, `clone`, `voices`, `translate`, `health`.

## Проверка на живом API, 18.10.2026 00:30 Алматы
health, voices, transcribe (HTTP), stream_transcribe (WS, partial и final), translate, detect_language, stream_speech (WS, flash): работают. speak (HTTP POST /v1/audio/speech): сервер отвечает HTTP 500 «TTS failed», это известная проблема прода с 15.10.2026, не SDK; SDK отдаёт FreedomSpeechError со статусом 500. Публикация на PyPI после появления аккаунта.
