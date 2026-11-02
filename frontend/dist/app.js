'use strict';
const $ = id => document.getElementById(id);
const allowed = new Set(['.mp3','.wav','.m4a','.mp4','.flac','.ogg','.opus','.webm','.aac','.wma','.mov','.mkv']);
const state = {file:null,url:null,model:'russian',online:null,ffmpeg:true,job:null,text:'',busy:false,models:null,modelPath:''};
const desktopToken = new URLSearchParams(location.search).get('token');
const authHeaders = () => desktopToken ? {'X-GigaScribe-Token':desktopToken} : {};
let modelsRefresh = false;
let pollToken = 0;
const toggle = (id, visible) => $(id).classList.toggle('hidden', !visible);
const size = bytes => bytes >= 1073741824 ? (bytes/1073741824).toFixed(2)+' ГБ' : bytes < 1048576 ? Math.round(bytes/1024)+' КБ' : (bytes/1048576).toFixed(1)+' МБ';
const time = sec => { const v = Math.floor(Number.isFinite(sec) ? sec : 0);return String(Math.floor(v/60)).padStart(2,'0')+':'+String(v%60).padStart(2,'0'); };
const srtTime = s => {const ms=Math.round(s*1000);return [Math.floor(ms/3600000),Math.floor(ms%3600000/60000),Math.floor(ms%60000/1000)].map(n=>String(n).padStart(2,'0')).join(':')+','+String(ms%1000).padStart(3,'0');};
const basename = filename => filename.replace(/\.[^.]+$/,'');
const escapeHtml = value => String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function setError(message) {
  toggle('status-error',Boolean(message));
  $('status-error').textContent=message||'';
}
function setModel(model){
 if(state.busy)return;
 state.model=model;
 for(const name of ['russian','multilingual']) {const el=$('model-'+name);el.classList.toggle('selected',name===model);el.setAttribute('aria-pressed',String(name===model));}
 updateControls();
 $('model-hint-text').textContent=model==='russian'?'Лучший выбор для русской речи. Модель умеет расставлять пунктуацию и приводить текст к читаемому виду.':'Распознаёт 5 языков. Результат может быть без заглавных букв и пунктуации — особенность CTC-модели.';
}
function updateControls(){
 const disabled=state.busy;
 $('start').disabled=disabled||!state.file||state.online===false||!state.ffmpeg||state.models?.[state.model]?.status!=='installed';
 $('start').textContent=state.busy?'◌  Обрабатываем аудио...':state.models?.[state.model]?.status==='installed'?'♫   Расшифровать аудио  →':'↓   Сначала скачайте выбранную модель';
 $('remove-file').disabled=disabled;
 $('file-input').disabled=disabled;
 for(const model of ['russian','multilingual'])$('model-'+model).disabled=disabled;
 const done=state.job?.status==='completed';
 $('copy').disabled=!done||!state.text;
 $('download-txt').disabled=!done||!state.text;
 $('download-srt').disabled=!done||!state.job?.result?.segments?.length;
 toggle('done-tag',done);
}
function renderJob(){
 const job=state.job;
 const status=job?.status;
 toggle('empty-state',!status);
 toggle('progress-state',status==='queued'||status==='processing');
 toggle('failed-state',status==='failed');
 toggle('transcript-content',status==='completed');
 $('result-box').classList.toggle('has-result',status==='completed');
 if(status==='queued'||status==='processing'){
   $('phase-detail').textContent=job.detail||'Запускаем обработку...';
   $('progress-label').textContent=job.progress+'%';
   $('progress-fill').style.width=job.progress+'%';
 }
 if(status==='failed')$('fail-message').textContent=job.error||'Проверьте файл и попробуйте ещё раз';
 if(status==='completed'){
   $('transcript-input').value=state.text;
   $('result-duration').textContent=time(job.result?.duration||0);
   $('result-model').textContent=job.model==='russian'?'GigaAM v3':'GigaAM Multilingual';
   updateWords();
 }
 updateControls();
}
function updateWords(){ $('result-count').textContent=(state.text.trim()?state.text.trim().split(/\s+/).length:0)+' слов'; }
function selectFile(file){
 if(!file||state.busy)return;
 const dot=file.name.lastIndexOf('.');
 if(dot<0||!allowed.has(file.name.slice(dot).toLowerCase())){setError('Неподдерживаемый формат аудио.');return;}
 if(file.size>200*1048576){setError('Файл слишком большой. Максимальный размер — 200 МБ.');return;}
 if(!file.size){setError('Файл пустой.');return;}
 if(state.url)URL.revokeObjectURL(state.url);
 state.file=file;state.url=URL.createObjectURL(file);
 state.job=null;state.text='';pollToken++;
 $('file-name').textContent=file.name;
 $('file-size').textContent=size(file.size)+' · Аудиозапись';
 $('audio-player').src=state.url;
 $('seek').value=0;$('position').textContent='00:00';$('duration').textContent='00:00';$('play-button').textContent='▶';
 toggle('drop-zone',false);toggle('file-card',true);
 setError('');renderJob();
}
function removeFile(){
 if(state.busy)return;
 if(state.url)URL.revokeObjectURL(state.url);
 $('audio-player').pause();$('audio-player').removeAttribute('src');
 $('file-input').value='';state.url=null;state.file=null;state.job=null;state.text='';pollToken++;
 toggle('drop-zone',true);toggle('file-card',false);setError('');renderJob();
}
async function parseError(response){
 try{let data=await response.json();return typeof data.detail==='string'?data.detail:'Ошибка '+response.status;}
 catch{return 'Ошибка '+response.status;}
}
async function start(){
 if(!state.file||state.busy)return;
 state.busy=true;state.text='';setError('');
 state.job={status:'queued',progress:0,detail:'Загрузка файла на локальный сервер',model:state.model};renderJob();
 const payload=new FormData();payload.set('file',state.file);payload.set('model',state.model);
 try{
   if(state.models?.[state.model]?.status!=='installed')throw new Error('Сначала скачайте выбранную модель');
   const res=await fetch('/api/jobs',{method:'POST',headers:authHeaders(),body:payload});
   if(!res.ok)throw new Error(await parseError(res));
   const data=await res.json();
   const token=++pollToken;
   state.job={id:data.job_id,status:'queued',progress:0,detail:'В очереди',model:state.model};renderJob();
   void poll(data.job_id,token);
 }catch(err){
   state.busy=false;state.job={status:'failed',error:err.message||String(err),model:state.model};
   setError(err.message||String(err));renderJob();
 }
}
async function poll(id,token){
 if(token!==pollToken)return;
 try{
   const response=await fetch('/api/jobs/'+encodeURIComponent(id),{cache:'no-store'});
   if(!response.ok)throw new Error(await parseError(response));
   const next=await response.json();
   if(token!==pollToken)return;
   state.job=next;
   if(next.status==='completed'){
     state.text=next.result?.text||'';state.busy=false;
   }else if(next.status==='failed'){
     state.busy=false;setError(next.error||'Ошибка распознавания');
   }
   renderJob();
   if(next.status==='queued'||next.status==='processing')setTimeout(()=>poll(id,token),900);
 }catch(err){
   if(token!==pollToken)return;
   state.busy=false;
   state.job={status:'failed',error:err.message||'Ошибка подключения',model:state.model};
   setError(state.job.error);renderJob();
 }
}
function save(text,filename,mime='text/plain;charset=utf-8'){
 const blob=new Blob(['\ufeff',text],{type:mime});const url=URL.createObjectURL(blob);
 const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();
 setTimeout(()=>URL.revokeObjectURL(url),1500);
}
function renderModels(){
 for(const model of ['russian','multilingual']){
   const data=state.models?.[model];
   const status=data?.status || 'checking';
   const action=$('action-'+model);
   const dot=$('dot-'+model);
   dot.className='manager-dot'+(status==='installed'?' ready':status==='downloading'?' downloading':status==='failed'?' failed':'');
   action.disabled=state.busy || !data || status==='downloading';
   action.classList.toggle('danger',status==='installed');
   action.textContent=status==='installed'?'Удалить':status==='downloading'?'Загрузка...':status==='failed'?'Повторить':'Скачать';
   const description=status==='installed'?`Установлена · ${size(data.size_bytes)}`:
     status==='downloading'?`${data.detail || 'Скачивание'} · ${data.progress||0}%`:
     status==='failed'?'Ошибка скачивания':status==='not_installed'?'Не установлена':'Проверка...';
   $('status-'+model).textContent=description;
   toggle('progress-'+model,status==='downloading');
   $('bar-'+model).style.width=(data?.progress||0)+'%';
 }
 $('models-path').textContent=state.modelPath||'Проверка расположения...';
 updateControls();
}
async function fetchModels(){
 if(modelsRefresh)return;
 modelsRefresh=true;
 try {
   const response=await fetch('/api/models',{cache:'no-store'});
   if(!response.ok)throw Error(await parseError(response));
   const data=await response.json();
   state.models=data.models;
   state.modelPath=data.path;
   renderModels();
 }catch(err){
   if(state.online===true){$('models-error').textContent='Не удалось получить список моделей: '+(err.message||err);toggle('models-error',true);}
 }finally{modelsRefresh=false;}
}
async function manageModel(model){
 const entry=state.models?.[model];
 if(!entry || state.busy || entry.status==='downloading')return;
 const removing=entry.status==='installed';
 if(removing && !confirm('Удалить '+(model==='russian'?'русскоязычную':'мультиязычную')+' модель с диска? При необходимости её можно будет скачать повторно.'))return;
 const action=$('action-'+model);action.disabled=true;
 toggle('models-error',false);
 try{
   const response=await fetch('/api/models/'+model+(removing?'':'/download'),{
      method:removing?'DELETE':'POST',headers:authHeaders()
   });
   if(!response.ok)throw Error(await parseError(response));
   await fetchModels();
 }catch(err){$('models-error').textContent=err.message||String(err);toggle('models-error',true)}
 finally{action.disabled=false;renderModels();}
}
async function checkServer(){
 try{const r=await fetch('/api/health');if(!r.ok)throw Error('offline');const data=await r.json();state.online=true;state.ffmpeg=data.ffmpeg!==false;}
 catch{state.online=false;}
 $('server-indicator').classList.toggle('offline',state.online===false);
 $('server-status').textContent=state.online ? 'Локальный сервер активен':'Сервер недоступен';
 if(!state.ffmpeg)setError('FFmpeg не найден. Установите FFmpeg и перезапустите сервер.');
 updateControls();
 if(state.online)void fetchModels();
}
for(let i=0;i<34;i++){
 const bar=document.createElement('span');
 bar.style.height=(16+65*Math.abs(Math.sin(i*.54+.3))*(.75+.25*Math.cos(i*.19)))+'%';
 $('hero-wave').append(bar);
}
$('pick-file').addEventListener('click',e=>{e.stopPropagation();$('file-input').click();});
$('file-input').addEventListener('change',e=>selectFile(e.target.files?.[0]));
$('drop-zone').addEventListener('click',()=>!state.busy&&$('file-input').click());
$('drop-zone').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();$('file-input').click();}});
$('drop-zone').addEventListener('dragover',e=>{e.preventDefault();$('drop-zone').classList.add('dragging');});
$('drop-zone').addEventListener('dragleave',()=> $('drop-zone').classList.remove('dragging'));
$('drop-zone').addEventListener('drop',e=>{e.preventDefault();$('drop-zone').classList.remove('dragging');selectFile(e.dataTransfer.files?.[0]);});
$('remove-file').addEventListener('click',removeFile);
for(const model of ['russian','multilingual']){
 $('model-'+model).addEventListener('click',()=>setModel(model));
 $('action-'+model).addEventListener('click',()=>manageModel(model));
}
$('copy-path').addEventListener('click',async()=>{
 try{await navigator.clipboard.writeText(state.modelPath);$('copy-path').textContent='✓ Скопировано';setTimeout(()=>$('copy-path').textContent='▢ Копировать путь',1300)}
 catch{setError('Не удалось скопировать путь: '+state.modelPath)}
});
$('start').addEventListener('click',start);
$('retry').addEventListener('click',start);
$('transcript-input').addEventListener('input',e=>{state.text=e.target.value;updateWords();updateControls();});
$('copy').addEventListener('click',async()=>{
 try{await navigator.clipboard.writeText(state.text);$('copy').textContent='✓ Скопировано';setTimeout(()=>$('copy').textContent='▢ Копировать',1600);}
 catch {setError('Не удалось скопировать текст. Выделите его и нажмите Ctrl+C.');}
});
$('download-txt').addEventListener('click',()=>save(state.text,basename(state.file?.name||'audio')+'_transcript.txt'));
$('download-srt').addEventListener('click',()=>{
 const segments=state.job?.result?.segments||[];
 save(segments.map((s,i)=>(i+1)+'\n'+srtTime(s.start)+' --> '+srtTime(s.end)+'\n'+s.text+'\n').join('\n'),basename(state.file?.name||'audio')+'_transcript.srt','text/srt;charset=utf-8');
});
const player=$('audio-player');
$('play-button').addEventListener('click',async()=>{
 if(player.paused){try{await player.play();}catch{setError('Браузер не может воспроизвести этот формат. Расшифровка всё равно доступна.');}}
 else player.pause();
});
player.addEventListener('play',()=>{$('play-button').textContent='Ⅱ';});
player.addEventListener('pause',()=>{$('play-button').textContent='▶';});
player.addEventListener('loadedmetadata',()=>{$('duration').textContent=time(player.duration);$('seek').max=String(player.duration||1);});
player.addEventListener('timeupdate',()=>{$('position').textContent=time(player.currentTime);$('seek').value=String(player.currentTime);});
$('seek').addEventListener('input',e=>{player.currentTime=Number(e.target.value);});
renderJob();renderModels();void checkServer();setInterval(checkServer,12000);setInterval(fetchModels,1400);
