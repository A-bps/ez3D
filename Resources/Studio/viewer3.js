import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {createIntro} from './hologram_intro.js';

const host=document.getElementById('meshPreview');
let renderer,scene,camera,controls,model,mixer,points,clips=[],playing=true,style='textured',observer;
let rootBone,lastRoot=null,loadVersion=0;
let environmentMap;
let skeletonHelper,jointPoints,joints=[],skeletonVisible=false;
let jointEditing=false,jointDraft=[],jointSelected=-1,jointDrag=null,jointLines=null;
const clock=new THREE.Clock(), temporary=new Set(), originals=new Map(), holoUniform={value:0};
const holoStart={value:0},holoBounds={value:new THREE.Vector2(0,1)};
let holoIntro,holoAnchor,holoRoot;
let motionScan=false;
let regionEnabled=false,regionPainting=false,regionDragging=false,regionRadius=.06,regionHeight=1;
let regionBrushes=[],regionMarkers=[],lassoPoints=[],lassoClosed=false,lassoMatrix=null,lassoCanvas=null,regionStrokeMoved=false;
let regionMarking=false,regionLandmarks=[],lassoCamera=null;
const revealDuration=7.2;

export function initialize(){
  if(renderer)return;
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:true,preserveDrawingBuffer:true});
  renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;host.append(renderer.domElement);
  scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(32,1,.01,1000);
  // Broad studio panels supply reflections for real metallic/roughness materials.
  const studio=new THREE.Scene();studio.background=new THREE.Color(.12,.12,.14);
  for(const [position,size,power] of [
    [[-4,3,3],[3,7],4],[[4,2,1],[2,6],3],[[0,5,-2],[5,3],2.5],[[0,1,5],[4,5],1.5]]){
    const panel=new THREE.Mesh(new THREE.PlaneGeometry(...size),new THREE.MeshBasicMaterial({color:new THREE.Color(power,power,power),side:THREE.DoubleSide}));
    panel.position.fromArray(position);panel.lookAt(0,0,0);studio.add(panel);
  }
  const pmrem=new THREE.PMREMGenerator(renderer);environmentMap=pmrem.fromScene(studio,.04);scene.environment=environmentMap.texture;scene.environmentIntensity=.75;
  studio.traverse(o=>{o.geometry?.dispose();o.material?.dispose();});pmrem.dispose();
  scene.add(new THREE.HemisphereLight(0xf2f3f5,0x4a4d55,2.2));
  const key=new THREE.DirectionalLight(0xffffff,3.1);key.position.set(3,5,5);scene.add(key);
  const rim=new THREE.DirectionalLight(0xa9ccff,1.1);rim.position.set(-4,2,-3);scene.add(rim);
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.autoRotateSpeed=1.86;
  lassoCanvas=document.createElement('canvas');lassoCanvas.className='lassoOverlay';host.append(lassoCanvas);
  const regionPoint=event=>{const r=renderer.domElement.getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))];};
  const addPoint=event=>{const p=regionPoint(event),last=lassoPoints.at(-1);if(!last||Math.hypot(p[0]-last[0],p[1]-last[1])>.002){lassoPoints.push(p);drawLasso();}};
  renderer.domElement.addEventListener('pointerdown',event=>{
    if(jointEditing&&event.button===0&&pickJoint(event)){event.preventDefault();event.stopImmediatePropagation();return;}
    if(regionEnabled&&regionMarking&&event.button===0){
      if(regionLandmarks.length>=6)return;
      const p=regionPoint(event),ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2(p[0]*2-1,1-p[1]*2),camera);
      if(!ray.intersectObject(model,true).length)return;
      regionLandmarks.push(p);drawLasso();host.dispatchEvent(new CustomEvent('landmarkchange',{detail:regionLandmarks.length}));return;
    }
    if(!regionEnabled||!regionPainting||event.button!==0)return;
    if(lassoClosed)clearRegion();regionDragging=true;regionStrokeMoved=false;renderer.domElement.setPointerCapture(event.pointerId);addPoint(event);
  },true);
  renderer.domElement.addEventListener('pointermove',event=>{if(jointDrag){moveJointDrag(event);return;}if(regionDragging&&lassoPoints.length<2048){regionStrokeMoved=true;addPoint(event);}});
  renderer.domElement.addEventListener('pointerup',()=>{if(jointDrag){jointDrag=null;controls.enabled=true;return;}if(!regionDragging)return;regionDragging=false;if(regionStrokeMoved&&lassoPoints.length>=3)closeRegion();});
  renderer.domElement.addEventListener('pointercancel',()=>{regionDragging=false;});
  controls.addEventListener('start',()=>{if(regionEnabled&&!regionPainting)clearRegion();});
  controls.zoomToCursor=true;controls.minDistance=.01;
  renderer.domElement.addEventListener('dblclick',event=>{
    if(event.button!==0||!model||regionEnabled||jointEditing)return;
    const rect=renderer.domElement.getBoundingClientRect(),ray=new THREE.Raycaster();
    ray.setFromCamera(new THREE.Vector2((event.clientX-rect.left)/rect.width*2-1,-(event.clientY-rect.top)/rect.height*2+1),camera);
    const hit=ray.intersectObject(model,true)[0];if(!hit)return;
    const offset=camera.position.clone().sub(controls.target);
    controls.target.copy(hit.point);camera.position.copy(hit.point).add(offset.multiplyScalar(.5));controls.update();
  });
  let rightDown,lastRight;
  renderer.domElement.addEventListener('pointerdown',event=>{
    if(event.button===2)rightDown=[event.clientX,event.clientY];
  });
  renderer.domElement.addEventListener('pointerup',event=>{
    if(event.button!==2||!rightDown)return;
    const [x,y]=rightDown;
    rightDown=null;
    if(!model||regionEnabled||jointEditing||Math.hypot(event.clientX-x,event.clientY-y)>5){lastRight=null;return;}
    const now=performance.now();
    if(lastRight&&now-lastRight.time<450&&Math.hypot(x-lastRight.x,y-lastRight.y)<20){
      const offset=camera.position.clone().sub(controls.target);
      camera.position.copy(controls.target).addScaledVector(offset,1.6);
      controls.update();
      lastRight=null;
    }else lastRight={x,y,time:now};
  });
  observer=new ResizeObserver(()=>{if(!renderer)return;const w=host.clientWidth,h=host.clientHeight;if(w&&h){renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}});observer.observe(host);
  renderer.setAnimationLoop(()=>{
    const delta=clock.getDelta();holoUniform.value+=delta;
    if(points){points.rotation.y=Math.sin(holoUniform.value*.35)*.1;points.material.opacity=.5+Math.sin(holoUniform.value*1.8)*.12;}
    if(mixer&&playing){mixer.update(delta);model.updateMatrixWorld(true);if(rootBone){const current=rootBone.getWorldPosition(new THREE.Vector3());if(lastRoot){const d=current.clone().sub(lastRoot);camera.position.add(d);controls.target.add(d);}lastRoot=current;}}
    if((motionScan||style==='hologram')&&holoAnchor){
      const offset=rootBone&&holoRoot?rootBone.getWorldPosition(new THREE.Vector3()).sub(holoRoot):new THREE.Vector3();
      holoBounds.value.x=holoAnchor.y+offset.y;holoIntro?.update(offset);
    }
    if(skeletonVisible&&jointPoints){const positions=jointPoints.geometry.attributes.position;const point=new THREE.Vector3();joints.forEach((bone,index)=>{jointEditing?point.fromArray(jointDraft[index].position):bone.getWorldPosition(point);positions.setXYZ(index,point.x,point.y,point.z);});positions.needsUpdate=true;if(jointEditing)updateJointLines();}
    controls.update();renderer.render(scene,camera);
  });
}
function clearModel(){
  host.classList.remove('hologramBackdrop');
  endJointEdit();
  clearRegion();regionEnabled=false;regionPainting=false;if(controls)controls.enabled=true;
  if(skeletonHelper){scene.remove(skeletonHelper);skeletonHelper.geometry.dispose();skeletonHelper.material.dispose();skeletonHelper=null;}
  if(jointPoints){scene.remove(jointPoints);jointPoints.geometry.dispose();jointPoints.material.map?.dispose();jointPoints.material.dispose();jointPoints=null;}
  joints=[];skeletonVisible=false;
  holoIntro?.dispose();holoIntro=null;
  holoAnchor=null;holoRoot=null;
  mixer?.stopAllAction();mixer=null;clips=[];rootBone=null;lastRoot=null;
  if(model){scene.remove(model);model.traverse(o=>o.geometry?.dispose());model=null;}
  const materials=new Set([...originals.values()].flat());for(const m of materials){for(const value of Object.values(m))if(value?.isTexture)value.dispose();m.dispose();}
  originals.clear();for(const material of temporary)material.dispose();temporary.clear();
  if(points){scene.remove(points);points.geometry.dispose();points.material.dispose();points=null;}
}
function drawLasso(){
  if(!lassoCanvas)return;lassoCanvas.width=host.clientWidth;lassoCanvas.height=host.clientHeight;
  const ctx=lassoCanvas.getContext('2d');ctx.clearRect(0,0,lassoCanvas.width,lassoCanvas.height);if(!lassoPoints.length)return;
  ctx.beginPath();lassoPoints.forEach((p,i)=>ctx[i?'lineTo':'moveTo'](p[0]*lassoCanvas.width,p[1]*lassoCanvas.height));
  if(lassoClosed){ctx.closePath();ctx.fillStyle='#2d8cff38';ctx.fill();}ctx.strokeStyle='#a9ccff';ctx.lineWidth=2;ctx.setLineDash([6,4]);ctx.stroke();
  ctx.setLineDash([]);regionLandmarks.forEach((p,i)=>{const x=p[0]*lassoCanvas.width,y=p[1]*lassoCanvas.height;ctx.fillStyle='#2d8cff';ctx.beginPath();ctx.arc(x,y,7,0,Math.PI*2);ctx.fill();ctx.fillStyle='#14161c';ctx.font='bold 11px sans-serif';ctx.textAlign='center';ctx.fillText(String(i+1),x,y+4);});
}
export function closeRegion(){
  if(lassoPoints.length<3)return;lassoClosed=true;camera.updateMatrixWorld(true);
  lassoMatrix=new THREE.Matrix4().multiplyMatrices(camera.projectionMatrix,camera.matrixWorldInverse).toArray();lassoCamera=camera.position.toArray();drawLasso();
  host.dispatchEvent(new CustomEvent('regionchange',{detail:lassoPoints.length}));
}
export function beginRegion(){clearRegion();regionEnabled=true;controls.autoRotate=false;setRegionPaint(false);}
export function setRegionPaint(value){regionPainting=!!value;regionMarking=false;controls.enabled=!regionPainting;renderer.domElement.style.cursor=regionPainting?'crosshair':'grab';}
export function markRegionLandmarks(){if(!lassoClosed)throw Error('Feche o contorno antes de marcar os pontos.');regionMarking=true;regionPainting=false;controls.enabled=false;renderer.domElement.style.cursor='crosshair';}
export function getRegionLandmarks(){return regionLandmarks.map(p=>[...p]);}
export function resetRegionLandmarks(){regionLandmarks=[];drawLasso();host.dispatchEvent(new CustomEvent('landmarkchange',{detail:0}));}
export function regionScreenshot(){return renderer.domElement.toDataURL('image/png');}
export function setRegionRadius(value){}
export function getRegion(){return lassoClosed&&lassoMatrix?{polygon:lassoPoints.map(p=>[...p]),matrix:[...lassoMatrix],camera:lassoCamera?[...lassoCamera]:null}:null;}
export function clearRegion(){
  for(const m of regionMarkers){scene.remove(m);m.geometry.dispose();m.material.dispose();}
  regionMarkers=[];regionBrushes=[];lassoPoints=[];lassoClosed=false;lassoMatrix=null;regionLandmarks=[];regionMarking=false;lassoCamera=null;drawLasso();
  host?.dispatchEvent(new CustomEvent('regionchange',{detail:0}));
}
export function endRegion(){clearRegion();regionEnabled=false;regionPainting=false;regionDragging=false;controls.enabled=true;renderer.domElement.style.cursor='';}
export async function load(url){
  initialize();const version=++loadVersion;
  const gltf=await new GLTFLoader().loadAsync(url);
  if(version!==loadVersion){gltf.scene.traverse(o=>{o.geometry?.dispose();for(const m of o.material?(Array.isArray(o.material)?o.material:[o.material]):[])m.dispose();});return [];}
  clearModel();model=gltf.scene;scene.add(model);
  model.traverse(o=>{if(o.isMesh){if(!o.geometry.attributes.normal){o.geometry.computeVertexNormals();for(const m of [o.material].flat()){m.flatShading=false;m.needsUpdate=true;}}originals.set(o,Array.isArray(o.material)?o.material:[o.material]);if(o.isSkinnedMesh){o.frustumCulled=false;if(!rootBone)rootBone=o.skeleton.bones.find(b=>/^(hips|pelvis)$/i.test(b.name))||o.skeleton.bones[0];}}});
  clips=gltf.animations;playing=true;if(clips.length){mixer=new THREE.AnimationMixer(model);mixer.clipAction(clips[0]).play();}
  model.updateMatrixWorld(true);lastRoot=rootBone?.getWorldPosition(new THREE.Vector3())||null;
  model.traverse(o=>{if(o.isBone)joints.push(o);});
  if(joints.length){
    skeletonHelper=new THREE.SkeletonHelper(model);skeletonHelper.material.depthTest=false;skeletonHelper.material.transparent=true;skeletonHelper.material.opacity=.95;skeletonHelper.material.vertexColors=false;skeletonHelper.material.color.set(0x2d8cff);skeletonHelper.renderOrder=1000;skeletonHelper.visible=false;scene.add(skeletonHelper);
    const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(new Float32Array(joints.length*3),3));
    const dot=document.createElement('canvas');dot.width=dot.height=32;const dotContext=dot.getContext('2d');dotContext.beginPath();dotContext.arc(16,16,14,0,Math.PI*2);dotContext.fillStyle='#fff';dotContext.fill();
    jointPoints=new THREE.Points(geometry,new THREE.PointsMaterial({color:0xffffff,map:new THREE.CanvasTexture(dot),size:6,sizeAttenuation:false,depthTest:false,depthWrite:false,transparent:true,alphaTest:.1}));jointPoints.renderOrder=1001;jointPoints.frustumCulled=false;jointPoints.visible=false;scene.add(jointPoints);
  }
  frame();setStyle(style);return clips.map((c,i)=>c.name||'Movimento '+(i+1));
}
export async function showReference(url){
  initialize();const version=++loadVersion;const image=new Image();image.src=url;await image.decode();if(version!==loadVersion)return;
  clearModel();const canvas=document.createElement('canvas');const ratio=image.width/image.height;canvas.height=180;canvas.width=Math.max(1,Math.round(180*ratio));const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0,canvas.width,canvas.height);
  const data=ctx.getImageData(0,0,canvas.width,canvas.height).data,positions=[],colors=[];
  for(let y=0;y<canvas.height;y+=2)for(let x=0;x<canvas.width;x+=2){const i=(y*canvas.width+x)*4;if(data[i+3]<50||Math.min(data[i],data[i+1],data[i+2])>242)continue;positions.push((x/canvas.width-.5)*ratio*2,(.5-y/canvas.height)*2,0);colors.push(data[i]/255,data[i+1]/255,data[i+2]/255);}
  const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));geometry.setAttribute('color',new THREE.Float32BufferAttribute(colors,3));
  points=new THREE.Points(geometry,new THREE.PointsMaterial({size:.009,vertexColors:true,transparent:true,opacity:.6,depthWrite:false}));scene.add(points);controls.target.set(0,0,0);camera.position.set(0,.05,4.5);controls.update();
}
export function frame(){
  if(!model)return;
  const bounds=new THREE.Box3().setFromObject(model),center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
  const span=Math.max(size.x,size.y,size.z,.1),focus=center.clone();focus.y-=size.y*.09;
  controls.target.copy(focus);camera.position.copy(focus).add(new THREE.Vector3(span*.15,span*.12,span*2.65));camera.far=span*100;camera.updateProjectionMatrix();controls.update();
}
export function focusFace(){
  if(!model)return;
  const bounds=new THREE.Box3().setFromObject(model),size=bounds.getSize(new THREE.Vector3()),center=bounds.getCenter(new THREE.Vector3());
  const head=joints.find(b=>/^(head|mixamorighead)$/i.test(b.name));
  const target=head?head.getWorldPosition(new THREE.Vector3()):new THREE.Vector3(center.x,bounds.max.y-size.y*.11,center.z);
  controls.autoRotate=false;controls.target.copy(target);
  const horizontalFit=size.y*.22/(2*Math.tan(THREE.MathUtils.degToRad(camera.fov/2))*Math.max(camera.aspect,.1));
  camera.position.copy(target).add(new THREE.Vector3(0,size.y*.025,Math.max(size.y*.42,horizontalFit,.1)));controls.update();
}
function hologram(original,scanning){
  return new THREE.ShaderMaterial({transparent:true,depthWrite:true,side:THREE.FrontSide,
    uniforms:{uTime:holoUniform,uStart:holoStart,uBounds:holoBounds,
      uMap:{value:original.map||null},uHasMap:{value:!!original.map},uTint:{value:original.color||new THREE.Color(0xffffff)}},
    vertexShader:`#include <common>
      #include <skinning_pars_vertex>
      varying vec3 vWorld;varying vec3 vViewNormal;varying vec3 vViewDirection;varying vec2 vMapUv;
      varying vec3 vSurface;
      void main(){
        #include <skinbase_vertex>
        #include <beginnormal_vertex>
        #include <skinnormal_vertex>
        #include <defaultnormal_vertex>
        #include <begin_vertex>
        #include <skinning_vertex>
        vec4 world=modelMatrix*vec4(transformed,1.0);vWorld=world.xyz;vMapUv=uv;
        vSurface=mat3(modelMatrix)*position;
        vec4 view=viewMatrix*world;vViewDirection=-view.xyz;vViewNormal=transformedNormal;
        gl_Position=projectionMatrix*view;
      }`,
    fragmentShader:`uniform float uTime;uniform float uStart;uniform vec2 uBounds;
      uniform sampler2D uMap;uniform bool uHasMap;uniform vec3 uTint;
      varying vec3 vWorld;varying vec3 vViewNormal;varying vec3 vViewDirection;varying vec2 vMapUv;
      varying vec3 vSurface;
      float hash(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
      float trace(vec3 p){
        vec3 width=max(fwidth(p),vec3(.001));
        vec3 gap=abs(fract(p)-.5);
        vec3 ink=(1.0-smoothstep(vec3(.018),vec3(.018)+width,gap))
          *(1.0-smoothstep(vec3(.10),vec3(.30),width));
        return max(ink.x*ink.y,max(ink.y*ink.z,ink.z*ink.x));
      }
      void main(){
        float age=max(0.0,uTime-uStart);
        float progress=clamp(age/${revealDuration.toFixed(1)},0.0,1.0);
        float height=(vWorld.y-uBounds.x)/max(uBounds.y,.001);
        float front=mix(-.025,1.065,progress);
        float noise=hash(floor(vWorld.xz/uBounds.y*180.0)+floor(uTime*12.0));
        // The reveal clip must never cut a moving character after its introduction.
        float assemblyHeight=height+(noise-.5)*.035;
        if(${!scanning}&&progress<1.0&&assemblyHeight>front)discard;
        vec3 normal=normalize(vViewNormal),eye=normalize(vViewDirection);
        float rim=pow(1.0-abs(dot(normal,eye)),2.1);
        float light=.5+.5*dot(normal,normalize(vec3(-.4,.7,1.0)));
        float phase=height*920.0-uTime*2.0;
        float lines=pow(.5+.5*sin(phase),18.0)*(1.0-smoothstep(.9,2.8,fwidth(phase)));
        vec3 surface=vSurface/max(uBounds.y,.001);
        float lattice=trace(surface*76.0);
        float circuits=trace(surface*21.0);
        float current=pow(.5+.5*sin(surface.y*28.0+surface.x*8.0-uTime*2.2),8.0);
        float assembly=1.0-smoothstep(.0,.11,abs(height-front));
        float fine=hash(gl_FragCoord.xy+floor(uTime*16.0));
        float edge=${scanning?'exp(-pow((height-(1.07-fract(age/2.8)*1.14))/.032,2.0))':'exp(-pow((height-front+.012)/.018,2.0))*(1.0-step(1.0,progress))'};
        float scanDistance=height-mix(-.12,1.12,fract(age*.16));
        float scan=exp(-pow(scanDistance/.010,2.0));
        float halo=exp(-pow(scanDistance/.043,2.0));
        float flicker=.97+.03*sin(uTime*19.0)*sin(uTime*7.0);
        float build=(1.0-step(1.0,progress))*${scanning?'0.0':'1.0'};
        float crest=exp(-pow((height-front+.036)/.007,2.0))*build;
        float echo=exp(-pow((height-front+.086)/.014,2.0))*build;
        float diagonal=height*138.0+(surface.x+surface.z)*48.0;
        float etching=pow(.5+.5*cos(diagonal),12.0)*(1.0-smoothstep(.7,2.4,fwidth(diagonal)));
        float tiles=trace(surface*36.0);
        float sectors=hash(floor(surface.xz*12.0)+floor(surface.y*20.0));
        float charge=pow(.5+.5*sin(uTime*3.2-sectors*12.0),20.0);
        float beacon=step(.92,sectors)*charge*tiles;
        float returnSweep=exp(-pow((height-(1.12-fract(age*.095)*1.24))/.065,2.0));
        float signal=pow(.5+.5*sin(height*37.0+surface.x*10.0-uTime*2.5),10.0);
        float facets=pow(max(0.0,dot(normal,normalize(vec3(.75,.28,.55)))),7.0);
        float iridescence=.5+.5*sin(height*11.0+dot(normal,eye)*4.0-age*.45);
        float settling=exp(-pow((height-front+.17)/.085,2.0))*build;
        vec3 body=mix(vec3(.008,.035,.09),vec3(.03,.16,.34),light);
        vec3 detail=uTint;if(uHasMap)detail*=texture2D(uMap,vMapUv).rgb;
        body=mix(body,detail*.32+vec3(.01,.05,.12),.45);
        vec3 mint=vec3(.34,.66,1.0);
        vec3 blue=vec3(.16,.54,1.0);
        vec3 color=body+mint*(rim*.79+lines*.11+lattice*(.13+halo*.55))
          +blue*(rim*rim*.36+halo*.37+circuits*(.18+current*.65))
          +mint*assembly*lattice*.85*(1.0-progress)
          +vec3(.66,.80,1.0)*(edge*(.40+lattice*.65)+scan*.68);
        color+=vec3(.28,.62,1.0)*crest*.34+vec3(.25,.39,1.0)*echo*.23;
        color+=mint*etching*(.035+settling*.28)+blue*returnSweep*tiles*.48;
        color+=vec3(.85,.92,1.0)*beacon*.88+mint*signal*circuits*.25;
        color+=blue*facets*.13+mix(blue,mint,iridescence)*rim*.21;
        color+=vec3(.10,.40,.90)*settling*.12+blue*tiles*.07;
        float alpha=clamp(.44+light*.16+rim*.32+edge*.5+scan*.18+lines*.035+lattice*.055,0.0,.96);
        gl_FragColor=vec4(color*flicker+(fine-.5)*.018,alpha);
      }`});
}
function startHologram(scanning=false){
  holoStart.value=holoUniform.value;
  // Recompute the current skinned pose bounds instead of using the cached rest pose.
  model.updateMatrixWorld(true);
  model.traverse(o=>{if(o.isSkinnedMesh)o.computeBoundingBox();});
  const bounds=new THREE.Box3().setFromObject(model),size=bounds.getSize(new THREE.Vector3());
  holoBounds.value.set(bounds.min.y,Math.max(size.y,.001));
  holoAnchor=bounds.getCenter(new THREE.Vector3());holoAnchor.y=bounds.min.y;
  holoRoot=rootBone?.getWorldPosition(new THREE.Vector3())||null;
  holoIntro?.dispose();holoIntro=scanning?null:createIntro(host,scene,model,bounds,holoUniform,holoStart,revealDuration);
}
export function setMotionScan(active){
  if(motionScan===active)return;
  motionScan=active;
  setStyle(style);
}
export function setStyle(value){
  style=value;
  host.classList.toggle('hologramBackdrop',!!model&&(motionScan||value==='hologram'));
  if(!model)return;
  if(motionScan||value==='hologram')startHologram(motionScan);else {holoIntro?.dispose();holoIntro=null;}
  for(const m of temporary)m.dispose();temporary.clear();
  for(const [mesh,materials] of originals){
    const changed=materials.map(original=>{
      if(!motionScan&&value==='textured')return original;
      let m;
      if(motionScan)m=hologram(original,true);
      else if(value==='normal')m=new THREE.MeshNormalMaterial({side:THREE.DoubleSide});
      else if(value==='solid')m=new THREE.MeshStandardMaterial({color:0xa8abb3,roughness:.85,metalness:0,side:THREE.DoubleSide});
      else if(value==='unlit')m=new THREE.MeshBasicMaterial({map:original.map,color:original.color||0xffffff,side:THREE.DoubleSide});
      else if(value==='wireframe')m=new THREE.MeshBasicMaterial({color:0xb8bbc2,wireframe:true});
      else if(value==='cartoon')m=new THREE.MeshToonMaterial({color:original.color||0xa9ccff,map:original.map,side:THREE.DoubleSide});
      else if(value==='hologram')m=hologram(original);
      else {m=new THREE.MeshStandardMaterial({color:0xf2f3f5,roughness:1,metalness:0});m.onBeforeCompile=shader=>{shader.fragmentShader=shader.fragmentShader.replace('#include <opaque_fragment>','#include <opaque_fragment>\nfloat hatch=step(.7,fract((gl_FragCoord.x+gl_FragCoord.y)*.16));gl_FragColor.rgb*=mix(.74,1.0,hatch);');};}
      temporary.add(m);return m;
    });mesh.material=changed.length===1?changed[0]:changed;
  }
}
export function toggleRotation(){initialize();controls.autoRotate=!controls.autoRotate;return controls.autoRotate;}
export function togglePlayback(){playing=!playing;return playing;}
export function clipPlayback(index){
  const clip=clips[index];
  const action=clip&&mixer?.existingAction(clip);
  return {duration:clip?.duration||0,time:action?.time||0};
}
export function seekClip(index,seconds){
  const clip=clips[index];
  const action=clip&&mixer?.existingAction(clip);
  if(!action||!Number.isFinite(seconds))return;
  playing=false;
  action.time=Math.min(Math.max(seconds,0),Math.max(0,clip.duration-.0001));
  mixer.update(0);
  model.updateMatrixWorld(true);
  if(rootBone){
    const current=rootBone.getWorldPosition(new THREE.Vector3());
    if(lastRoot){const offset=current.clone().sub(lastRoot);camera.position.add(offset);controls.target.add(offset);}
    lastRoot=current;
  }
}
export function toggleSkeleton(){if(!joints.length)return false;skeletonVisible=!skeletonVisible;skeletonHelper.visible=skeletonVisible;jointPoints.visible=skeletonVisible;return skeletonVisible;}
export function clear(){++loadVersion;clearModel();}
export function selectClip(index){if(!mixer||!clips[index])return;mixer.stopAllAction();mixer.clipAction(clips[index]).reset().play();playing=true;}
export function snapshot(){if(!renderer)return null;renderer.render(scene,camera);return renderer.domElement.toDataURL('image/png');}
export function state(){let triangles=0,vertices=0;if(model)model.traverse(o=>{if(o.isMesh){vertices+=o.geometry.attributes.position.count;triangles+=(o.geometry.index?.count||o.geometry.attributes.position.count)/3;}});return {rendering:!!renderer,clipCount:clips.length,time:mixer?.time||0,playing,style,boneCount:joints.length,skeletonVisible,hologramProgress:Math.min(1,Math.max(0,(holoUniform.value-holoStart.value)/revealDuration)),triangles:Math.round(triangles),vertices};}
export function dispose(){++loadVersion;if(!renderer)return;clearModel();environmentMap?.dispose();environmentMap=null;renderer.setAnimationLoop(null);observer.disconnect();controls.dispose();renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();renderer=null;}

