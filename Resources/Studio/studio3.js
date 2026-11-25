let genStart=0;function tickTimer(){const el=$('generationTimer');if(!el)return;if(!genStart){el.textContent='';return;}const s=Math.max(0,Math.floor((Date.now()-genStart)/1000));el.textContent=String(Math.floor(s/60)).padStart(2,'0')+':'+String(s%60).padStart(2,'0');}setInterval(tickTimer,1000);
import * as viewer from './viewer3.js';
import {thumbnail} from './vendor/ez3d-thumbnail.js';
import {createGenerationParticles} from './generation_particles.js?v=3';
const $=id=>document.getElementById(id);
const showGenerationParticles=createGenerationParticles($('generationParticles'));
let session, selected, reference, library=[], busy=false, observedJob, lastFinished, exportJob;
const fileURL=path=>'/file?path='+encodeURIComponent(path);
let libraryPaintKey='';
let studioStopped=false,pollTimer;
let typedMessage='',typeTimer;
const visualProgress={job:null,confirmed:0,displayed:0,ceiling:0,updated:0,total:6,active:false,lastFrame:0};
function animateProgress(now){if(studioStopped)return;
  const p=visualProgress,delta=p.lastFrame?Math.min((now-p.lastFrame)/1000,.1):0;p.lastFrame=now;
  if(p.active){
    // Continuous visual estimate inside the current stage; never claims completion.
    const age=(now-p.updated)/1000;
    const target=p.confirmed+(p.ceiling-p.confirmed)*(1-Math.exp(-age/32));
    p.displayed=Math.max(p.displayed,p.displayed+(target-p.displayed)*(1-Math.exp(-delta*2.8)));
    $('generationBar').style.width=(p.displayed/p.total*100)+'%';
  }
  if(!$('playback').hidden){
    const {time,duration}=viewer.clipPlayback(Number($('animationSelect').value));
    $('clipPosition').max=duration;
    $('clipPosition').value=time;
    const label=time.toFixed(2)+' / '+duration.toFixed(2)+' s';
    if($('clipTime').textContent!==label)$('clipTime').textContent=label;
  }
  requestAnimationFrame(animateProgress);
}
requestAnimationFrame(animateProgress);
function typeStage(message){if(message===typedMessage)return;typedMessage=message;clearInterval(typeTimer);const target=$('generationStage');if(matchMedia('(prefers-reduced-motion: reduce)').matches){target.textContent=message;return;}let count=0;target.textContent='';typeTimer=setInterval(()=>{target.textContent=message.slice(0,++count);if(count>=message.length)clearInterval(typeTimer);},22);}
async function renderProgress(job){
  const scanning=job?.kind==='animation';
  if(job?.kind!=='model')showGenerationParticles(null);
  $('generation').hidden=!job;
  $('generation').classList.toggle('motionProgress',scanning);
  viewer.setMotionScan(scanning && !!selected && selected.id===job.source_job);
  visualProgress.active=!!job;if(!job)return;
  let progress=job.generation_progress;
  // The file also supports updating a running worker without restarting its server.
  if(!progress){try{const response=await fetch(fileURL(job.folder+'/trellis-request.progress.json'));if(response.ok)progress=await response.json();}catch{}}
  progress=progress||{};
  $('generationName').textContent=job.title||'Sua criação';
  typeStage(progress.message||job.progress||job.stage||'Preparando');
  const measured=Number.isFinite(progress.value)&&progress.max>0;
  const value=measured?Math.min(progress.max,Math.max(0,progress.value)):0;
  $('generationTrack').classList.toggle('measured',measured);
  $('generationTrack').classList.toggle('working',!progress.measured);
  const p=visualProgress;
  const total=progress.max||6;if(p.total!==total){p.displayed=p.displayed/p.total*total;p.confirmed=0;p.total=total;}
  if(p.job!==job.id){Object.assign(p,{job:job.id,confirmed:0,displayed:0,updated:performance.now(),total:progress.max||6,referenceSynced:false});}
  if(job.kind==='model'&&!p.referenceSynced){
    try{
      let image=job.reference;
      if(!image){const response=await fetch(fileURL(job.folder+'/trellis-request.json'));if(response.ok){const data=await response.json();image=data.image?.replace(/^\/mnt\/([a-z])\//,(_,drive)=>drive.toUpperCase()+':/');}}
      if(image && session?.current?.id===job.id){reference=image;$('referenceImage').src=fileURL(image);$('referenceImage').hidden=false;$('uploadHint').hidden=true;$('removeReference').hidden=false;$('imageName').textContent='Referência da criação';$('modelName').value=job.title||'';p.referenceSynced=true;showGenerationParticles(fileURL(image));}
    }catch{}
  }
  if(value!==p.confirmed){p.confirmed=Math.max(p.confirmed,value);p.updated=performance.now();}
  p.ceiling=progress.measured?p.confirmed:measured?Math.min(p.total-.03,Math.floor(p.confirmed)+.92):.9;
  p.ceiling=Math.max(p.confirmed,p.ceiling);
  $('generationTrack').setAttribute('title','Progresso visual aproximado. Etapas e passos vêm do gerador.');
  $('generationTrack').removeAttribute('aria-valuenow');
  $('generationTrack').setAttribute('aria-valuetext',progress.detail||progress.message||job.stage);
  genStart=Date.parse(job.started)||0;tickTimer();
  $('generationCount').textContent=measured?`Etapa ${Math.min(progress.stage_count,progress.stage_index+1)} de ${progress.stage_count}`:'';
  $('generationDetail').textContent=progress.detail||(!measured?'Processando · esta etapa não fornece porcentagem de conclusão':job.kind==='model'?'Referência → geometria → materiais PBR → arquivo 3D':'Uma ferramenta por vez na GPU.');
}
function renderLibrary(){const key=JSON.stringify([library,selected?.id,$('search').value]);if(key!==libraryPaintKey){libraryPaintKey=key;paintLibrary();}}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,8000);}
async function request(route,data){const response=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json','X-Studio-Token':session.token},body:JSON.stringify(data)});const body=await response.json();if(!response.ok)throw Error(body.error||'Falha no servidor');return body;}
function action(fn){return async()=>{try{await fn();}catch(error){toast(error.message);}};}
const stages=['Model','Texture','Rig','Animation'];
let stage='Model';
function mode(next){
  stage=next;
  for(const name of stages){
    const active=name===stage;
    $(name.toLowerCase()+'Panel').hidden=!active;
    $('mode'+name).classList.toggle('active',active);
    $('mode'+name).setAttribute('aria-pressed',String(active));
  }
  updateControls();
  if(stage==='Texture')setPreviewStyle('textured');
  if(stage==='Animation'&&!presetsLoaded&&session)action(loadPresets)();
}
function updateControls(){
  const name=selected?.name||'Nenhum modelo selecionado';
  $('textureTarget').textContent=$('rigTarget').textContent=name;
  for(const id of ['compareTextured','compareSolid'])$(id).disabled=!selected;
  $('openTextures').disabled=busy||!selected;
  $('inspectJoints').disabled=busy||!selected?.rigged;
  $('rigNotice').textContent=selected?.review_required?'O vínculo deste personagem precisa de revisão.':selected?.rigged?'Esqueleto salvo. Confira os pontos antes de animar.':'Prepare apenas personagens que precisam se mover.';
$('capeOption').hidden=!selected?.rigged||!selected.review_required;$('rearGarment').disabled=busy;for(const card of $('motionLibrary').children)card.disabled=busy||!selected?.rigged||selected.motion_compatible===false;$('editJoints').disabled=!selected?.rigged;$('saveJoints').disabled=busy;$('shutdown').disabled=busy;const ready=session?.runtime?.generation_ready===true;$('generate').disabled=busy||!reference||!ready;$('modelNotice').hidden=ready;$('modelNotice').textContent=session?.runtime?.message||'Conectando ao servidor…';$('animate').disabled=busy||!selected?.rigged||selected.motion_compatible===false||!$('motion').value.trim();$('gameRig').disabled=$('prepareRig').disabled=busy||!selected||!session?.runtime?.rig_ready;$('prepareRig').textContent=selected?.rigged?'Recalcular vínculo':'Preparar esqueleto';$('prepareRig').title=selected?.rigged?'Recalcula os pesos em uma cópia. Não garante corrigir deformações; o movimento ainda precisa de validação.':(session?.runtime?.rig_message||'Preparar esqueleto TokenRig');$('animationTarget').textContent=selected?.name||'Selecione um personagem à direita';$('animationNotice').hidden=!!selected?.rigged&&selected.motion_compatible!==false&&!selected.review_required;$('animationNotice').textContent=selected?.review_required?'Este modelo apresentou deformações no teste. O rig precisa de revisão antes de uso final no jogo.':selected?.motion_compatible===false?'Este esqueleto está salvo, mas precisa de adaptação para os movimentos humanoides do Kimodo.':selected?'Prepare o esqueleto deste personagem antes de gerar movimentos.':'Escolha um modelo com esqueleto na biblioteca para gerar ou visualizar movimentos.';for(const id of ['openAssetFolder','export'])$(id).disabled=!selected||busy;}
let presetsLoaded = false;
let pendingPreset;
async function loadPresets() {
  const response = await fetch('/api/motion-library');
  if (!response.ok) throw Error('Biblioteca de animações indisponível.');
  const presets = await response.json();
  const cards = $('motionLibrary');
  cards.replaceChildren();
  for (const preset of presets) {
    const button = document.createElement('button');
    const preview = document.createElement('img');
    preview.src = fileURL(preset.preview);
    preview.alt = '';
    preview.loading = 'lazy';
    const name = document.createElement('span');
    name.textContent = preset.name;
    button.append(preview, name);
    button.title = 'Aplicar e reproduzir ' + preset.name;
    button.onclick = action(async () => {
      if (busy || !selected?.rigged || selected.motion_compatible === false) return;
      const rearGarment = !$('capeOption').hidden && $('rearGarment').checked;
      const saved = !rearGarment && selected.animations.find(clip => clip.preset === preset.id);
      if (saved) return showModel(saved.preview);
      busy = true;
      updateControls();
      try {
        const result = await request('/api/animation', {job:selected.id, preset:preset.id, rear_garment:rearGarment});
        pendingPreset = result.job;
        observedJob = result.job;
        await poll();
      } catch (error) {
        busy = false;
        updateControls();
        throw error;
      }
    });
    cards.append(button);
  }
  presetsLoaded = true;
  updateControls();
}
function paintLibrary(){const query=$('search').value.toLowerCase();$('library').replaceChildren();$('modelCount').textContent=library.length;$('libraryEmpty').hidden=library.length>0;for(const item of library.filter(x=>x.name.toLowerCase().includes(query))){const button=document.createElement('button');button.className='modelCard'+(selected?.id===item.id?' selected':'');const symbol=document.createElement('div');symbol.className='modelThumb';symbol.textContent='◇';const preview=document.createElement('img');preview.alt=item.name;preview.loading='lazy';if(item.thumbnail){preview.src=fileURL(item.thumbnail);symbol.replaceWith(preview);}else if(session?.runtime?.catalog_version){thumbnail(fileURL(item.preview)).then(async data=>{preview.src=data;if(symbol.isConnected)symbol.replaceWith(preview);try{await request('/api/thumbnail',{job:item.id,data});}catch{}}).catch(()=>{});}const name=document.createElement('strong');name.textContent=item.name;const detail=document.createElement('small');detail.textContent=item.animations.length?`${item.animations.length} animações${item.review_required?' · revisar rig':''}`:item.rigged?(item.motion_compatible===false||item.review_required?'Rig para revisão':'Com esqueleto'):'Modelo 3D';const media=document.createElement('div');media.className='modelCardMedia';
media.append(item.thumbnail?preview:symbol);button.append(media,name,detail);
let clayPending=false;
media.addEventListener('pointerenter',()=>{
  if(!item.preview||clayPending||media.querySelector('.clayThumb'))return;
  clayPending=true;
  thumbnail(fileURL(item.preview),true).then(data=>{
    if(!media.isConnected)return;
    const clay=document.createElement('img');clay.alt='';clay.className='clayThumb';clay.src=data;
    media.append(clay);
  }).catch(()=>{}).finally(()=>{clayPending=false;});
});
media.addEventListener('pointermove',event=>{
  const bounds=media.getBoundingClientRect();
  media.style.setProperty('--clay-x',event.clientX-bounds.left+'px');
  media.style.setProperty('--clay-y',event.clientY-bounds.top+'px');
});
button.oncontextmenu=event=>openCatalogMenu(event,item);button.onclick=action(()=>select(item));$('library').append(button);}}
async function showModel(path){$('emptyState').hidden=true;const clips=await viewer.load(fileURL(path));$('playback').hidden=!clips.length;$('animationSelect').replaceChildren(...clips.map((name,index)=>new Option(name,index)));const stats=viewer.state();$('showSkeleton').disabled=!stats.boneCount;$('showSkeleton').setAttribute('aria-pressed','false');$('showSkeleton').classList.remove('active');$('meshStats').textContent=`${stats.triangles.toLocaleString('pt-BR')} triângulos${stats.boneCount?' · '+stats.boneCount+' ossos':''}`;}
async function select(item, preview=item.preview){if(!$('jointPanel').hidden){viewer.endJointEdit();$('jointPanel').hidden=true;}if(selected?.id!==item.id)$('rearGarment').checked=false;selected=item;$('assetTitle').textContent=item.name;$('assetInfo').textContent=item.review_required?'Rig em revisão · deformação ainda não aprovada':item.rigged?(item.motion_compatible===false?'Esqueleto para revisão · use como modelo estático':'Modelo com esqueleto · animações separadas'):(item.artifacts?.textured?.endsWith('geometry.glb')?'Geometria sem textura':'Modelo com materiais');renderLibrary();updateControls();$('savedMotions').hidden=!item.animations.length;$('clipList').replaceChildren();for(const clip of item.animations){const button=document.createElement('button');button.textContent=clip.name;button.oncontextmenu=event=>openCatalogMenu(event,item,clip);button.onclick=action(()=>showModel(clip.preview));$('clipList').append(button);}await showModel(preview);}
async function upload(file){if(!file?.type.startsWith('image/'))throw Error('Escolha uma imagem PNG, JPEG ou WebP.');const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});const body=await request('/api/upload',{data});reference=body.path;$('referenceImage').src=fileURL(reference);$('referenceImage').hidden=false;$('uploadHint').hidden=true;$('removeReference').hidden=false;$('imageName').textContent=file.name||'Imagem colada';updateControls();}
async function poll(){try{const response=await fetch('/api/state');if(!response.ok)throw Error('Servidor indisponível');session=await response.json();library=session.library||[];busy=!!session.current;$('runtimeStatus').textContent=busy?(session.current.kind==='joint_edit'?'Ajustando vínculo · CPU':session.current.kind==='import'?'Importando no Unreal':'Processamento local'):'Estúdio local';await renderProgress(session.current);if(busy){observedJob=session.current.id;if(!selected){$('assetTitle').textContent=session.current.title||'Sua criação';$('emptyState').hidden=true;}}else if(observedJob&&lastFinished!==observedJob){const job=session.jobs.find(x=>x.id===observedJob);if(job){lastFinished=observedJob;toast(job.status==='complete'?(job.kind==='repair'?'Pesos recalculados. A deformação ainda precisa ser validada.':'Etapa concluída.'):job.error||'Etapa interrompida.');const item=library.find(x=>x.id===observedJob);if(item)await select(item,pendingPreset===job.id?item.animations.at(-1)?.preview:item.preview);if(pendingPreset===job.id)pendingPreset=null;}}renderLibrary();updateControls();}catch(error){$('runtimeStatus').textContent='Servidor desconectado';toast(error.message);} }
for(const name of stages)$('mode'+name).onclick=()=>mode(name);
$('upload').onclick=()=>$('fileInput').click();$('fileInput').onchange=action(()=>upload($('fileInput').files[0]));
document.addEventListener('paste',event=>{const image=[...(event.clipboardData?.items||[])].find(x=>x.type.startsWith('image/'));if(image){event.preventDefault();action(()=>upload(image.getAsFile()))();}});
window.addEventListener('ez3d-clipboard-image',event=>{if(typeof event.detail==='string'&&event.detail.startsWith('data:image/png;base64,'))action(async()=>{const blob=await(await fetch(event.detail)).blob();await upload(new File([blob],'imagem-colada.png',{type:'image/png'}));})();});
$('upload').ondragover=e=>e.preventDefault();$('upload').ondrop=e=>{e.preventDefault();action(()=>upload(e.dataTransfer.files[0]))();};
const extraViews={};
document.querySelectorAll('#extraViews input').forEach(input=>{input.onchange=action(async()=>{const file=input.files[0];input.value='';if(!file?.type.startsWith('image/'))return;const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=reject;reader.readAsDataURL(file);});const body=await request('/api/upload',{data});extraViews[input.dataset.view]=body.path;const slot=input.parentElement;slot.querySelector('img').src=data;slot.querySelector('img').hidden=false;slot.classList.add('filled');});});
// right-click clears a view
document.querySelectorAll('#extraViews .viewSlot').forEach(slot=>{slot.title='Clique para escolher · botão direito remove';slot.oncontextmenu=event=>{event.preventDefault();delete extraViews[slot.querySelector('input').dataset.view];slot.querySelector('img').hidden=true;slot.classList.remove('filled');};});
const engineGuide={
trellis:{title:'TRELLIS.2 (Microsoft)',lines:[['O que faz','Cria o 3D a partir de uma única imagem, com materiais PBR.'],['Ideal para','Objetos, itens de cenário e personagens quando você só tem uma foto.'],['Atenção','Ele inventa o que não aparece, como as costas. Modelo pesado (até 2 milhões de triângulos); na Máxima pode levar cerca de 10 min numa RTX 3090.']]},
hunyuan:{title:'Hunyuan3D-2mv (Tencent)',lines:[['O que faz','Cria a forma a partir de até 4 vistas e depois pinta a textura.'],['Ideal para','Personagens e objetos quando você tem frente, lados e costas. Modelo leve (40 a 90 mil triângulos), melhor para jogo e web.'],['Atenção','As fotos precisam ser do mesmo item, na mesma pose. Textura simples, sem PBR completo. Licença não vale na UE, Reino Unido e Coreia do Sul.']]}};
function showEngineInfo(){const guide=engineGuide[$('engine').value];$('engineInfo').replaceChildren(Object.assign(document.createElement('strong'),{textContent:guide.title}),...guide.lines.map(([label,text])=>{const row=document.createElement('p');row.append(Object.assign(document.createElement('b'),{textContent:label+': '}),text);return row;}));}
showEngineInfo();
$('engine').onchange=()=>{showEngineInfo();$('extraViews').hidden=$('engine').value!=='hunyuan';updateGenerationQuality();};
$('removeReference').onclick=()=>{reference=null;$('referenceImage').hidden=true;$('uploadHint').hidden=false;$('removeReference').hidden=true;$('imageName').textContent='';updateControls();};
$('motion').oninput=updateControls;$('search').oninput=renderLibrary;$('refresh').onclick=poll;
$('animate').onclick=action(async()=>{const result=await request('/api/animation',{job:selected.id,motion:$('motion').value,rear_garment:!$('capeOption').hidden&&$('rearGarment').checked});pendingPreset=result.job;observedJob=result.job;await poll();});
$('generate').onclick=action(async()=>{const result=await request('/api/model',{reference,name:$('modelName').value,auto_rig:false,quality:generationProfile(),engine:$('engine').value,views:$('engine').value==='hunyuan'?extraViews:undefined});observedJob=result.job;await poll();});
$('gameRig').onclick=action(async()=>{const result=await request('/api/rig',{job:selected.id,game_ready:true});observedJob=result.job;await poll();});
$('prepareRig').onclick=action(async()=>{const result=await request('/api/rig',{job:selected.id});observedJob=result.job;await poll();});
$('showSkeleton').onclick=()=>{const visible=viewer.toggleSkeleton();$('showSkeleton').setAttribute('aria-pressed',String(visible));$('showSkeleton').classList.toggle('active',visible);};
$('focusFace').onclick=viewer.focusFace;$('frame').onclick=viewer.frame;$('rotate').onclick=viewer.toggleRotation;$('playPause').onclick=()=>{viewer.togglePlayback();$('playPause').textContent=viewer.state().playing?'Pausar':'Reproduzir';};$('animationSelect').onchange=()=>viewer.selectClip(Number($('animationSelect').value));
$('clipPosition').oninput=()=>{
  viewer.seekClip(Number($('animationSelect').value),Number($('clipPosition').value));
  $('playPause').textContent='Reproduzir';
};
function setPreviewStyle(style){
  viewer.setStyle(style);
  document.querySelectorAll('[data-style]').forEach(button=>button.classList.toggle('chosen',button.dataset.style===style));
  $('compareTextured').setAttribute('aria-pressed',String(style==='textured'));
  $('compareSolid').setAttribute('aria-pressed',String(style==='solid'));
}
for(const button of document.querySelectorAll('[data-style]'))button.onclick=()=>setPreviewStyle(button.dataset.style);
$('compareTextured').onclick=()=>setPreviewStyle('textured');
$('compareSolid').onclick=()=>setPreviewStyle('solid');
$('openTextures').onclick=()=>$('openAssetFolder').click();
$('inspectJoints').onclick=()=>$('editJoints').click();

