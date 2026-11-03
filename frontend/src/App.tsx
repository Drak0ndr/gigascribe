import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import {
  Alert, Badge, Button, Card, Divider, FileButton, Group,
  Modal, NativeSelect, Paper, Progress, ScrollArea, SegmentedControl, SimpleGrid,
  Stack, Text, Textarea, TextInput, Title,
} from '@mantine/core'
import { notifications } from '@mantine/notifications'
import {
  IconCheck, IconCopy, IconDownload, IconFolder, IconInfoCircle,
  IconMicrophone, IconMinus, IconPlayerPlay, IconRefresh, IconSettings, IconSquare,
  IconTrash, IconUpload, IconX, IconCpu, IconCloudDownload,
} from '@tabler/icons-react' 

type ModelChoice = 'russian' | 'multilingual'
type Page = 'transcribe' | 'models' | 'settings'
type Mode = 'standard' | 'vad'
type Device = 'auto' | 'cuda' | 'cpu'
type ModelStatus = {status: 'installed'|'not_installed'|'downloading'|'failed', installed: boolean, size_bytes: number, progress: number, detail: string, error: string|null}
type ModelInfo = {models: Record<ModelChoice,ModelStatus>, vad: ModelStatus, path: string}
type Segment = {start: number, end: number, text: string}
type Result = {text:string,segments:Segment[],duration:number,model:ModelChoice,mode:Mode,device:string}
type Job = {job_id?:string,id:string,status:'queued'|'processing'|'completed'|'failed',progress:number,detail:string,error?:string|null,result?:Result|null}
type DeviceInfo = {cuda_available:boolean,gpu_name:string|null,torch_version:string|null,cuda_runtime:string|null,reason:string|null}
declare global {
  interface Window {
    pywebview?: {api?: { minimize:()=>Promise<void>,toggle_maximize:()=>Promise<void>,close:()=>Promise<void>,save_text:(name:string,body:string)=>Promise<boolean> }}
  }
}
const token = new URLSearchParams(location.search).get('token')
const auth = (): Record<string, string> => token ? {'X-GigaScribe-Token': token} : {}
const fmtSize = (n:number) => n > 1e9 ? `${(n/1024**3).toFixed(2)} ГБ` : `${(n/1024**2).toFixed(0)} МБ`
const fmtTime = (n:number) => `${Math.floor(n/3600)?`${Math.floor(n/3600)}:`:''}${Math.floor(n%3600/60).toString().padStart(2,'0')}:${Math.floor(n%60).toString().padStart(2,'0')}`
const srtTime = (n:number) => {const ms=Math.round(n*1000);return `${Math.floor(ms/3600000).toString().padStart(2,'0')}:${Math.floor(ms%3600000/60000).toString().padStart(2,'0')}:${Math.floor(ms%60000/1000).toString().padStart(2,'0')},${(ms%1000).toString().padStart(3,'0')}`}
const errMsg = async(r:Response) => {try{const v=await r.json();return typeof v.detail==='string'?v.detail:`HTTP ${r.status}`}catch{return `HTTP ${r.status}`}}
const baseName = (n:string) => n.replace(/\.[^.]+$/,'')
const formats = '.mp3,.wav,.m4a,.mp4,.flac,.ogg,.opus,.webm,.aac,.wma,.mov,.mkv'