export function beginJointEdit(mapping={}){
  if(!joints.length)throw Error('Este modelo não tem articulações.');
  endRegion();playing=false;controls.autoRotate=false;model.updateMatrixWorld(true);
  jointDraft=joints.map(bone=>({name:bone.name,parent:bone.parent?.isBone?bone.parent.name:null,position:bone.getWorldPosition(new THREE.Vector3()).toArray(),role:mapping[bone.name]||''}));
  jointEditing=true;jointSelected=0;skeletonVisible=true;jointPoints.visible=true;skeletonHelper.visible=false;
  jointPoints.geometry.setAttribute('color',new THREE.Float32BufferAttribute(new Float32Array(joints.length*3).fill(1),3));jointPoints.material.vertexColors=true;jointPoints.material.needsUpdate=true;updateJointColors();
  jointLines=new THREE.LineSegments(new THREE.BufferGeometry(),new THREE.LineBasicMaterial({color:0x2d8cff,depthTest:false,transparent:true}));jointLines.renderOrder=1000;scene.add(jointLines);
  return getJointDraft();
}
export function endJointEdit(){
  jointEditing=false;jointDrag=null;jointDraft=[];jointSelected=-1;
  if(jointLines){scene.remove(jointLines);jointLines.geometry.dispose();jointLines.material.dispose();jointLines=null;}
  if(skeletonHelper)skeletonHelper.visible=skeletonVisible;
  if(jointPoints){jointPoints.material.vertexColors=false;jointPoints.material.needsUpdate=true;}
  if(controls)controls.enabled=true;
}
export function getJointDraft(){return jointDraft.map(x=>({...x,position:[...x.position]}));}
export function selectJoint(name){jointSelected=jointDraft.findIndex(x=>x.name===name);updateJointColors();return jointDraft[jointSelected];}
function updateJointColors(){const colors=jointPoints?.geometry.attributes.color;if(!colors)return;for(let i=0;i<joints.length;i++)colors.setXYZ(i,i===jointSelected ? .35 : 1,1,1);colors.needsUpdate=true;}
export function setJoint(name,position,role){
  const item=jointDraft.find(x=>x.name===name);if(!item)return;
  if(position&&position.length===3&&position.every(Number.isFinite))item.position=[...position];
  if(role!==undefined){if(role&&!['Spine1','Spine2','Chest','Neck1'].includes(role))for(const other of jointDraft)if(other!==item&&other.role===role)other.role='';item.role=role;}
}
function updateJointLines(){
  const byName=new Map(jointDraft.map(x=>[x.name,x]));const lines=[];
  for(const item of jointDraft){const parent=byName.get(item.parent);if(parent)lines.push(...parent.position,...item.position);}
  const attribute=jointLines.geometry.attributes.position;if(attribute?.array.length===lines.length){attribute.array.set(lines);attribute.needsUpdate=true;}else jointLines.geometry.setAttribute('position',new THREE.Float32BufferAttribute(lines,3));
}
function jointRay(event){const r=renderer.domElement.getBoundingClientRect(),ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2((event.clientX-r.left)/r.width*2-1,-(event.clientY-r.top)/r.height*2+1),camera);return ray;}
function pickJoint(event){
  const r=renderer.domElement.getBoundingClientRect();let best=14,index=-1;
  jointDraft.forEach((item,i)=>{const p=new THREE.Vector3().fromArray(item.position).project(camera);if(p.z<-1||p.z>1)return;const distance=Math.hypot((p.x+1)*r.width/2+r.left-event.clientX,(1-p.y)*r.height/2+r.top-event.clientY);if(distance<best){best=distance;index=i;}});
  if(index<0)return false;
  jointSelected=index;const point=new THREE.Vector3().fromArray(jointDraft[index].position),plane=new THREE.Plane().setFromNormalAndCoplanarPoint(camera.getWorldDirection(new THREE.Vector3()),point);
  const hit=jointRay(event).ray.intersectPlane(plane,new THREE.Vector3());if(!hit)return false;
  jointDrag={plane,offset:point.sub(hit)};controls.enabled=false;renderer.domElement.setPointerCapture(event.pointerId);
  host.dispatchEvent(new CustomEvent('jointpick',{detail:jointDraft[index].name}));return true;
}
function moveJointDrag(event){const hit=jointRay(event).ray.intersectPlane(jointDrag.plane,new THREE.Vector3());if(!hit)return;jointDraft[jointSelected].position=hit.add(jointDrag.offset).toArray();host.dispatchEvent(new CustomEvent('jointpick',{detail:jointDraft[jointSelected].name}));}

