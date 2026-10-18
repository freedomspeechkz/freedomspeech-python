# freedomspeech

Python SDK для API [Freedom Speech](https://api.freedomspeech.kz): распознавание и синтез казахской, русской и английской речи, клонирование голоса, перевод. Документация API: <https://freedomspeech.kz/docs>.

Python SDK for the [Freedom Speech](https://api.freedomspeech.kz) API: Kazakh, Russian and English speech recognition and synthesis, voice cloning, translation. API reference: <https://freedomspeech.kz/docs>. English section below.

Python 3.9+. Зависимости: `requests`, `websockets`.

```bash
pip install freedomspeech
export FS_API_KEY=...        # ключ выдаётся через форму на сайте
```

## Быстрый старт

Распознать файл:

```python
from freedomspeech import FreedomSpeech
fs = FreedomSpeech()
print(fs.transcribe("call.mp3").text)
```

Озвучить текст:

```python
from freedomspeech import FreedomSpeech
wav = FreedomSpeech().speak("Сәлеметсіз бе, бұл сынақ.", voice="tomiris", language="kk")
open("hello.wav", "wb").write(wav)
```

Клонировать голос по записи:

```python
from freedomspeech import FreedomSpeech
wav = FreedomSpeech().clone_voice("Сәлеметсіз бе, қалыңыз қалай?", reference_audio="me.wav", language="kk")
open("cloned.wav", "wb").write(wav)
```

Из терминала:

```bash
fs transcribe call.mp3
fs speak "Сәлем" --voice tomiris -o out.wav
fs voices
```

## Что умеет

| Метод | Эндпоинт | Что возвращает |
|---|---|---|
| `transcribe(audio, prettify=True, diarize=False, speaker_names=None)` | `POST /v1/audio/transcriptions` | `Transcription`: `text`, `duration`, `processing_time`, `language`, `segments` |
| `stream_transcribe(pcm16_chunks)` | `WS /ws/asr` | генератор `TranscriptEvent` (`partial` / `final`) |
| `asr_stream()` | `WS /ws/asr` | `ASRStream` с `send`, `stop`, `recv`, `poll` для живого микрофона |
| `speak(text, voice, language=None, emotion=None)` | `POST /v1/audio/speech` | WAV байтами, 16 бит, моно, 24 кГц |
| `stream_speech(text, voice, model="freedomspeech-flash")` | `WS /ws/tts` | генератор кусков PCM16 24 кГц |
| `tts_stream()` | `WS /ws/tts` | `TTSStream`: несколько `synthesize` на одном соединении, `cancel` |
| `clone_voice(text, reference_audio=... или voice_id=..., speed=None, duration=None)` | `POST /api/voice-clone` | WAV байтами |
| `clone_voice_variants(...)` | `POST /api/voice-clone?variants=all` | список `CloneVariant(engine, wav)` |
| `design_voice(text, instruct, language=None)` | `POST /api/voice-design` | `VoiceDesign`: `reference_wav`, `variants` |
| `voices()`, `voice_preview(id)` | `GET /api/voices`, `GET /api/voices/{id}/preview` | список `Voice`, WAV байтами |
| `translate(text, target_lang, source_lang=None)` | `POST /v2/translate` | список `Translation` |
| `detect_language(text)`, `languages()` | `POST /v2/detect`, `GET /v2/languages` | список `Detection`, dict |
| `health()` | `GET /api/asr/health` | dict |

Вспомогательное: `pcm16_to_wav`, `wav_to_pcm16`, `pcm16_frames`, константы `ASR_SAMPLE_RATE` (16000) и `TTS_SAMPLE_RATE` (24000).

## Подробнее

### Клиент

```python
fs = FreedomSpeech(api_key=None, base_url="https://api.freedomspeech.kz", timeout=60)
```

Ключ из кабинета console.freedomspeech.kz работает на https://api.freedomspeech.kz, это адрес по умолчанию с версии 0.1.1. На этом адресе опубликованы распознавание (`transcribe`, `stream_transcribe`) и синтез (`speak`, `stream_speech`). Клонирование голоса, voice design, перевод, каталог голосов и `health` на нём пока не опубликованы (бэкенд в работе) и ключам кабинета недоступны; для другого адреса передайте `base_url` или задайте `FS_BASE_URL`.

Ключ берётся из аргумента или переменной окружения `FS_API_KEY`. HTTP-запросы идут с заголовком `X-API-Key`, WebSocket открывается с `?apiKey=`. Флаг `bearer_auth=True` дополнительно добавляет `Authorization: Bearer`; публичная документация описывает только `X-API-Key`, флаг нужен для прокси.

### Распознавание с говорящими

```python
r = fs.transcribe("call.wav", diarize=True, speaker_names=["Оператор", "Клиент"])
for seg in r.segments:
    print(f"{seg.start:.1f} {seg.speaker}: {seg.text}")
```

Для замеров WER передавайте `prettify=False`: сервер не будет приводить числа и даты к цифрам.

### Поток с микрофона

На вход `/ws/asr` идёт только сырой PCM16: 16 кГц, моно, little-endian, без контейнера. Сервер не пересэмплирует и не распаковывает WAV, MP3 или WebM; неверный формат даёт пустой текст без ошибки. Конвертация файла: `ffmpeg -i in.mp3 -ar 16000 -ac 1 -f s16le out.pcm`.

```python
from freedomspeech import pcm16_frames
pcm = open("speech.pcm", "rb").read()
text = []
for ev in fs.stream_transcribe(pcm16_frames(pcm, frame_ms=100), realtime=True):
    if ev.is_final:
        text.append(ev.text)
print(" ".join(text))
```

Длинную речь сервер режет на отрезки по паузам и присылает `final` на каждый: итоговый текст собирается из всех `final`. Для диалога держите один `ASRStream` на всю сессию: после каждой фразы вызывайте `stop()`, забирайте `final` и продолжайте слать звук по тому же соединению. Соединение закрывается сервером после 15 с без аудио.

### Поток синтеза

```python
from freedomspeech import pcm16_to_wav
pcm = b"".join(fs.stream_speech("Сәлем, Айгерім!", voice="tomiris", model="freedomspeech-flash"))
open("hi.wav", "wb").write(pcm16_to_wav(pcm))
```

Две модели: `freedomspeech` (по умолчанию на сервере; синтезирует фразу целиком и отдаёт кусками) и `freedomspeech-flash` (звук идёт по мере синтеза; неречевые теги и эмоции у flash пока не подключены). По HTTP `speak()` работает модель `freedomspeech`; flash доступна по WebSocket.

Для чата или ассистента откройте одно соединение на сессию: первый запрос на новом сокете медленнее. На одном соединении до 8 запросов в очереди и до 60 с ожидания; при переполнении приходит `StreamError` с `code="queue_full"`.

```python
with fs.tts_stream() as s:
    for sentence in sentences:
        play(b"".join(s.synthesize(sentence, voice="tomiris", model="freedomspeech-flash")))
```

### Теги в тексте

Просодия для всех голосов: `<pause>`, `<louder>...</louder>`, `<slower>...</slower>` (вложенные теги закрываются по порядку). Неречевые звуки только у `tomiris`: `<sigh>`, `<laugh>`, `<cough>` и другие. Незакрытый тег даёт `400`.

### Ошибки

Все ошибки наследуют `FreedomSpeechError` с полями `status` (HTTP-код), `body` (тело ответа), `code` (машинный код, если есть). `AuthenticationError` поднимается при 401 и когда ключ не задан; `StreamError` приходит с кадром `{"type": "error"}` по WebSocket; `ConnectionFailed` при сетевых сбоях.

```python
from freedomspeech import FreedomSpeech, AuthenticationError, FreedomSpeechError
try:
    FreedomSpeech(api_key="bad").speak("Сәлем")
except AuthenticationError as e:
    print(e)          # Ключ не принят ... | HTTP 401
except FreedomSpeechError as e:
    print(e.status, e.body)
```

### Лимиты по документации

- Текст синтеза: 2000 символов у демо-ключа сайта; для выданных ключей лимит согласуется при выдаче.
- Референс для клонирования: рекомендуется до 20 с; WAV, MP3, OGG, M4A, FLAC, WebM.
- Форматы файла распознавания: wav, mp3, ogg, oga, m4a, flac, webm, mp4, opus.
- Перевод и определение языка: до 50 строк за запрос.

## English

### Install and run

```bash
pip install freedomspeech
export FS_API_KEY=...
```

Transcribe a file:

```python
from freedomspeech import FreedomSpeech
fs = FreedomSpeech()
print(fs.transcribe("call.mp3").text)
```

Synthesize speech:

```python
from freedomspeech import FreedomSpeech
wav = FreedomSpeech().speak("Сәлеметсіз бе, бұл сынақ.", voice="tomiris", language="kk")
open("hello.wav", "wb").write(wav)
```

Clone a voice from a recording:

```python
from freedomspeech import FreedomSpeech
wav = FreedomSpeech().clone_voice("Сәлеметсіз бе, қалыңыз қалай?", reference_audio="me.wav", language="kk")
open("cloned.wav", "wb").write(wav)
```

Command line: `fs transcribe call.mp3`, `fs speak "Сәлем" --voice tomiris -o out.wav`, `fs voices`.

### Notes

- Authentication: `X-API-Key` header for HTTP, `?apiKey=` for WebSocket. The key comes from the `api_key` argument or the `FS_API_KEY` environment variable. `bearer_auth=True` adds `Authorization: Bearer` on top (not part of the public docs; useful behind proxies).
- `transcribe(..., diarize=True, speaker_names=[...])` requests `response_format=diarized_json` and returns `segments` with `speaker`, `start`, `end`. Use `prettify=False` for raw output when measuring WER.
- `stream_transcribe(pcm16_chunks)` streams raw PCM16 (16 kHz, mono, little-endian, no container) over `WS /ws/asr` and yields `partial` and `final` events. The server does not resample or unwrap containers; convert first (`ffmpeg -i in.mp3 -ar 16000 -ac 1 -f s16le out.pcm`). Long speech is split into segments, one `final` per segment.
- `speak()` returns a WAV file (16-bit, mono, 24 kHz). `stream_speech()` yields raw PCM16 chunks over `WS /ws/tts`; the default model there is `freedomspeech-flash`. Wrap chunks with `pcm16_to_wav`.
- `tts_stream()` keeps one socket open for many requests (up to 8 queued, 60 s max wait); the first request on a fresh socket is slower.
- Errors derive from `FreedomSpeechError` (`status`, `body`, `code`); `AuthenticationError` on 401, `StreamError` for WebSocket error frames, `ConnectionFailed` for network failures.

## Разработка / Development

```bash
pip install -e .[dev]
pytest
```

Tests run without network: HTTP and WebSocket calls are mocked.

License: MIT.
