# Whisper Beta

Веб-приложение для записи разговора с микрофона и распознавания речи с помощью `faster-whisper`.

## Требования

- macOS или Linux
- Python 3.9 или новее
- Доступ к микрофону
- Интернет при первом запуске для загрузки модели Whisper

На macOS запись через микрофон доступна в браузере по адресу `http://localhost:8000`.

## Установка

```bash
git clone https://github.com/timi131/whisper_beta.git
cd whisper_beta

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Запуск

```bash
python app.py
```

Откройте в браузере: http://127.0.0.1:8000

При первом запуске приложение загрузит модель Whisper. Это может занять время. Далее нажмите «Начать запись» и разрешите доступ к микрофону.

Остановите сервер сочетанием `Ctrl+C`.

## Настройки

Настройки задаются переменными окружения перед запуском.

| Переменная | Значение по умолчанию | Описание |
|---|---:|---|
| `WHISPER_MODEL` | `small` | Модель: `tiny`, `base`, `small`, `medium`, `large-v3-turbo` |
| `LANGUAGE` | `ru` | Язык распознавания, например `ru` или `en`; пустая строка включает автоопределение |
| `BEAM` | `1` | Размер beam search от `1` до `5` |
| `CPU_THREADS` | `4` | Число потоков CPU |
| `SIL_RMS` | `300` | Порог определения тишины |
| `REC_DIR` | `recordings` | Каталог для аудио и текстовых расшифровок |
| `HOST` | `127.0.0.1` | Адрес сервера |
| `PORT` | `8000` | Порт сервера |

Пример запуска с английской моделью:

```bash
WHISPER_MODEL=base LANGUAGE=en BEAM=3 CPU_THREADS=6 python app.py
```

Пример запуска внутри локальной сети:

```bash
HOST=0.0.0.0 PORT=8000 python app.py
```

Для доступа с другого устройства нужен HTTPS или локальный адрес сервера без HTTPS не даст браузеру доступ к микрофону в большинстве браузеров.

## Запуск в Docker

Сборка образа:

```bash
docker build -t whisper-beta .
```

Запуск локально:

```bash
docker run --rm -p 8000:8000 -e PORT=8000 whisper-beta
```

Откройте http://127.0.0.1:8000.

Для платформ развёртывания в Docker или через `Procfile` приложение слушает `0.0.0.0`, а порт берётся из переменной `PORT`. Команда запуска:

```bash
python -m uvicorn app:app --host 0.0.0.0 --port $PORT
```

## Сохранённые записи

Записи сохраняются в каталоге `recordings/<дата-время>/`:

- `audio.wav` — исходное аудио
- `transcript.txt` — расшифровка с временными метками

Список записей и кнопка удаления доступны на главной странице.

## macOS-инсталлятор

Для установки через Finder соберите приложение и образ:

```bash
chmod +x scripts/build-macos-app.sh scripts/install-macos.sh scripts/stop-macos.sh
./scripts/build-macos-app.sh
```

Будут созданы:

- `dist/Whisper Beta.app` — приложение macOS
- `dist/Whisper-Beta-macOS.dmg` — образ для перетаскивания в `Applications`

Для автоматической установки в `~/Applications`:

```bash
./scripts/install-macos.sh
```

При первом запуске приложение создаст окружение в `~/Library/Application Support/WhisperBeta`, установит зависимости и откроет страницу по адресу http://127.0.0.1:8000.

Сервер останавливается командой:

```bash
./scripts/stop-macos.sh
```