$('snapshot').onclick=()=>{const link=document.createElement('a');link.href=viewer.snapshot();link.download='previa.png';link.click();};
$('openRoot').onclick=action(()=>request('/api/folder',{path:session.root}));$('openAssetFolder').onclick=action(()=>request('/api/asset-folder',{job:selected.id}));
$('export').onclick=()=>{$('project').replaceChildren(...session.projects.map(x=>new Option(x.name+' · '+x.path,x.path)));$('importDialog').showModal();};$('confirmImport').onclick=action(async()=>{const result=await request('/api/import',{job:selected.id,project:$('project').value});observedJob=result.job;$('importDialog').close();await poll();});
$('addProject').onclick=()=>{$('projectPathPanel').hidden=false;$('projectPath').focus();};
$('saveProjectPath').onclick=action(async()=>{const path=$('projectPath').value.trim().replace(/^"|"$/g,'');if(!path)throw Error('Informe o caminho completo do arquivo .uproject.');$('projectPathMessage').textContent='Verificando projeto…';try{await request('/api/project',{path});await poll();$('project').replaceChildren(...session.projects.map(x=>new Option(x.name+' · '+x.path,x.path)));const match=session.projects.find(x=>x.path.toLowerCase()===path.toLowerCase());if(match)$('project').value=match.path;$('projectPathMessage').textContent='Projeto adicionado.';$('projectPathPanel').hidden=true;}catch(error){$('projectPathMessage').textContent=error.message;throw error;}});
for(const button of document.querySelectorAll('[data-close]'))button.onclick=()=>$(button.dataset.close).close();
try{viewer.initialize();await poll();pollTimer=setInterval(async()=>{await poll();if(exportJob){const job=session?.jobs.find(x=>x.id===exportJob);if(job?.status==='complete'&&job.export_file){const link=document.createElement('a');link.href=fileURL(job.export_file);link.download=job.export_file.split(/[\\/]/).pop();link.click();exportJob=null;}else if(job?.status==='failed')exportJob=null;}},2000);}catch(error){toast(error.message);}

