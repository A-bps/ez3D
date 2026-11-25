import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
const host=document.getElementById('meshPreview');
let renderer,scene,camera,controls,mixer,model,clips=[],playing=true,resizeObserver,rootBone,lastRoot=null;
const clock=new THREE.Clock();
function initialize(){
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));host.append(renderer.domElement);
  renderer.outputColorSpace=THREE.SRGBColorSpace;scene=new THREE.Scene();camera=new THREE.PerspectiveCamera(35,1,.01,200);
  scene.add(new THREE.HemisphereLight(0xffffff,0x555960,2.3));const key=new THREE.DirectionalLight(0xffffff,3);key.position.set(3,5,4);scene.add(key);
  controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;
  resizeObserver=new ResizeObserver(()=>{const w=host.clientWidth,h=host.clientHeight;if(w&&h&&renderer){renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}});resizeObserver.observe(host);
  renderer.setAnimationLoop(()=>{const dt=clock.getDelta();if(mixer&&playing){mixer.update(dt);model.updateMatrixWorld(true);if(rootBone){const point=rootBone.getWorldPosition(new THREE.Vector3());if(lastRoot){const delta=point.clone().sub(lastRoot);camera.position.add(delta);controls.target.add(delta);}lastRoot=point;}}controls.update();renderer.render(scene,camera);});
}
export async function load(url){
  if(!renderer)initialize();
  const gltf=await new GLTFLoader().loadAsync(url);
  if(model){scene.remove(model);model.traverse(o=>{o.geometry?.dispose();if(o.material){for(const m of Array.isArray(o.material)?o.material:[o.material]){for(const value of Object.values(m))if(value?.isTexture)value.dispose();m.dispose();}}});}
  mixer?.stopAllAction();model=gltf.scene;scene.add(model);
  rootBone=null;model.traverse(o=>{if(!rootBone&&o.isSkinnedMesh)rootBone=o.skeleton.bones[0];});lastRoot=rootBone?.getWorldPosition(new THREE.Vector3())||null;
  frame();clips=gltf.animations;playing=true;
  mixer=null;if(clips.length){mixer=new THREE.AnimationMixer(model);mixer.clipAction(clips[0]).play();}controls.update();
  return clips.map((c,i)=>c.name||'Animação '+(i+1));
}
export function frame(){
  if(!model)return;
  const bounds=new THREE.Box3().setFromObject(model), center=bounds.getCenter(new THREE.Vector3()), size=bounds.getSize(new THREE.Vector3());
  const span=Math.max(size.x,size.y,size.z,.1);controls.target.copy(center);camera.position.copy(center).add(new THREE.Vector3(span*1.1,span*.5,span*2.2));camera.far=span*100;camera.updateProjectionMatrix();
  controls.update();
}
export function togglePlayback(){playing=!playing;return playing;}
export function playbackState(){return {playing,clipCount:clips.length,time:mixer?.time||0,rendering:!!renderer};}
export function selectClip(index){if(!mixer||!clips[index])return;mixer.stopAllAction();mixer.clipAction(clips[index]).reset().play();playing=true;}
export function dispose(){
  if(!renderer)return;renderer.setAnimationLoop(null);mixer?.stopAllAction();resizeObserver?.disconnect();controls.dispose();
  model?.traverse(o=>{o.geometry?.dispose();for(const m of o.material?(Array.isArray(o.material)?o.material:[o.material]):[]){for(const value of Object.values(m))if(value?.isTexture)value.dispose();m.dispose();}});
  renderer.dispose();renderer.forceContextLoss();renderer.domElement.remove();renderer=null;mixer=null;model=null;clips=[];rootBone=null;lastRoot=null;
}
