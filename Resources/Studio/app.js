const $ = id => document.getElementById(id);
let state, token, reference = null, selected = null, selectedImage = null, viewedMesh = null, inspected = null;
const shown = new Set();
let freshConversation=false;
const group=job=>job?.settings?.conversation_id||job?.id;
const traces = new Map();
const file = path => '/file?path=' + encodeURIComponent(path);
const settings = () => ({output:'image', motion:'', new_conversation:freshConversation, ...(selected?.settings?.conversation_id&&!freshConversation?{conversation_id:selected.settings.conversation_id,conversation_title:selected.settings.conversation_title}:{})});
function toast(text) {$('toast').textContent = text; $('toast').hidden = false; setTimeout(() => $('toast').hidden = true, 7000);}
async function post(route, data) {
  if(['message','build','import','animation','history'].includes(route)&&state.version!==2)throw Error('Ative a atualização: Pasta de trabalhos → Reiniciar-Estudio.cmd. Depois atualize esta página.');
  const res = await fetch('/api/' + route, {method:'POST', headers:{'Content-Type':'application/json','X-Studio-Token':token}, body:JSON.stringify(data)});
  const result = await res.json(); if (!res.ok) throw Error(result.error || 'A operação falhou.'); return result;
}
function message(text, kind='assistant', ref=null) {
  document.querySelector('.welcome')?.remove();
  const el = document.createElement('div'); el.className = 'message ' + kind;
  if(ref){const img=document.createElement('img');img.src=file(ref);img.alt='Referência';el.append(img);}
  const content=document.createElement('span');content.textContent=text;el.append(content);$('messages').append(el);el.scrollIntoView({block:'end'});
}
function trace(job) {
  let box=traces.get(job.id);
  if(!box){document.querySelector('.welcome')?.remove();box=document.createElement('details');box.className='trace';
    const summary=document.createElement('summary'),dot=document.createElement('span'),title=document.createElement('span'),time=document.createElement('span');
    dot.className='traceDot';title.className='traceLabel';time.className='traceTime';summary.append(dot,title,time);
    const meter=document.createElement('div');meter.className='stageMeter';meter.append(document.createElement('span'));
    const list=document.createElement('ol');box.append(summary,meter,list);$('messages').append(box);traces.set(job.id,box);
  }
  box.className='trace '+job.status;
  const running=job.status==='running';
  box.querySelector('.traceLabel').textContent=running?(job.progress||job.stage):({complete:'Etapas concluídas',failed:'Uma etapa precisa de atenção',cancelled:'Trabalho interrompido',answered:'Escolhas definidas',awaiting_answers:'Aguardando suas escolhas',needs_input:'Aguardando seu pedido'})[job.status]||job.stage;
  const elapsed=Math.max(0,Math.round((Date.parse(job.finished||new Date().toISOString())-Date.parse(job.started))/1000));
  box.querySelector('.traceTime').textContent=elapsed<60?elapsed+'s':Math.floor(elapsed/60)+'min '+elapsed%60+'s';
  const meter=box.querySelector('.stageMeter'),bar=meter.firstElementChild;
  meter.hidden=!running&&job.status!=='complete';
  if(running&&Number.isFinite(job.progress_value)&&job.progress_max>0){meter.className='stageMeter';bar.style.width=Math.min(100,100*job.progress_value/job.progress_max)+'%';}
  else {meter.className='stageMeter '+(running?'indeterminate':'finished');bar.style.width='';}
  const list=box.querySelector('ol');list.replaceChildren();
  for(const event of job.events||[]){const li=document.createElement('li');li.textContent=event.stage+(event.seconds?' · '+Math.round(event.seconds)+'s':'');list.append(li);}
  let error=box.querySelector('.traceError');
  if(job.error&&!error){error=document.createElement('div');error.className='traceError';error.textContent='Não foi possível concluir esta etapa. Os resultados anteriores foram preservados.';const open=document.createElement('button');open.className='quiet';open.textContent='Abrir pasta de diagnóstico';open.onclick=()=>post('folder',{path:job.folder}).catch(e=>toast(e.message));error.append(open);box.append(error);}
}
function showQuestions(job) {
  const pending=job.reply.questions, box=document.createElement('div');box.className='questions';const choices={},custom={};
  let sending=false;
  async function answer(q,value,isCustom,options,button){
    if(sending)return;
    choices[q.id]=value;if(isCustom)custom[q.id]=value;else delete custom[q.id];
    for(const sibling of options.querySelectorAll('button'))sibling.classList.toggle('chosen',sibling===button);
    if(Object.keys(choices).length!==pending.questions.length)return;
    sending=true;for(const b of box.querySelectorAll('button,input'))b.disabled=true;
    try{
      await post('message',{text:'__ez3d_answers__:'+JSON.stringify({token:pending.token,answers:choices,custom_answers:custom}),settings:job.settings});
      const summary=document.createElement('details'),title=document.createElement('summary');title.textContent='Escolhas definidas';summary.append(title);
      const text=document.createElement('p');text.textContent=pending.questions.map(q=>q.question+' '+choices[q.id]).join(' · ');summary.append(text);box.replaceWith(summary);await refresh();
    }catch(e){sending=false;for(const b of box.querySelectorAll('button,input'))b.disabled=false;toast(e.message);}
  }
  for(const q of pending.questions){
    const field=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=q.question;field.append(legend);
    const options=document.createElement('div');options.className='options';
    for(const value of q.options){const button=document.createElement('button');button.textContent=value;button.onclick=()=>answer(q,value,false,options,button);options.append(button);}
    const other=document.createElement('button');other.textContent='Outra opção…';other.type='button';
    const entry=document.createElement('div');entry.className='customAnswer';entry.hidden=true;
    const input=document.createElement('input');input.placeholder='Escreva sua opção';input.maxLength=1000;input.setAttribute('aria-label','Outra resposta: '+q.question);
    const use=document.createElement('button');use.textContent='Usar resposta';use.onclick=()=>{if(input.value.trim())answer(q,input.value.trim(),true,options,other);};
    input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();use.click();}};
    other.onclick=()=>{entry.hidden=!entry.hidden;if(!entry.hidden)input.focus();};entry.append(input,use);options.append(other,entry);field.append(options);box.append(field);
  }
  document.querySelector('.welcome')?.remove();$('messages').append(box);box.scrollIntoView({block:'end'});
}
async function preview(job) {
  inspected=job;
  const a=job?.artifacts || {}, images=job?.reply?.images || [];
  const path=a.image || images[0]?.path;
  document.querySelector('main').classList.toggle('noAsset',!(path||a.character||a.textured||a.geometry));
  $('assetStage').textContent=a.fbx?'Com esqueleto':a.textured?'3D com textura':'Imagem';
  document.querySelector('.importPanel').hidden=!(a.fbx||a.textured);
  $('showMesh').hidden=!(a.character||a.textured||a.geometry);
  if(!path){selectedImage=null;$('imagePreview').hidden=true;$('imagePreview').removeAttribute('src');$('emptyPreview').hidden=false;}
  if(path){selectedImage=path;$('imagePreview').src=file(path);$('imagePreview').hidden=false;$('emptyPreview').hidden=true;}
  const mesh=a.character || a.textured || a.geometry;
  $('addAnimation').hidden=!a.fbx;
  $('addAnimation').disabled=!!state.current;
  $('showMesh').disabled=!mesh;
  $('import').disabled=!(job?.status==='complete' && (a.fbx || a.textured));
  $('build3d').disabled=!path||!!state.current;
  $('previewStatus').textContent=job?.rig_validation ? 'Rig humanoide · conferir a deformação na prévia.' : (mesh?'Arraste para girar · use a roda para aproximar.':'');
  const labels={image:'Imagem gerada',geometry:'Geometria',textured:'Malha com materiais',fbx:'Personagem para Unreal',character:'Prévia com esqueleto'};
  $('artifactList').replaceChildren();
  for(const [kind,path] of Object.entries(a)){const link=document.createElement('a');link.className='artifact';link.href=file(path);link.target='_blank';
    const type=document.createElement('span');type.className='fileType';type.textContent=path.split('.').pop().toUpperCase();
    const content=document.createElement('div'),name=document.createElement('strong'),label=document.createElement('small');name.textContent=path.split(/[\\/]/).pop();label.textContent=labels[kind]||kind;content.append(name,label);link.append(type,content);
    if(path.endsWith('.glb'))link.onclick=e=>{e.preventDefault();openViewer(path);};$('artifactList').append(link);
  }
  for(const clip of job?.animations||[]){const link=document.createElement('a');link.className='artifact';link.href=file(clip.fbx);link.textContent='▶ '+clip.name;link.title='Animação separada · mesmo esqueleto';$('artifactList').append(link);}
  if(job?.animations?.length)$('previewStatus').textContent='✦ '+job.animations.length+' animação(ões) · malha base preservada';
  if(!Object.keys(a).length){const p=document.createElement('p');p.textContent='Os arquivos aparecem conforme cada etapa termina.';$('artifactList').append(p);}
  if($('viewerDialog').open && mesh && !viewedMesh){
    await openViewer(mesh);
  }
}
async function openViewer(mesh=null){
  const a=inspected?.artifacts||{};mesh=mesh||a.character||a.textured||a.geometry;
  if(!$('viewerDialog').open)$('viewerDialog').showModal();$('toggleViewer').setAttribute('aria-expanded','true');
  $('meshPreview').hidden=!mesh;$('viewerEmpty').hidden=!!mesh;
  if(!mesh)return;
  $('viewerAssetName').textContent=mesh.split(/[\\/]/).pop();
  try{const viewer=await import('./viewer.js');const clips=await viewer.load(file(mesh));viewedMesh=mesh;
    $('animationSelect').replaceChildren();
    if(inspected?.animations?.length){const base=new Option('Pose neutra',a.character);base.dataset.path=a.character;base.selected=mesh===a.character;$('animationSelect').append(base);for(const clip of inspected.animations){const option=new Option(clip.name,clip.preview);option.dataset.path=clip.preview;option.selected=clip.preview===mesh;$('animationSelect').append(option);}}
    else clips.forEach((name,i)=>$('animationSelect').append(new Option(name,i)));
    $('animationSelect').hidden=!clips.length&&!inspected?.animations?.length;$('playAnimation').hidden=!clips.length;$('playAnimation').textContent='Pausar';
    $('viewerStatus').textContent=clips.length?'Animação em reprodução · arraste para girar':'Malha estática · arraste para girar';
  }catch(e){$('viewerStatus').textContent='Não foi possível carregar a prévia 3D.';toast(e.message);}
}
async function closeViewer(){ $('viewerDialog').close();$('toggleViewer').setAttribute('aria-expanded','false');viewedMesh=null;const viewer=await import('./viewer.js');viewer.dispose();}
function clearConversation(){ $('messages').replaceChildren();shown.clear();traces.clear();$('receipt').hidden=true; }
function selectJob(job){if(group(selected)!==group(job))clearConversation();freshConversation=false;selected=job;preview(job);renderJobs();refresh().catch(e=>toast(e.message));}
function renderJobs(){
  const ids=new Set(),jobs=state.jobs.filter(j=>{const id=group(j);if(ids.has(id))return false;ids.add(id);return true;});
  $('jobs').replaceChildren();$('jobCount').textContent=jobs.length;
  for(const job of jobs){const b=document.createElement('button');b.className=selected?.id===job.id?'selected':'';
    const title=document.createElement('strong');title.textContent=job.title||(job.kind==='import'?'Importação · '+job.project.split(/[\\/]/).pop():(job.reply?.folder?.split(/[\\/]/).pop()?.replace(/-\d{8}-.*/,'').replaceAll('-',' ')||'Criação'));
    b.oncontextmenu=e=>{e.preventDefault();showHistoryMenu(e,job,title.textContent);};
    const sub=document.createElement('small');sub.textContent=({complete:'Concluído',failed:'Precisa de atenção',running:'Em criação',answered:'Escolhas definidas',awaiting_answers:'Aguardando escolhas',needs_input:'Conversa'})[job.status]||job.status;
    if(job.animations?.length)sub.textContent+=' · ▶ '+job.animations.length;
    b.append(title,sub);b.onclick=()=>selectJob(job);$('jobs').append(b);
  }
}
function showHistoryMenu(event,job,title){
  document.getElementById('historyMenu')?.remove();
  const menu=document.createElement('div');menu.id='historyMenu';menu.setAttribute('role','menu');
  for(const [label,hide] of [['Renomear',false],['Excluir histórico',true]]){
    const button=document.createElement('button');button.textContent=label;button.onclick=async()=>{
      menu.remove();const name=hide?null:prompt('Nome do chat',title);if(!hide&&name===null)return;
      try{await post('history',{job:job.id,title:name,hide});if(hide){traces.get(job.id)?.remove();traces.delete(job.id);document.querySelectorAll('[data-job="'+job.id+'"]').forEach(e=>e.remove());if(selected?.id===job.id)selected=null;}await refresh();}catch(e){toast(e.message);}
    };menu.append(button);
  }
  document.body.append(menu);menu.style.left=Math.min(event.clientX,innerWidth-200)+'px';menu.style.top=Math.min(event.clientY,innerHeight-100)+'px';
  setTimeout(()=>document.addEventListener('click',()=>menu.remove(),{once:true}),0);
}
async function refresh(initial=false){
  const res=await fetch('/api/state');state=await res.json();token=state.token;
  $('serviceNotice').hidden=state.version===2;
  if(!state.current)state.current=state.jobs.find(j=>j.status==='running')||null;
  if(!freshConversation){const latest=state.jobs.find(j=>group(j)===group(selected));selected=latest||selected||state.current||state.jobs[0];}
  const visibleJobs=state.jobs.filter(j=>!freshConversation&&group(j)===group(selected));
  const previous=$('project').value;$('project').replaceChildren(new Option('Selecione um projeto Unreal',''));
  for(const p of state.projects)$('project').append(new Option(p.name+' · '+p.path,p.path));$('project').value=previous;
  $('activity').hidden=!state.current;$('send').disabled=!!state.current;
  if(state.current&&group(state.current)===group(selected)){trace(state.current);}
  for(const job of [...visibleJobs].reverse()){
    if(shown.has(job.id)||job.status==='running')continue;shown.add(job.id);
    const user=job.user_text||(state.chat.find(m=>m.job===job.id)?.text);if(user&&!user.startsWith('__ez3d_answers__:'))message(user,'user',job.reference);
    trace(job);
    if(job.reply?.questions&&job.questions_active)showQuestions(job);
    if(job.reply?.message)message(job.reply.message);
    if(job.reply?.images?.length){
      const gallery=document.createElement('div');gallery.className='result';gallery.dataset.job=job.id;
      for(const image of job.reply.images){const img=document.createElement('img');img.src=file(image.path);img.alt=({front:'Frente',back:'Costas',left:'Perfil esquerdo',right:'Perfil direito'})[image.view]||'Imagem criada';img.onclick=()=>{selectJob(job);selectedImage=image.path;$('largeImage').src=img.src;$('imageDialog').showModal();};gallery.append(img);}
      $('messages').append(gallery);
    }
    if(job.receipt){message('Importação concluída: '+job.receipt.assets.length+' assets. A lista está pronta para copiar para o ez3D.');$('receipt').hidden=false;$('receiptText').textContent=job.ez3d_message;}
  }
  if(state.current&&state.current.kind!=='import'&&(!selected||selected.id===state.current.id)){selected=state.current;await preview(selected);}
  else if(selected){selected=state.jobs.find(j=>j.id===selected.id)||selected;await preview(state.jobs.find(j=>group(j)===group(selected)&&(j.artifacts?.image||j.artifacts?.fbx||j.artifacts?.textured))||selected);}
  else if(!freshConversation&&state.jobs.length){selected=state.jobs[0];await preview(selected);}else await preview(null);
  renderJobs();
}
$('send').onclick=async()=>{const text=$('prompt').value.trim();if(!text)return;try{
  const cfg=settings();if($('viewerDialog').open)await closeViewer();await post('message',{text,reference,settings:cfg});selected=null;freshConversation=false;clearConversation();$('prompt').value='';reference=null;$('reference').hidden=true;await refresh();
}catch(e){toast(e.message);}};
$('prompt').onkeydown=e=>{if(e.ctrlKey&&e.key==='Enter'){e.preventDefault();$('send').click();}};
document.addEventListener('paste',async e=>{const item=[...(e.clipboardData?.items||[])].find(x=>x.type.startsWith('image/'));if(!item)return;e.preventDefault();
  const reader=new FileReader();reader.onload=async()=>{try{const result=await post('upload',{data:reader.result});reference=result.path;const img=document.createElement('img');img.src=file(reference);img.alt='Referência anexada';const remove=document.createElement('button');remove.textContent='Remover';remove.onclick=()=>{reference=null;$('reference').hidden=true;};$('reference').replaceChildren(img,remove);$('reference').hidden=false;}catch(e){toast(e.message);}};reader.readAsDataURL(item.getAsFile());
});
$('output').onchange=()=>{$('motionField').hidden=$('output').value!=='animated';$('rigNotice').hidden=!['rig','animated'].includes($('output').value);};
$('openCreationFolder').onclick=()=>{if(inspected)post('folder',{path:inspected.artifacts?.fbx?inspected.folder:(inspected.reply?.folder||inspected.folder)}).catch(e=>toast(e.message));};
$('showImage').onclick=()=>{if(selectedImage){$('largeImage').src=file(selectedImage);$('imageDialog').showModal();}};
$('closeImage').onclick=()=>$('imageDialog').close();
$('showMesh').onclick=()=>openViewer();
$('toggleViewer').onclick=()=>$('viewerDialog').open?closeViewer():openViewer();
$('closeViewer').onclick=closeViewer;
$('viewerDialog').addEventListener('cancel',e=>{e.preventDefault();closeViewer();});
$('playAnimation').onclick=async()=>{const viewer=await import('./viewer.js');const playing=viewer.togglePlayback();$('playAnimation').textContent=playing?'Pausar':'Reproduzir';};
$('animationSelect').onchange=async()=>{const option=$('animationSelect').selectedOptions[0];if(option.dataset.path)return openViewer(option.dataset.path);const viewer=await import('./viewer.js');viewer.selectClip(Number($('animationSelect').value));$('playAnimation').textContent='Pausar';};
$('frameMesh').onclick=async()=>{const viewer=await import('./viewer.js');viewer.frame();};
$('build3d').onclick=()=>$('buildDialog').showModal();
async function buildSelected(output){try{$('buildDialog').close();if($('viewerDialog').open)await closeViewer();await post('build',{job:inspected.id,settings:{output}});selected=null;await refresh();}catch(e){toast(e.message);}}
$('buildRig').onclick=()=>buildSelected('rig');$('buildStatic').onclick=()=>buildSelected('static');$('cancelBuild').onclick=()=>$('buildDialog').close();
$('addAnimation').onclick=async()=>{const motion=prompt('Qual animação adicionar a este personagem?');if(!motion)return;try{if($('viewerDialog').open)await closeViewer();await post('animation',{job:inspected.id,motion});selected=null;await refresh();}catch(e){toast(e.message);}};
$('pause').onclick=()=>post('pause',{}).then(x=>toast(x.message)).catch(e=>toast(e.message));
$('clearView').onclick=()=>{$('messages').replaceChildren();traces.clear();};
$('newJob').onclick=()=>{freshConversation=true;selected=null;clearConversation();preview(null);$('prompt').focus();};
$('import').onclick=async()=>{if(!$('project').value)return toast('Selecione o projeto de destino.');try{await post('import',{job:inspected.id,project:$('project').value});await refresh();}catch(e){toast(e.message);}};
$('addProject').onclick=async()=>{const path=window.prompt('Caminho completo do arquivo .uproject:');if(path)try{await post('project',{path});await refresh();}catch(e){toast(e.message);}};
$('openRoot').onclick=()=>post('folder',{path:state.root}).catch(e=>toast(e.message));
$('copyReceipt').onclick=()=>navigator.clipboard.writeText($('receiptText').textContent).then(()=>toast('Lista copiada. Cole no chat do ez3D.'));
await refresh(true);setInterval(()=>refresh().catch(()=>{}),1800);
