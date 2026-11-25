import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
let renderer,queue=Promise.resolve();
const cache=new Map();
async function render(url,clay){
  if(!renderer){renderer=new THREE.WebGLRenderer({antialias:true,alpha:false});renderer.setSize(240,270);renderer.setPixelRatio(1);renderer.outputColorSpace=THREE.SRGBColorSpace;renderer.toneMapping=THREE.ACESFilmicToneMapping;}
  const gltf=await new GLTFLoader().loadAsync(url),scene=new THREE.Scene();scene.background=new THREE.Color('#26282b');scene.add(gltf.scene);
  scene.add(new THREE.HemisphereLight(0xffffff,0x555960,3));
  for(const [position,power] of [[[3,4,5],3],[[-4,2,1],2]]){const light=new THREE.DirectionalLight(0xffffff,power);light.position.fromArray(position);scene.add(light);}
  const box=new THREE.Box3().setFromObject(gltf.scene),center=box.getCenter(new THREE.Vector3()),size=box.getSize(new THREE.Vector3());
  const span=Math.max(size.y,size.x*270/240,size.z),camera=new THREE.PerspectiveCamera(32,240/270,.001,10000);
  camera.position.copy(center).add(new THREE.Vector3(span*.12,span*.04,span*2.2));camera.lookAt(center);
  const originals=new Map();
  const material=clay?new THREE.MeshStandardMaterial({color:0xe9eaec,roughness:.85,side:THREE.DoubleSide}):null;
  if(material)gltf.scene.traverse(object=>{
    if(!object.isMesh)return;
    originals.set(object,object.material);
    object.material=material;
  });
  renderer.render(scene,camera);
  for(const [object,original] of originals)object.material=original;
  material?.dispose();
  const png=renderer.domElement.toDataURL('image/png'),resources=new Set();
  gltf.scene.traverse(object=>{object.geometry?.dispose();for(const material of object.material?(Array.isArray(object.material)?object.material:[object.material]):[]){for(const value of Object.values(material))if(value?.isTexture)resources.add(value);resources.add(material);}});
  for(const resource of resources){if(resource.isTexture)resource.source?.data?.close?.();resource.dispose();}
  renderer.renderLists.dispose();return png;
}
export function thumbnail(url,clay=false){
  const key=JSON.stringify([url,clay]);
  if(!cache.has(key)){
    const next=queue.then(()=>render(url,clay));
    queue=next.catch(()=>{cache.delete(key);});
    cache.set(key,next);
  }
  return cache.get(key);
}
window.addEventListener('pagehide',()=>{renderer?.dispose();renderer?.forceContextLoss();});