let catalogTarget;
function openCatalogMenu(event,item,clip){event.preventDefault();event.stopPropagation();catalogTarget={item,clip};const menu=$('catalogMenu');menu.hidden=false;menu.style.left=Math.min(event.clientX,innerWidth-210)+'px';menu.style.top=Math.min(event.clientY,innerHeight-105)+'px';$('deleteItem').textContent=clip?'Remover animação':'Remover modelo da biblioteca';}
async function editCatalog(change){if(!catalogTarget)return;const {item,clip}=catalogTarget;await request('/api/library-edit',{job:item.id,target:clip?'clip':'model',clip:clip?.preview,...change});$('catalogMenu').hidden=true;await poll();if(selected?.id===item.id){const refreshed=library.find(x=>x.id===item.id);if(refreshed)await select(refreshed);else{selected=null;viewer.clear();$('emptyState').hidden=false;$('assetTitle').textContent='Sua próxima criação';$('assetInfo').textContent='Escolha um modelo na biblioteca';$('meshStats').textContent='';$('playback').hidden=true;$('showSkeleton').disabled=true;$('clipList').replaceChildren();renderLibrary();updateControls();}}}
$('renameItem').onclick=()=>{$('catalogMenu').hidden=true;$('renameName').value=catalogTarget.clip?.name||catalogTarget.item.name;$('renameDialog').showModal();$('renameName').select();};
$('confirmRename').onclick=action(async()=>{await editCatalog({name:$('renameName').value});$('renameDialog').close();toast('Nome atualizado.');});
$('renameName').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();$('confirmRename').click();}};
$('deleteItem').onclick=action(async()=>{await editCatalog({hide:true});toast('Removido da biblioteca. Os arquivos foram preservados.');});
document.addEventListener('pointerdown',event=>{if(!$('catalogMenu').contains(event.target))$('catalogMenu').hidden=true;});
document.addEventListener('keydown',event=>{if(event.key==='Escape')$('catalogMenu').hidden=true;});
function setupResizers(){
 const main=document.querySelector('main');let sizes;try{sizes=JSON.parse(localStorage.getItem('ez3d-panel-widths')||'{}');}catch{sizes={};}
 function apply(side,value){const other=side==='left'?(sizes.right||270):(sizes.left||286);const available=Math.max(210,main.clientWidth-72-10-260-other);sizes[side]=Math.max(210,Math.min(value,Math.min(side==='left'?560:640,available)));document.documentElement.style.setProperty('--'+side+'-width',sizes[side]+'px');const handle=$(side==='left'?'resizeLeft':'resizeRight');handle.setAttribute('aria-valuenow',Math.round(sizes[side]));handle.setAttribute('aria-valuemin','210');handle.setAttribute('aria-valuemax',Math.round(available));try{localStorage.setItem('ez3d-panel-widths',JSON.stringify(sizes));}catch{}}
 for(const [id,side,sign] of [['resizeLeft','left',1],['resizeRight','right',-1]]){const handle=$(id);if(sizes[side])apply(side,sizes[side]);handle.onpointerdown=event=>{const start=event.clientX,width=side==='left'?document.querySelector('.inputPanel').getBoundingClientRect().width:document.querySelector('.libraryPanel').getBoundingClientRect().width;handle.setPointerCapture(event.pointerId);handle.classList.add('dragging');document.body.classList.add('resizing');handle.onpointermove=move=>apply(side,width+(move.clientX-start)*sign);};handle.onpointerup=handle.onpointercancel=()=>{handle.onpointermove=null;handle.classList.remove('dragging');document.body.classList.remove('resizing');};handle.onkeydown=event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();apply(side,(sizes[side]||(side==='left'?286:270))+(event.key==='ArrowRight'?20:-20)*sign);}};}
 window.addEventListener('resize',()=>{apply('left',sizes.left||286);apply('right',sizes.right||270);});
}
setupResizers();

