import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from 'react'
import {
  AudioLines, ArrowDownToLine, ArrowRight, Check, CheckCheck,
  CircleAlert, Clipboard, Clock3, CloudUpload, FileAudio2, FileText,
  FolderOpen, Globe2, Languages, LoaderCircle, Mic2,
  Pause, Play, RotateCcw, ShieldCheck, Sparkles, Waves, X,
} from 'lucide-react'

type ModelChoice = 'russian' | 'multilingual'
type ModelStatus = {status: 'installed' | 'not_installed' | 'failed' | 'downloading', installed: boolean, size_bytes: number, progress: number, detail: string, error: string | null}
const desktopToken = new URLSearchParams(window.location.search).get('token')
const authHeaders = () => desktopToken ? {'X-GigaScribe-Token': desktopToken} : {}

type Segment = { start: number; end: number; text: string }
type Result = { text: string; segments: Segment[]; duration: number; model: ModelChoice; model_name: string }
type Job = {
  id: string; filename: string; model: ModelChoice;
  status: 'queued' | 'processing' | 'completed' | 'failed';
  phase: string; progress: number; detail: string; error?: string | null; result?: Result | null;
}
const formats = '.mp3,.wav,.m4a,.mp4,.flac,.ogg,.opus,.webm,.aac,.wma,.mov,.mkv'
const maxBytes = 200 * 1024 * 1024
const fmtSize = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} ГБ` : bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} КБ` : `${(bytes / 1024 / 1024).toFixed(1)} МБ`
const fmtTime = (seconds: number) => {
  if (!Number.isFinite(seconds)) return '00:00'
  const s = Math.floor(seconds)
  return `${Math.floor(s / 60).toString().padStart(2, '0')}:${(s % 60).toString().padStart(2, '0')}`
}
const fmtSrt = (s: number) => {
  const ms = Math.round(s * 1000)
  const hh = Math.floor(ms / 3600000).toString().padStart(2, '0')
  const mm = Math.floor(ms % 3600000 / 60000).toString().padStart(2, '0')
  const ss = Math.floor(ms % 60000 / 1000).toString().padStart(2, '0')
  const frac = (ms % 1000).toString().padStart(3, '0')
  return `${hh}:${mm}:${ss},${frac}`
}
const saveFile = (text: string, filename: string, mime = 'text/plain;charset=utf-8') => {
  const blob = new Blob(['\ufeff', text], { type: mime })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1500)
}
const baseName = (name: string) => name.replace(/\.[^.]+$/, '')
const readError = async (response: Response) => {
  try {
    const data = await response.json()
    const detail = data.detail
    return typeof detail === 'string' ? detail : `Ошибка сервера: ${response.status}`
  } catch {
    return `Ошибка сервера: ${response.status}`
  }
}