function App() {
  const [page,setPage] = useState<Page>('transcribe')
  const [model,setModel] = useState<ModelChoice>('russian')
  const [mode,setMode] = useState<Mode>('standard')
  const [device,setDevice] = useState<Device>('auto')
  const [file,setFile] = useState<File|null>(null)
  const [url,setUrl] = useState<string|null>(null)
  const [drag,setDrag] = useState(false)
  const [models,setModels] = useState<ModelInfo|null>(null)
  const [deviceInfo,setDeviceInfo] = useState<DeviceInfo|null>(null)
  const [job,setJob] = useState<Job|null>(null)
  const [resultText,setResultText] = useState('')
  const [vadToken,setVadToken] = useState('')
  const [pendingDelete,setPendingDelete] = useState<'vad'|ModelChoice|null>(null)
  const [busyUpload,setBusyUpload] = useState(false)
  const [isDesktop,setIsDesktop] = useState(Boolean(window.pywebview?.api))
  const jobId = useRef<string|null>(null)
  const busy=busyUpload||job?.status==='processing'||job?.status==='queued'
  const ready=job?.status==='completed'&&!!job.result

  useEffect(()=>{
    const listener=()=>setIsDesktop(Boolean(window.pywebview?.api))
    window.addEventListener('pywebviewready',listener)
    return()=>window.removeEventListener('pywebviewready',listener)
  },[])
  useEffect(()=>{
    let active=true
    const load=async()=>{
      try{
        const [mr,hr]=await Promise.all([fetch('/api/models',{cache:'no-store'}),fetch('/api/health',{cache:'no-store'})])
        if(!mr.ok||!hr.ok)throw new Error('Сервер недоступен')
        const m:ModelInfo=await mr.json();const h=await hr.json()
        if(active){setModels(m);setDeviceInfo(h.device)}
      }catch{if(active)setDeviceInfo(null)}
    }
    void load();const timer=setInterval(load,1750)
    return()=>{active=false;clearInterval(timer)}
  },[])
  useEffect(()=>{
    const id=jobId.current
    if(!id||!busy||busyUpload)return
    let cancelled=false
    const poll=async()=>{
      try{
        const r=await fetch(`/api/jobs/${id}`,{cache:'no-store'})
        if(!r.ok)throw new Error(await errMsg(r))
        const j:Job=await r.json()
        if(cancelled)return
        setJob(j)
        if(j.status==='completed'){
          setResultText(j.result?.text??'')
          notifications.show({title:'Готово',message:'Расшифровка завершена',color:'green',icon:<IconCheck size={16}/>})
        }
        if(j.status==='failed') notifications.show({title:'Ошибка распознавания',message:j.error||'Неизвестная ошибка',color:'red'})
      }catch(e){if(!cancelled){setJob(old=>old?{...old,status:'failed',error:String(e)}:old)}}
    }
    void poll();const timer=setInterval(poll,1150)
    return()=>{cancelled=true;clearInterval(timer)}
  },[job?.status,busyUpload])
  useEffect(()=>()=>{if(url)URL.revokeObjectURL(url)},[url])

  const pickFile=useCallback((f:File|null)=>{
    if(!f)return
    if(!formats.split(',').includes(`.${f.name.split('.').pop()?.toLowerCase()}`)){
      notifications.show({message:'Неподдерживаемый формат',color:'red'});return
    }
    if(!f.size||f.size>200*1024*1024){notifications.show({message:'Файл пустой или превышает 200 МБ',color:'red'});return}
    setUrl(old=>{if(old)URL.revokeObjectURL(old);return URL.createObjectURL(f)})
    setFile(f);setJob(null);jobId.current=null;setResultText('')
  },[])
  const onDrop=(e:DragEvent)=>{e.preventDefault();setDrag(false);if(!busy)pickFile(e.dataTransfer.files[0])}
  const upload=async()=>{
    if(!file||busy)return
    if(!models?.models[model]?.installed){setPage('models');notifications.show({message:'Сначала скачайте выбранную модель',color:'yellow'});return}
    if(mode==='vad'&&!models.vad.installed){setPage('models');notifications.show({message:'Для VAD сначала загрузите модель сегментации',color:'yellow'});return}
    const body=new FormData();body.append('file',file);body.append('model',model);body.append('mode',mode);body.append('device',device)
    setBusyUpload(true);setJob(null);setResultText('')
    try{
      const r=await fetch('/api/jobs',{method:'POST',headers:auth(),body})
      if(!r.ok)throw new Error(await errMsg(r))
      const data=await r.json()
      jobId.current=data.job_id
      setJob({id:data.job_id,status:'queued',progress:0,detail:'В очереди'})
    }catch(e){notifications.show({message:String(e),color:'red'})}
    finally{setBusyUpload(false)}
  }
  const manage=async(kind:'vad'|ModelChoice,remove=false)=>{
    if(busy)return
    try{
      const r=await fetch(kind==='vad'?`/api/vad${remove?'':'/download'}`:`/api/models/${kind}${remove?'':'/download'}`,{
        method:remove?'DELETE':'POST',headers:{...auth(),...(kind==='vad'&&!remove?{'Content-Type':'application/json'}:{})},
        body:kind==='vad'&&!remove?JSON.stringify({token:vadToken}):undefined,
      })
      if(!r.ok)throw new Error(await errMsg(r))
      if(kind==='vad')setVadToken('')
      const mr=await fetch('/api/models');if(mr.ok)setModels(await mr.json())
      notifications.show({message:remove?'Модель удалена':'Загрузка запущена',color:remove?'gray':'indigo'})
    }catch(e){notifications.show({title:'Операция не выполнена',message:String(e),color:'red'})}
  }
  const exportText=async(ext:'txt'|'srt')=>{
    if(!job?.result)return
    const content=ext==='txt'?resultText:(job.result.segments.map((s,i)=>`${i+1}\n${srtTime(s.start)} --> ${srtTime(s.end)}\n${s.text}`).join('\n\n'))
    const filename=`${baseName(file?.name||'transcription')}.${ext}`
    try{
      if(window.pywebview?.api?.save_text){
        const written=await window.pywebview.api.save_text(filename,content)
        if(written)notifications.show({message:'Файл сохранён',color:'green'})
      }else{
        const link=document.createElement('a');const uri=URL.createObjectURL(new Blob(['\ufeff'+content],{type:'text/plain;charset=utf-8'}))
        link.href=uri;link.download=filename;document.body.appendChild(link);link.click();link.remove()
        setTimeout(()=>URL.revokeObjectURL(uri),10000)
      }
    }catch(e){notifications.show({title:'Ошибка сохранения',message:String(e),color:'red'})}
  }
  const installedCount=Number(models?.models.russian.installed||false)+Number(models?.models.multilingual.installed||false)
  const nav=[{key:'transcribe',label:'Расшифровка',icon:IconMicrophone},{key:'models',label:'Модели',icon:IconFolder},{key:'settings',label:'Настройки',icon:IconSettings}] as const

  return <div className="app-root">
    {isDesktop&&<div className="windowbar">
      <div className="windowbrand pywebview-drag-region"><IconMicrophone size={18} stroke={2}/><span>GigaScribe</span><span className="windowver">1.3.4</span></div>
      <div className="windowactions">
        <button aria-label="Свернуть" onClick={()=>void window.pywebview?.api?.minimize()}><IconMinus size={16}/></button>
        <button aria-label="Развернуть" onClick={()=>void window.pywebview?.api?.toggle_maximize()}><IconSquare size={13}/></button>
        <button aria-label="Закрыть" className="windowclose" onClick={()=>void window.pywebview?.api?.close()}><IconX size={17}/></button>
      </div>
    </div>}
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-heading"><div className="app-mark"><IconMicrophone size={21}/></div><div><Text fw={700} size="sm">GigaScribe</Text><Text c="dimmed" size="xs">Локальная транскрибация</Text></div></div>
        <Text size="xs" c="dimmed" tt="uppercase" fw={600} mt={26} mb={8} ml={13}>Рабочее пространство</Text>
        <nav className="nav">
          {nav.map(n=><button key={n.key} className={page===n.key?'active':''} onClick={()=>setPage(n.key)}><n.icon size={19} stroke={1.7}/>{n.label}</button>)}
        </nav>
        <div className="sidebar-bottom">
          <Group gap="xs" wrap="nowrap"><Badge variant="light" color={deviceInfo?.cuda_available?'green':'gray'} size="sm">{deviceInfo?.cuda_available?'CUDA доступна':'CPU'}</Badge><Text size="xs" c="dimmed" lineClamp={1}>{installedCount}/2 моделей</Text></Group>
          <Text size="xs" c="dimmed" mt={9}>Всё аудио обрабатывается на вашем компьютере</Text>
        </div>
      </aside>
      <main className="content"><ScrollArea h="100%" type="scroll" scrollbarSize={7}><div className="content-inner">
        {page==='transcribe'&&<Stack gap="lg">
          <header className="page-header"><Text size="sm" c="dimmed" mb={3}>РАБОЧАЯ ОБЛАСТЬ</Text><Title order={2}>Расшифровка аудио</Title><Text c="dimmed" size="sm" mt={6}>Загрузите запись, выберите модель и получите текст.</Text></header>
          <SimpleGrid cols={{base:1,md:2}} spacing="md">
            <Card className="panel" padding="lg" radius="lg" withBorder>
              <Group justify="space-between" mb="md"><Text fw={600}>1. Аудиофайл</Text><Badge variant="light" color="gray">До 200 МБ</Badge></Group>
              <div className={'dropzone'+(drag?' dragging':'')} onDragOver={e=>{e.preventDefault();setDrag(true)}} onDragLeave={()=>setDrag(false)} onDrop={onDrop}>
                <IconUpload size={28} stroke={1.45} color="var(--mantine-color-dimmed)"/>
                <Text fw={600} size="sm" mt="sm">{file?file.name:'Перетащите аудиофайл сюда'}</Text>
                <Text size="xs" c="dimmed" mt={4}>{file?`${fmtSize(file.size)} · готов к обработке`:'MP3, M4A, WAV, FLAC, OGG и другие'}</Text>
                <FileButton onChange={pickFile} accept={formats} disabled={busy}>{(props) => <Button {...props} size="xs" variant="default" mt="lg" leftSection={<IconFolder size={15}/>}>{file?'Заменить файл':'Выбрать файл'}</Button>}</FileButton>
              </div>
              {url&&<audio className="audio-player" src={url} controls preload="metadata"/>}
            </Card>
            <Card className="panel" padding="lg" radius="lg" withBorder>
              <Text fw={600} mb="md">2. Параметры</Text>
              <Stack gap="md">
                <NativeSelect label="Модель распознавания" value={model} onChange={e=>setModel(e.currentTarget.value as ModelChoice)} disabled={busy} data={[{value:'russian',label:'Русская · GigaAM v3 RNNT'},{value:'multilingual',label:'Мультиязычная · GigaAM CTC'}]}/>
                {!models?.models[model]?.installed&&<Alert color="yellow" variant="light" icon={<IconInfoCircle size={17}/>} title="Модель не установлена"><Text size="xs">Перейдите в «Модели», чтобы скачать её.</Text></Alert>}
                <div><Text size="sm" fw={500} mb={7}>Обработка длинной записи</Text><SegmentedControl fullWidth value={mode} onChange={v=>setMode(v as Mode)} disabled={busy} data={[{label:'Стандартная',value:'standard'},{label:'Умная (VAD)',value:'vad'}]}/><Text c="dimmed" size="xs" mt={7}>{mode==='vad'?'Разделение по речи; требуется отдельная модель VAD.':'Быстрое разделение на фрагменты до 23 секунд.'}</Text></div>
                <NativeSelect label="Устройство" value={device} onChange={e=>setDevice(e.currentTarget.value as Device)} disabled={busy} data={[{label:'Автоматически (GPU при наличии)',value:'auto'},{label:'NVIDIA CUDA',value:'cuda',disabled:!deviceInfo?.cuda_available},{label:'Процессор (CPU)',value:'cpu'}]}/>
              </Stack>
            </Card>
          </SimpleGrid>
          <Card className="panel" padding="lg" radius="lg" withBorder>
            <Group justify="space-between" align="center"><div><Text fw={600}>3. Запуск</Text><Text size="xs" c="dimmed" mt={3}>{job?.detail||'Результат появится ниже после обработки'}</Text></div><Button leftSection={busy?<IconRefresh size={17}/>:<IconPlayerPlay size={17}/>} loading={busy} disabled={!file||!models?.models[model]?.installed||(mode==='vad'&&!models?.vad.installed)} onClick={()=>void upload()}>Расшифровать</Button></Group>
            {busy&&<Progress mt="md" value={job?.progress||5} animated radius="sm"/>}
            {job?.status==='failed'&&<Alert color="red" mt="md" title="Ошибка распознавания">{job.error}</Alert>}
          </Card>
          <Card className="panel" padding="lg" radius="lg" withBorder>
            <Group justify="space-between" align="center" mb="md"><Group gap="sm"><Text fw={600}>Результат</Text>{ready&&<Badge color="green" variant="light">Готово · {fmtTime(job!.result!.duration)}</Badge>}</Group><Group gap="xs"><Button variant="default" size="xs" leftSection={<IconCopy size={15}/>} disabled={!ready} onClick={()=>void navigator.clipboard.writeText(resultText).then(()=>notifications.show({message:'Скопировано',color:'green'}))}>Копировать</Button><Button variant="light" size="xs" leftSection={<IconDownload size={15}/>} disabled={!ready} onClick={()=>void exportText('txt')}>TXT</Button><Button variant="default" size="xs" disabled={!ready} onClick={()=>void exportText('srt')}>SRT</Button></Group></Group>
            <Textarea minRows={9} autosize maxRows={25} placeholder="Здесь появится расшифрованный текст. Его можно будет отредактировать перед сохранением." value={resultText} onChange={e=>setResultText(e.currentTarget.value)} disabled={!ready}/>
            {ready&&<Text size="xs" c="dimmed" mt="sm">Распознано на {job!.result!.device.toUpperCase()} · {job!.result!.segments.length} фрагментов. Изменения текста учитываются при экспорте в TXT.</Text>}
          </Card>
        </Stack>}
        {page==='models'&&<Stack gap="lg">
          <header className="page-header"><Text size="sm" c="dimmed" mb={3}>УПРАВЛЕНИЕ ХРАНИЛИЩЕМ</Text><Title order={2}>Модели</Title><Text c="dimmed" size="sm" mt={6}>Скачивайте только нужные модели. Удаление не влияет на остальные.</Text></header>
          {(['russian','multilingual'] as const).map(choice=>{
            const m=models?.models[choice]
            return <Card key={choice} className="panel" padding="lg" withBorder radius="lg">
              <Group justify="space-between" align="flex-start" wrap="nowrap"><Group align="flex-start" gap="md" wrap="nowrap"><div className="model-icon">{choice==='russian'?<IconMicrophone size={22}/>:<IconMicrophone size={22}/>}</div><div><Text fw={600}>{choice==='russian'?'GigaAM v3 Russian':'GigaAM Multilingual'}</Text><Text c="dimmed" size="sm" mt={3}>{choice==='russian'?'Русская речь, пунктуация и регистр':'RU · EN · KK · KY · UZ'}</Text><Group gap="xs" mt="sm"><Badge variant="light" color={m?.installed?'green':m?.status==='failed'?'red':'gray'}>{m?.status==='downloading'?'Загружается':m?.installed?'Установлена':m?.status==='failed'?'Ошибка':'Не установлена'}</Badge>{m?.size_bytes? <Text size="xs" c="dimmed">{fmtSize(m.size_bytes)}</Text>:null}</Group></div></Group>
                {m?.installed?<Button variant="subtle" color="red" size="sm" leftSection={<IconTrash size={16}/>} disabled={busy} onClick={()=>setPendingDelete(choice)}>Удалить</Button>:<Button variant="default" size="sm" leftSection={<IconCloudDownload size={17}/>} loading={m?.status==='downloading'} disabled={busy} onClick={()=>void manage(choice)}>Скачать</Button>}
              </Group>
              {m?.status==='downloading'&&<><Progress value={m.progress} mt="md" size="sm"/><Text c="dimmed" size="xs" mt={5}>{m.detail}</Text></>}
              {m?.error&&<Alert color="red" mt="md">{m.error}</Alert>}
            </Card>
          })}
          <Card className="panel" padding="lg" withBorder radius="lg">
            <Group align="flex-start" justify="space-between"><div><Text fw={600}>VAD · pyannote segmentation 3.0</Text><Text size="sm" c="dimmed" mt={4}>Необязательная модель для умного деления длинных записей</Text><Badge mt="sm" color={models?.vad.installed?'green':'gray'} variant="light">{models?.vad.installed?'Установлена':models?.vad.status==='downloading'?'Загрузка...':'Не установлена'}</Badge></div>{models?.vad.installed?<Button color="red" variant="subtle" disabled={busy} leftSection={<IconTrash size={16}/>} onClick={()=>setPendingDelete('vad')}>Удалить</Button>:null}</Group>
            {!models?.vad.installed&&<Stack gap="xs" mt="lg"><Text size="sm" c="dimmed">Для загрузки нужен токен Hugging Face после принятия условий доступа к pyannote/segmentation-3.0. Токен не сохраняется.</Text><Group align="end"><TextInput style={{flex:1}} label="Hugging Face token" type="password" value={vadToken} onChange={e=>setVadToken(e.currentTarget.value)} placeholder="hf_…" autoComplete="off"/><Button variant="default" leftSection={<IconCloudDownload size={16}/>} loading={models?.vad.status==='downloading'} disabled={!vadToken.trim()||busy} onClick={()=>void manage('vad')}>Скачать VAD</Button></Group></Stack>}
            {models?.vad.status==='downloading'&&<Progress animated value={models.vad.progress} mt="md"/>}
            {models?.vad.error&&<Alert color="red" mt="md">{models.vad.error}</Alert>}
          </Card>
          <Paper className="path-panel" p="md" radius="md"><Group gap="xs"><IconFolder size={18}/><Text size="sm" fw={600}>Расположение моделей</Text></Group><Text c="dimmed" size="sm" mt={7} style={{wordBreak:'break-all'}}>{models?.path||'Получение пути...'}</Text></Paper>
        </Stack>}
        {page==='settings'&&<Stack gap="lg">
          <header className="page-header"><Text c="dimmed" size="sm" mb={3}>СИСТЕМА</Text><Title order={2}>Настройки и диагностика</Title><Text c="dimmed" size="sm" mt={6}>Выбранное устройство видно при расшифровке.</Text></header>
          <Card className="panel" withBorder radius="lg" padding="lg"><Group gap="sm" mb="lg"><IconCpu size={22}/><Text fw={600}>Ускорение NVIDIA</Text></Group><Stack gap="sm"><Group justify="space-between"><Text size="sm" c="dimmed">CUDA</Text><Badge variant="light" color={deviceInfo?.cuda_available?'green':'red'}>{deviceInfo?.cuda_available?'Доступна':'Недоступна'}</Badge></Group><Divider/><Group justify="space-between"><Text size="sm" c="dimmed">Устройство</Text><Text size="sm">{deviceInfo?.gpu_name||'CPU'}</Text></Group><Divider/><Group justify="space-between"><Text size="sm" c="dimmed">Версия PyTorch</Text><Text size="sm">{deviceInfo?.torch_version||'—'}</Text></Group><Divider/><Group justify="space-between"><Text size="sm" c="dimmed">CUDA runtime</Text><Text size="sm">{deviceInfo?.cuda_runtime||'—'}</Text></Group></Stack>{deviceInfo?.reason&&<Alert mt="lg" color="yellow" icon={<IconInfoCircle size={17}/>}>{deviceInfo.reason}</Alert>}</Card>
          <Card className="panel" withBorder radius="lg" padding="lg"><Text fw={600}>Файлы моделей</Text><Text size="sm" c="dimmed" mt={8} style={{wordBreak:'break-all'}}>{models?.path||'—'}</Text><Text size="xs" c="dimmed" mt="sm">Все веса остаются на диске при обновлении установленной версии приложения.</Text></Card>
        </Stack>}
        <div className="footer"><Text size="xs" c="dimmed">GigaScribe 1.3.4 · Обработка данных локально</Text><Text size="xs" c="dimmed">GigaAM by Sber</Text></div>
      </div></ScrollArea></main>
    </div>
    <Modal centered title="Удалить модель?" opened={pendingDelete!==null} onClose={()=>setPendingDelete(null)}>
      <Text size="sm">Файлы выбранной модели будут удалены с компьютера. При необходимости её можно скачать повторно.</Text>
      <Group justify="flex-end" mt="xl"><Button variant="default" onClick={()=>setPendingDelete(null)}>Отмена</Button><Button color="red" onClick={()=>{const choice=pendingDelete;setPendingDelete(null);if(choice)void manage(choice,true)}}>Удалить модель</Button></Group>
    </Modal>
  </div>
}
export default App