function generationProfile(){
  const maximum = $('qualityLevel').value === 'maximum';
  if ($('creationType').value === 'character') return maximum ? 'character_maximum' : 'character';
  return maximum ? 'maximum' : 'balanced';
}
function updateGenerationQuality(){
  const profile = generationProfile();
  if($('engine').value==='hunyuan'){$('qualityDescription').textContent=$('qualityLevel').value==='maximum'?'Hunyuan3D · mais detalhe · até 90 mil triângulos':'Hunyuan3D · mais rápido · até 40 mil triângulos';return;}
  $('qualityDescription').textContent = {
    maximum:'TRELLIS.2 · até 2 milhões de triângulos · PBR 4K',
    balanced:'TRELLIS.2 · até 240 mil triângulos · PBR 4K',
    character:'Mais passos de geometria · até 500 mil triângulos · PBR 4K',
    character_maximum:'Mais passos de geometria · até 2 milhões de triângulos · PBR 4K'
  }[profile];
}
$('qualityLevel').onchange = $('creationType').onchange = updateGenerationQuality;
$('shutdown').onclick=action(async()=>{if(!confirm('Encerrar o '+(window.BRAND||{name:'ez3D'}).name+'? O servidor local será desligado.'))return;await request('/api/shutdown',{});studioStopped=true;clearInterval(pollTimer);clearInterval(typeTimer);viewer.dispose();if(window.ez3dDesktop)location.href='ez3d://close';else document.body.replaceChildren(Object.assign(document.createElement('p'),{textContent:(window.BRAND||{name:'ez3D'}).name+' encerrado. Use o atalho da área de trabalho para iniciar novamente.'}));});

