#!/usr/bin/env python3
"""Запись разговора в браузере + транскрипция на CPU (faster-whisper).

Установка:  pip install fastapi "uvicorn[standard]" faster-whisper numpy
Запуск:     python app.py          (слушает 127.0.0.1:8000)

Переменные окружения (все необязательные):
  WHISPER_MODEL  tiny | base | small | medium | large-v3-turbo   (по умолчанию small)
  LANGUAGE       ru | en | ...  (пусто = автоопределение)
  BEAM           1..5, больше = точнее, но медленнее (по умолчанию 1)
  CPU_THREADS    число потоков (по умолчанию 4)
  SIL_RMS        порог тишины для нарезки на куски (по умолчанию 300)
  REC_DIR        куда сохранять записи (по умолчанию ./recordings)
  HOST / PORT    адрес и порт (по умолчанию 127.0.0.1 / 8000)
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import threading
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

MODEL_SIZE = os.getenv("WHISPER_MODEL", "small")
LANGUAGE = os.getenv("LANGUAGE", "ru") or None
BEAM = int(os.getenv("BEAM", "1"))
THREADS = int(os.getenv("CPU_THREADS", "4"))
SIL_RMS = float(os.getenv("SIL_RMS", "300"))
REC_DIR = Path(os.getenv("REC_DIR", "recordings"))
REC_DIR.mkdir(parents=True, exist_ok=True)

SR = 16000            # браузер присылает mono PCM int16 16 кГц
MIN_CHUNK = 8 * SR    # режем на куски не короче 8 с — по паузе в речи
MAX_CHUNK = 25 * SR   # но не длиннее 25 с
SIL_WIN = SR // 2     # пауза = 0.5 с тишины

model = None
model_lock = threading.Lock()  # на CPU одновременно крутим только один кусок


def load_model() -> None:
    global model
    from faster_whisper import WhisperModel

    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8", cpu_threads=THREADS)


def transcribe_chunk(pcm: np.ndarray, prompt: str) -> str:
    audio = pcm.astype(np.float32) / 32768.0
    with model_lock:
        segs, _ = model.transcribe(
            audio,
            language=LANGUAGE,
            beam_size=BEAM,
            vad_filter=True,                    # отсекает тишину и «галлюцинации»
            condition_on_previous_text=False,
            initial_prompt=prompt or None,      # хвост предыдущего куска для связности
        )
        return " ".join(s.text.strip() for s in segs).strip()


def fmt_time(sec: float) -> str:
    sec = int(sec)
    h, m, s = sec // 3600, sec % 3600 // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"Загружаю модель Whisper «{MODEL_SIZE}»… (при первом запуске скачивается)")
    await asyncio.to_thread(load_model)
    print("Модель готова.")
    yield


app = FastAPI(lifespan=lifespan)
app.mount("/files", StaticFiles(directory=REC_DIR), name="files")


# ---------------------------------------------------------------- API
@app.get("/api/recordings")
def list_recordings():
    out = []
    for d in sorted(REC_DIR.iterdir(), reverse=True):
        wav, txt = d / "audio.wav", d / "transcript.txt"
        if not wav.exists():
            continue
        duration = max(0, wav.stat().st_size - 44) / (SR * 2)
        preview = txt.read_text(encoding="utf-8")[:160] if txt.exists() else ""
        out.append({"id": d.name, "duration": fmt_time(duration), "preview": preview})
    return out[:50]


@app.delete("/api/recordings/{rec_id}")
def delete_recording(rec_id: str):
    if not re.fullmatch(r"[\d_-]+", rec_id):
        raise HTTPException(400, "bad id")
    d = REC_DIR / rec_id
    if d.is_dir():
        shutil.rmtree(d)
    return {"ok": True}


@app.websocket("/ws")
async def ws_record(ws: WebSocket):
    await ws.accept()
    rec_id = time.strftime("%Y-%m-%d_%H-%M-%S")
    d = REC_DIR / rec_id
    d.mkdir(parents=True, exist_ok=True)

    wav = wave.open(str(d / "audio.wav"), "wb")
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(SR)
    txt = open(d / "transcript.txt", "w", encoding="utf-8")

    queue: asyncio.Queue = asyncio.Queue()
    connected = True

    async def send(obj: dict) -> None:
        nonlocal connected
        if connected:
            try:
                await ws.send_json(obj)
            except Exception:
                connected = False

    async def transcriber() -> None:
        prompt = ""
        while True:
            item = await queue.get()
            if item is None:
                return
            start, pcm = item
            try:
                text = await asyncio.to_thread(transcribe_chunk, pcm, prompt)
            except Exception as e:  # не роняем запись из-за одного куска
                text = f"[ошибка распознавания: {e}]"
            if text:
                stamp = fmt_time(start / SR)
                txt.write(f"[{stamp}] {text}\n")
                txt.flush()
                prompt = text[-200:]
                await send({"type": "text", "time": stamp, "text": text})

    worker = asyncio.create_task(transcriber())

    buf: list[np.ndarray] = []
    buf_len = 0
    pos = 0      # сколько сэмплов уже отдано на распознавание
    quiet = 0    # сколько сэмплов подряд была тишина

    def cut() -> None:
        nonlocal buf, buf_len, pos
        if not buf_len:
            return
        pcm = np.concatenate(buf)
        queue.put_nowait((pos, pcm))
        pos += len(pcm)
        buf, buf_len = [], 0

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                connected = False
                break
            data = msg.get("bytes")
            if data:
                pcm = np.frombuffer(data, dtype=np.int16)
                wav.writeframes(pcm.tobytes())
                buf.append(pcm)
                buf_len += len(pcm)
                rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
                quiet = quiet + len(pcm) if rms < SIL_RMS else 0
                if (buf_len >= MIN_CHUNK and quiet >= SIL_WIN) or buf_len >= MAX_CHUNK:
                    cut()
                    quiet = 0
            elif msg.get("text") == "stop":
                break
    except Exception:
        connected = False
    finally:
        cut()                         # остаток записи
        queue.put_nowait(None)
        await worker                  # дожидаемся распознавания всех кусков
        wav.close()
        txt.close()
        await send({"type": "done", "id": rec_id})
        if connected:
            try:
                await ws.close()
            except Exception:
                pass


# ---------------------------------------------------------------- страница
PAGE = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Запись с транскрипцией</title>
<style>
:root{--bg:#fff;--fg:#1c1c1e;--mut:#6b6b70;--line:#e2e2e6;--acc:#d93025;--box:#f6f6f8}
@media(prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececee;--mut:#9a9aa0;--line:#2c2c30;--box:#1f1f23}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 16px}h2{font-size:17px;margin:32px 0 8px}
.bar{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
button{font:inherit;cursor:pointer;border-radius:10px;border:1px solid var(--line);
  background:var(--box);color:var(--fg);padding:10px 18px}
#btn{background:var(--acc);border-color:var(--acc);color:#fff;font-weight:600}
#btn.on{background:var(--box);color:var(--fg);border-color:var(--line)}
#btn:disabled{opacity:.5;cursor:default}
#timer{font-variant-numeric:tabular-nums;font-size:20px}
#status{color:var(--mut);font-size:14px}
.meter{height:6px;background:var(--box);border-radius:3px;margin:14px 0;overflow:hidden}
#lvl{height:100%;width:0;background:var(--acc);transition:width .08s}
.box{background:var(--box);border-radius:12px;padding:14px 16px;min-height:140px;max-height:55vh;overflow:auto}
.box p{margin:0 0 10px}.box .t{color:var(--mut);font-size:13px;margin-right:6px;font-variant-numeric:tabular-nums}
.empty{color:var(--mut)}
.rec{border:1px solid var(--line);border-radius:12px;padding:10px 14px;margin-bottom:10px}
.rec .h{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}
.rec .m{color:var(--mut);font-size:14px}.rec .pv{color:var(--mut);font-size:14px;margin:4px 0 6px}
.rec a{margin-right:14px;color:inherit}.rec button{padding:2px 10px;font-size:13px}
</style></head><body><main>
<h1>Запись разговора</h1>
<div class="bar">
  <button id="btn">● Начать запись</button>
  <span id="timer">00:00</span><span id="status"></span>
</div>
<div class="meter"><div id="lvl"></div></div>
<div id="out" class="box"><span class="empty">Здесь появится текст — с задержкой в одну фразу.</span></div>
<h2>Сохранённые записи</h2><div id="list"></div>
</main>
<script>
const $ = id => document.getElementById(id);
const btn = $('btn'), timer = $('timer'), statusEl = $('status'), out = $('out'), lvl = $('lvl');
let ws, ctx, stream, node, pending = [], pendingLen = 0, t0, tick, wake, recording = false;

const workletCode = `class Rec extends AudioWorkletProcessor{
  process(i){const c=i[0][0]; if(c) this.port.postMessage(c.slice(0)); return true}
} registerProcessor('rec', Rec)`;

const fmt = s => { s = Math.floor(s); const m = Math.floor(s/60), r = s%60;
  return String(m).padStart(2,'0')+':'+String(r).padStart(2,'0'); };
const setStatus = t => statusEl.textContent = t;

function downsample(buf, inRate, outRate){
  if (inRate === outRate) return buf;
  const ratio = inRate / outRate, n = Math.floor(buf.length / ratio), res = new Float32Array(n);
  for (let i = 0; i < n; i++){
    const a = Math.floor(i*ratio), b = Math.min(Math.floor((i+1)*ratio), buf.length);
    let s = 0; for (let j = a; j < b; j++) s += buf[j];
    res[i] = s / Math.max(1, b - a);
  }
  return res;
}

function flush(){
  if (!pendingLen) return;
  const all = new Float32Array(pendingLen); let o = 0;
  for (const c of pending){ all.set(c, o); o += c.length; }
  pending = []; pendingLen = 0;
  const ds = downsample(all, ctx.sampleRate, 16000), pcm = new Int16Array(ds.length);
  let sum = 0;
  for (let i = 0; i < ds.length; i++){
    const s = Math.max(-1, Math.min(1, ds[i])); sum += s*s;
    pcm[i] = s < 0 ? s*32768 : s*32767;
  }
  lvl.style.width = Math.min(100, Math.sqrt(sum/Math.max(1,ds.length))*400) + '%';
  if (ws && ws.readyState === 1) ws.send(pcm.buffer);
}

function addLine(time, text){
  if (out.querySelector('.empty')) out.textContent = '';
  const p = document.createElement('p'), t = document.createElement('span');
  t.className = 't'; t.textContent = '[' + time + ']';
  p.append(t, text); out.append(p); out.scrollTop = out.scrollHeight;
}

function onMsg(e){
  const m = JSON.parse(e.data);
  if (m.type === 'text') addLine(m.time, m.text);
  if (m.type === 'done'){
    setStatus('Готово. Запись сохранена.'); btn.disabled = false; loadList();
  }
}

function stopAudio(){
  clearInterval(tick); lvl.style.width = '0';
  try { node && node.disconnect(); } catch {}
  stream && stream.getTracks().forEach(t => t.stop());
  ctx && ctx.close();
  wake && wake.release().catch(() => {});
}

async function start(){
  try {
    stream = await navigator.mediaDevices.getUserMedia(
      {audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true}});
  } catch (e) {
    setStatus('Нет доступа к микрофону. Нужен HTTPS (или localhost) и разрешение в браузере.'); return;
  }
  ws = new WebSocket((location.protocol === 'https:' ? 'wss://' : 'ws://') + location.host + '/ws');
  ws.onmessage = onMsg;
  try { await new Promise((ok, err) => { ws.onopen = ok; ws.onerror = err; }); }
  catch { stream.getTracks().forEach(t => t.stop()); setStatus('Не удалось подключиться к серверу'); return; }
  ws.onclose = () => { if (recording){ recording = false; stopAudio(); btn.className=''; btn.textContent='● Начать запись';
    btn.disabled = false; setStatus('Соединение потеряно'); } };

  ctx = new AudioContext();
  await ctx.audioWorklet.addModule(URL.createObjectURL(new Blob([workletCode], {type: 'application/javascript'})));
  const src = ctx.createMediaStreamSource(stream);
  node = new AudioWorkletNode(ctx, 'rec');
  node.port.onmessage = e => { pending.push(e.data); pendingLen += e.data.length; if (pendingLen >= 4096) flush(); };
  const mute = ctx.createGain(); mute.gain.value = 0;
  src.connect(node); node.connect(mute); mute.connect(ctx.destination);
  try { wake = await navigator.wakeLock.request('screen'); } catch {}

  recording = true; out.innerHTML = '<span class="empty">Слушаю…</span>';
  t0 = Date.now(); tick = setInterval(() => timer.textContent = fmt((Date.now() - t0)/1000), 500);
  btn.className = 'on'; btn.textContent = '■ Остановить'; setStatus('Идёт запись');
}

function stop(){
  recording = false; flush(); ws.send('stop'); stopAudio();
  btn.className = ''; btn.textContent = '● Начать запись'; btn.disabled = true;
  setStatus('Дорабатываю распознавание последних фраз…');
}

btn.onclick = () => recording ? stop() : start();

async function loadList(){
  const items = await (await fetch('/api/recordings')).json(), box = $('list'); box.textContent = '';
  if (!items.length){ box.innerHTML = '<span class="empty">Пока пусто</span>'; return; }
  for (const r of items){
    const el = document.createElement('div'); el.className = 'rec';
    const h = document.createElement('div'); h.className = 'h';
    const title = document.createElement('b'); title.textContent = r.id.replace('_', ' ').replace(/-/g, (m, i) => i > 9 ? ':' : '-');
    const meta = document.createElement('span'); meta.className = 'm'; meta.textContent = r.duration;
    h.append(title, meta);
    const pv = document.createElement('div'); pv.className = 'pv'; pv.textContent = r.preview;
    const a1 = Object.assign(document.createElement('a'), {href: `/files/${r.id}/transcript.txt`, textContent: 'Текст', target: '_blank'});
    const a2 = Object.assign(document.createElement('a'), {href: `/files/${r.id}/audio.wav`, textContent: 'Аудио', target: '_blank'});
    const del = document.createElement('button'); del.textContent = 'Удалить';
    del.onclick = async () => { if (confirm('Удалить запись?')){ await fetch('/api/recordings/' + r.id, {method: 'DELETE'}); loadList(); } };
    el.append(h, pv, a1, a2, del); box.append(el);
  }
}
loadList();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


if __name__ == "__main__":
    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8000")))