function App() {
  const [model, setModel] = useState<ModelChoice>('russian')
  const [audio, setAudio] = useState<File | null>(null)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState('')
  const [job, setJob] = useState<Job | null>(null)
  const [text, setText] = useState('')
  const [copied, setCopied] = useState(false)
  const [serverOnline, setServerOnline] = useState<boolean | null>(null)
  const [hasFfmpeg, setHasFfmpeg] = useState(true)
  const [models, setModels] = useState<Record<ModelChoice, ModelStatus> | null>(null)
  const [modelsPath, setModelsPath] = useState('')
  const [modelsError, setModelsError] = useState('')
  const [playing, setPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [position, setPosition] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const playerRef = useRef<HTMLAudioElement>(null)
  const fileUrlRef = useRef<string | null>(null)
  const jobIdRef = useRef<string | null>(null)
  const pollSequence = useRef(0)
  const busy = job?.status === 'queued' || job?.status === 'processing'
  const done = job?.status === 'completed'

  useEffect(() => {
    let mounted = true
    const check = async () => {
      try {
        const r = await fetch('/api/health')
        if (!r.ok) throw new Error()
        const data = await r.json()
        if (mounted) { setServerOnline(true); setHasFfmpeg(data.ffmpeg !== false) }
      } catch { if (mounted) setServerOnline(false) }
    }
    void check()
    const timer = window.setInterval(check, 12000)
    return () => { mounted = false; clearInterval(timer) }
  }, [])

  useEffect(() => {
    let active = true
    const checkModels = async () => {
      try {
        const res = await fetch('/api/models', {cache:'no-store'})
        if (!res.ok) throw new Error(await readError(res))
        const data: {models: Record<ModelChoice, ModelStatus>, path: string} = await res.json()
        if (active) { setModels(data.models); setModelsPath(data.path) }
      } catch(e) { if (active) setModelsError(e instanceof Error ? e.message : 'Не удалось получить список моделей') }
    }
    void checkModels()
    const timer = window.setInterval(checkModels, 1500)
    return () => { active = false; clearInterval(timer) }
  }, [])

  const manageModel = async (choice: ModelChoice) => {
    const status = models?.[choice]?.status
    if (!status || status === 'downloading' || busy) return
    const removing = status === 'installed'
    if (removing && !window.confirm(`Удалить ${choice === 'russian' ? 'русскоязычную' : 'мультиязычную'} модель с диска?`)) return
    try {
      setModelsError('')
      const response = await fetch(`/api/models/${choice}${removing ? '' : '/download'}`,
        {method: removing ? 'DELETE' : 'POST', headers: authHeaders()})
      if (!response.ok) throw new Error(await readError(response))
      const updated = await fetch('/api/models').then(r => r.json())
      setModels(updated.models)
    } catch(e) { setModelsError(e instanceof Error ? e.message : 'Ошибка операции с моделью') }
  }

  useEffect(() => () => { if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current) }, [])

  const chooseFile = useCallback((file: File) => {
    const ext = `.${file.name.split('.').at(-1)?.toLowerCase()}`
    if (!formats.split(',').includes(ext)) { setError('Неподдерживаемый формат. Выберите MP3, WAV, M4A, OGG, FLAC, WebM или MP4.'); return }
    if (file.size > maxBytes) { setError('Файл слишком большой. Максимум — 200 МБ.'); return }
    if (file.size === 0) { setError('Файл пустой.'); return }
    if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current)
    const url = URL.createObjectURL(file)
    fileUrlRef.current = url
    setAudioUrl(url)
    setAudio(file)
    setError('')
    setJob(null)
    setText('')
    setPosition(0)
    setDuration(0)
    setPlaying(false)
  }, [])

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setDragging(false)
    if (busy) return
    const first = event.dataTransfer.files[0]
    if (first) chooseFile(first)
  }

  useEffect(() => {
    const id = jobIdRef.current
    if (!id || !busy) return
    let cancelled = false
    const sequence = ++pollSequence.current
    let timer: number | undefined
    const poll = async () => {
      try {
        const res = await fetch(`/api/jobs/${id}`, { cache: 'no-store' })
        if (!res.ok) throw new Error(await readError(res))
        const next: Job = await res.json()
        if (cancelled || sequence !== pollSequence.current) return
        setJob(next)
        if (next.status === 'completed' && next.result) setText(next.result.text)
        if (next.status === 'failed') setError(next.error || 'Не удалось распознать аудио')
        if (next.status === 'processing' || next.status === 'queued') {
          timer = window.setTimeout(poll, 850)
        }
      } catch (e) {
        if (cancelled) return
        setError(e instanceof Error ? e.message : 'Не удалось получить статус задачи')
        setJob(prev => prev ? { ...prev, status: 'failed' } : null)
      }
    }
    void poll()
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [job?.status, job?.id, busy])

  const start = async () => {
    if (!audio || busy || models?.[model]?.status !== 'installed') return
    setError('')
    setJob(null)
    setText('')
    jobIdRef.current = null
    const form = new FormData()
    form.append('file', audio)
    form.append('model', model)
    try {
      setJob({ id: '', filename: audio.name, model, status: 'queued', phase: 'upload', progress: 0, detail: 'Загрузка файла на локальный сервер' })
      const res = await fetch('/api/jobs', { method: 'POST', headers: authHeaders(), body: form })
      if (!res.ok) throw new Error(await readError(res))
      const data: { job_id: string } = await res.json()
      jobIdRef.current = data.job_id
      setJob({ id: data.job_id, filename: audio.name, model, status: 'queued', phase: 'queued', progress: 0, detail: 'В очереди' })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось подключиться к серверу')
      setJob(null)
      setServerOnline(false)
    }
  }

  const reset = () => {
    if (busy) return
    if (fileUrlRef.current) URL.revokeObjectURL(fileUrlRef.current)
    fileUrlRef.current = null
    jobIdRef.current = null
    setAudioUrl(null); setAudio(null); setJob(null); setText(''); setError(''); setDuration(0); setPosition(0)
    if (inputRef.current) inputRef.current.value = ''
  }

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch { setError('Не удалось скопировать текст. Используйте Ctrl+C.') }
  }

  const srt = () => (job?.result?.segments || [])
    .map((s, i) => `${i + 1}\n${fmtSrt(s.start)} --> ${fmtSrt(s.end)}\n${s.text}\n`).join('\n')
  const resultName = `${baseName(audio?.name || 'transcript')}_transcript`

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="logo"><span className="logo-icon"><AudioLines size={22} strokeWidth={2.4}/></span><span>giga<span className="logo-accent">scribe</span><small>STUDIO</small></span></div>
        <div className="side-nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <div className="side-item active"><Waves size={18}/> Расшифровка <span className="side-marker"/></div>
        <div className="side-spacer"/>
        <div className="sidebar-bottom">
          <div className="sidebar-note"><ShieldCheck size={18} className="green"/><div><strong>Приватность прежде всего</strong><p>Аудио обрабатывается на вашем компьютере и не отправляется в облако.</p></div></div>
          <a href="https://github.com/salute-developers/GigaAM" target="_blank" rel="noopener noreferrer" className="powered">Основано на GigaAM <ArrowRight size={15}/></a>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar"><div className="breadcrumb">Инструменты <span>/</span> <strong>Расшифровка аудио</strong></div><div className={`server-indicator ${serverOnline === false ? 'offline' : ''}`}><i className="live-dot"/>{serverOnline === null ? 'Подключение...' : serverOnline ? 'Локальный сервер активен' : 'Сервер недоступен'}</div></header>
        <main className="content">
          <div className="hero">
            <div className="hero-eyebrow"><Sparkles size={13}/> РАСПОЗНАВАНИЕ РЕЧИ НА БАЗЕ ИИ</div>
            <h1>Превратите звук<br/><span>в понятный текст.</span></h1>
            <p>Загрузите аудиозапись, выберите модель Сбера и получите расшифровку. Всё локально, без API-ключей и отправки файлов в облако.</p>
            <div className="hero-art" aria-hidden="true"><div className="orb orb-one"/><div className="orb orb-two"/><div className="hero-wave">{Array.from({ length: 34 }, (_, i) => <span key={i} style={{height: `${16 + 65 * Math.abs(Math.sin(i * 0.54 + 0.3)) * (0.75 + 0.25 * Math.cos(i * 0.19))}%`}}/>)}</div></div>
          </div>

          <div className="workspace-grid">
            <section className="left-column">
              <div className="section-title"><span className="step-number">01</span><div><h2>Аудиофайл</h2><p>Начните с загрузки записи</p></div></div>
              <input type="file" ref={inputRef} accept={formats} className="visually-hidden" onChange={(e: ChangeEvent<HTMLInputElement>) => { const f = e.target.files?.[0]; if (f) chooseFile(f) }} />
              {!audio ? (
                <div className={`drop-zone ${dragging ? 'dragging' : ''}`} role="button" tabIndex={busy ? -1 : 0}
                  onClick={() => inputRef.current?.click()}
                  onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); inputRef.current?.click() } }}
                  onDragEnter={e => { e.preventDefault(); setDragging(true) }}
                  onDragOver={e => e.preventDefault()}
                  onDragLeave={e => { e.preventDefault(); if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false) }}
                  onDrop={onDrop}>
                  <div className="drop-icon"><CloudUpload size={28} strokeWidth={1.8}/></div>
                  <h3>Перетащите аудиофайл сюда</h3>
                  <p>или нажмите, чтобы выбрать на компьютере</p>
                  <button type="button" className="btn choose-btn" onClick={e => { e.stopPropagation(); inputRef.current?.click() }}><FolderOpen size={16}/> Выбрать файл</button>
                  <div className="format-hint">MP3, WAV, M4A, FLAC, OGG, WebM, MP4 · до 200 МБ</div>
                </div>
              ) : (
                <div className="file-card">
                  <div className="file-card-top"><span className="file-symbol"><FileAudio2 size={25}/></span><div className="file-identity"><strong title={audio.name}>{audio.name}</strong><span>{fmtSize(audio.size)} · Аудиозапись</span></div><button disabled={busy} className="icon-button" aria-label="Убрать файл" onClick={reset}><X size={19}/></button></div>
                  {audioUrl && <audio ref={playerRef} src={audioUrl} preload="metadata" onLoadedMetadata={e => setDuration(e.currentTarget.duration)} onTimeUpdate={e => setPosition(e.currentTarget.currentTime)} onEnded={() => setPlaying(false)}/>}
                  <div className="audio-controls"><button className="play-button" aria-label={playing ? 'Пауза' : 'Воспроизвести'} onClick={() => { if (!playerRef.current) return; if (playing) { playerRef.current.pause(); setPlaying(false) } else { void playerRef.current.play().then(() => setPlaying(true)).catch(() => setError('Браузер не поддерживает воспроизведение этого формата. Расшифровка всё равно доступна.')) } }}>{playing ? <Pause size={17} fill="currentColor"/> : <Play size={17} fill="currentColor"/>}</button><span className="timer">{fmtTime(position)}</span><input className="seek" type="range" min="0" max={duration || 1} step="0.05" value={position} aria-label="Позиция аудио" onChange={e => { const value = Number(e.target.value); setPosition(value); if (playerRef.current) playerRef.current.currentTime = value }}/><span className="timer">{fmtTime(duration)}</span></div>
                  <span className="file-success"><Check size={14}/> Файл выбран и готов к обработке</span>
                </div>
              )}

              <div className="section-title model-title"><span className="step-number">02</span><div><h2>Модель распознавания</h2><p>Выберите подходящий язык записи</p></div></div>
              <div className="models">
                <button type="button" className={`model-card ${model === 'russian' ? 'selected' : ''}`} disabled={busy} onClick={() => setModel('russian')}>
                  <span className="model-icon ru"><Mic2 size={20}/></span><span className="model-details"><span className="model-name">Русскоязычная <span className="mini-tag">РЕКОМЕНДУЕМ</span></span><span className="model-desc">GigaAM v3 · e2e RNN-T</span><span className="model-extra">Русский язык · знаки препинания</span></span><span className="radio-mark">{model === 'russian' && <Check size={12} strokeWidth={3}/>}</span>
                </button>
                <button type="button" className={`model-card ${model === 'multilingual' ? 'selected' : ''}`} disabled={busy} onClick={() => setModel('multilingual')}>
                  <span className="model-icon ml"><Globe2 size={20}/></span><span className="model-details"><span className="model-name">Мультиязычная</span><span className="model-desc">GigaAM Multilingual · CTC</span><span className="model-extra">RU · EN · KK · KY · UZ</span></span><span className="radio-mark">{model === 'multilingual' && <Check size={12} strokeWidth={3}/>}</span>
                </button>
              </div>
              <div className="manager-panel">
                <div className="manager-heading"><div><strong>Установленные модели</strong><small>Скачиваются только по вашему выбору</small></div><span className="manager-spark">✦</span></div>
                {(['russian', 'multilingual'] as const).map(choice => {
                  const entry = models?.[choice]
                  const status = entry?.status
                  return <div className="manager-row" key={choice}>
                    <span className={`manager-dot ${status === 'installed' ? 'ready' : status || ''}`}/>
                    <div className="manager-identity"><strong>{choice === 'russian' ? 'Русскоязычная' : 'Мультиязычная'}</strong>
                      <small>{status === 'installed' ? `Установлена · ${fmtSize(entry.size_bytes)}` : status === 'downloading' ? `${entry.detail} · ${entry.progress}%` : status === 'failed' ? 'Ошибка загрузки' : status === 'not_installed' ? 'Не установлена' : 'Проверка...'}</small>
                      {status === 'downloading' && <div className="manager-progress"><span style={{width:`${entry.progress}%`}}/></div>}
                    </div>
                    <button type="button" className={`manager-action ${status === 'installed' ? 'danger' : ''}`} disabled={busy || !status || status === 'downloading'} onClick={() => void manageModel(choice)}>{status === 'installed' ? 'Удалить' : status === 'failed' ? 'Повторить' : status === 'downloading' ? 'Загрузка...' : 'Скачать'}</button>
                  </div>
                })}
                <div className="manager-footer"><span>Папка моделей</span><button type="button" onClick={() => void navigator.clipboard.writeText(modelsPath)}>▢ Копировать путь</button></div>
                <div className="manager-path">{modelsPath || 'Проверка расположения...'}</div>
                {!!modelsError && <div className="models-error" role="alert">{modelsError}</div>}
              </div>
              <div className="model-hint"><Languages size={16}/><span>{model === 'russian' ? 'Лучший выбор для русской речи. Модель умеет расставлять пунктуацию и приводить текст к читаемому виду.' : 'Распознаёт 5 языков. Результат может быть без заглавных букв и знаков препинания — особенность CTC-модели.'}</span></div>
              <button type="button" className="btn primary-btn" disabled={!audio || busy || serverOnline === false || !hasFfmpeg || models?.[model]?.status !== 'installed'} onClick={() => void start()}>{busy ? <><LoaderCircle size={19} className="spin"/> Обрабатываем аудио...</> : models?.[model]?.status !== 'installed' ? 'Сначала скачайте выбранную модель' : <><AudioLines size={19}/> Расшифровать аудио <ArrowRight size={19}/></>}</button>
              {!hasFfmpeg && <p className="notice">FFmpeg не обнаружен. Установите его по инструкции из README.</p>}
              {serverOnline === false && <p className="notice">Запустите сервер: <code>uvicorn backend.main:app --host 127.0.0.1 --port 8000</code></p>}
            </section>

            <section className="right-column">
              <div className="section-title right-head"><span className="step-number">03</span><div><h2>Результат</h2><p>Ваша готовая расшифровка</p></div>{done && <span className="done-tag"><CheckCheck size={15}/> Готово</span>}</div>
              <div className={`result-box ${done ? 'has-result' : ''}`}>
                {!job ? (
                  <div className="empty-state"><div className="empty-illustration"><div className="empty-aura"/><FileText size={39} strokeWidth={1.2}/><span className="tiny-star"><Sparkles size={17}/></span></div><h3>Здесь появится текст</h3><p>Загрузите запись, выберите модель и нажмите «Расшифровать аудио»</p><div className="empty-line"/><div className="empty-line short"/><div className="empty-line medium"/></div>
                ) : busy ? (
                  <div className="progress-state"><div className="working-icon"><AudioLines size={29}/></div><h3>Обрабатываем вашу запись</h3><p>{job.detail || 'Запускаем обработку...'}</p><div className="progress-meta"><span>Прогресс</span><strong>{job.progress}%</strong></div><div className="progress-track"><div className="progress-fill" style={{width: `${job.progress}%`}}/></div><p className="progress-note">Расшифровка выполняется локально. Длинные записи могут обрабатываться несколько минут.</p></div>
                ) : job.status === 'failed' ? (
                  <div className="failed-state"><CircleAlert size={37}/><h3>Не получилось распознать</h3><p>{error || job.error || 'Проверьте файл и попробуйте ещё раз'}</p><button type="button" className="btn retry-btn" onClick={() => void start()}><RotateCcw size={16}/> Повторить</button></div>
                ) : (
                  <div className="transcript-content"><div className="result-meta"><span><Clock3 size={15}/> {fmtTime(job.result?.duration || duration)}</span><span className="meta-divider"/><span>{job.result?.model === 'russian' ? 'GigaAM v3' : 'GigaAM Multilingual'}</span><span className="result-count">{text.trim().split(/\s+/).filter(Boolean).length} слов</span></div><textarea aria-label="Текст расшифровки, можно редактировать" spellCheck={false} className="transcript-input" placeholder="В аудио не обнаружено распознаваемой речи" value={text} onChange={e => setText(e.target.value)}/><div className="edit-tip">Вы можете отредактировать текст перед сохранением</div></div>
                )}
              </div>
              <div className="result-actions"><button type="button" disabled={!done || !text} onClick={() => void copyText()} className="btn action-btn">{copied ? <Check size={16}/> : <Clipboard size={16}/>} {copied ? 'Скопировано' : 'Копировать'}</button><button type="button" disabled={!done || !text} onClick={() => saveFile(text, `${resultName}.txt`)} className="btn action-btn"><ArrowDownToLine size={16}/> Скачать TXT</button><button type="button" disabled={!done || !job?.result?.segments.length} onClick={() => saveFile(srt(), `${resultName}.srt`, 'text/srt;charset=utf-8')} className="btn action-btn"><FileText size={16}/> Скачать SRT</button></div>
              <div className="privacy-footer"><ShieldCheck size={16}/><span>Без облака и подписок. Веса моделей скачиваются отдельно только по вашему выбору.</span></div>
            </section>
          </div>
          <footer className="footer"><span>GigaScribe · локальная расшифровка речи</span><span>Сделано на открытых моделях <a href="https://github.com/salute-developers/GigaAM" target="_blank" rel="noopener noreferrer">GigaAM</a></span></footer>
        </main>
      </div>
    </div>
  )
}

export default App