let jointMapping={};
const jointRoleNames={Hips:'Quadril',Spine1:'Coluna inferior',Spine2:'Coluna média',Chest:'Peito',Neck1:'Pescoço',Head:'Cabeça'};
for(const [side,label] of [['Left','esquerdo'],['Right','direito']])for(const [part,title] of [['Shoulder','Ombro'],['Arm','Braço'],['ForeArm','Antebraço'],['Hand','Punho/mão'],['Leg','Quadril/perna'],['Shin','Joelho'],['Foot','Tornozelo'],['ToeBase','Base dos dedos do pé']])jointRoleNames[side+part]=title+' '+label;
$('jointRole').replaceChildren(new Option('Auxiliar / sem identificação',''),...Object.entries(jointRoleNames).map(([value,label])=>new Option(label,value)));
function fillJointFields(name){const item=viewer.selectJoint(name);if(!item)return;$('jointSelect').value=name;$('jointRole').value=item.role;['jointX','jointY','jointZ'].forEach((id,i)=>$(id).value=item.position[i].toFixed(5));}
function populateJoints(){const draft=viewer.getJointDraft();$('jointSelect').replaceChildren(...draft.map(x=>new Option((jointRoleNames[x.role]||'Auxiliar')+' · '+x.name,x.name)));if(draft.length)fillJointFields(draft[0].name);}
$('editJoints').onclick=action(async()=>{if(!selected?.rigged)return;await showModel(selected.preview);const response=await fetch('/api/joints?job='+encodeURIComponent(selected.id));const metadata=await response.json();if(!response.ok)throw Error(metadata.error||'Não foi possível ler as articulações.');jointMapping=metadata.mapping;viewer.beginJointEdit(jointMapping);$('jointPanel').hidden=false;$('showSkeleton').disabled=true;populateJoints();});
$('jointSelect').onchange=()=>fillJointFields($('jointSelect').value);
$('meshPreview').addEventListener('jointpick',event=>fillJointFields(event.detail));
function commitJointFields(){viewer.setJoint($('jointSelect').value,['jointX','jointY','jointZ'].map(x=>Number($(x).value)),$('jointRole').value);}
for(const id of ['jointX','jointY','jointZ','jointRole']){$(id).oninput=commitJointFields;$(id).onchange=commitJointFields;}
$('resetJoints').onclick=()=>{viewer.endJointEdit();viewer.beginJointEdit(jointMapping);populateJoints();};
$('closeJoints').onclick=action(async()=>{viewer.endJointEdit();$('jointPanel').hidden=true;await showModel(selected.preview);updateControls();});
$('saveJoints').onclick=action(async()=>{if(busy)throw Error('Aguarde a criação terminar antes de salvar o vínculo.');commitJointFields();const result=await request('/api/joints',{job:selected.id,configuration:{coordinate_space:'gltf_y_up',joints:viewer.getJointDraft()},recalculate:$('recalculateJoints').checked});observedJob=result.job;viewer.endJointEdit();$('jointPanel').hidden=true;await poll();});
