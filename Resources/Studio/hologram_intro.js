import * as THREE from 'three';

// Short trails converge on sampled vertices as each section is revealed.
export function createIntro(host,scene,model,bounds,time,start,duration){
  const size=bounds.getSize(new THREE.Vector3());
  const meshes=[];model.traverse(o=>{if(o.isMesh)meshes.push(o);});
  const positions=[],heights=[],seeds=[],travel=[],point=new THREE.Vector3();
  for(let i=0;i<1800&&meshes.length;i++){
    const mesh=meshes[i%meshes.length],count=mesh.geometry.attributes.position.count;
    mesh.getVertexPosition(Math.floor((i*.61803398875%1)*count),point);mesh.localToWorld(point);
    const angle=i*2.39996323;
    const spread=size.y*(.018+.065*(i*.41421356%1));
    positions.push(point.x+Math.cos(angle)*spread,point.y+spread*.55,
      point.z+Math.sin(angle)*spread,point.x,point.y,point.z);
    const height=(point.y-bounds.min.y)/Math.max(size.y,.001);
    heights.push(height,height);seeds.push(i*.137%1,i*.137%1);travel.push(0,1);
  }
  const geometry=new THREE.BufferGeometry();
  geometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
  geometry.setAttribute('aHeight',new THREE.Float32BufferAttribute(heights,1));
  geometry.setAttribute('aSeed',new THREE.Float32BufferAttribute(seeds,1));
  geometry.setAttribute('aTravel',new THREE.Float32BufferAttribute(travel,1));
  const material=new THREE.ShaderMaterial({transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,
    uniforms:{uTime:time,uStart:start},
    vertexShader:'attribute float aHeight;attribute float aSeed;attribute float aTravel;varying float vTravel;varying float vHeight;varying float vSeed;void main(){vTravel=aTravel;vHeight=aHeight;vSeed=aSeed;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}',
    fragmentShader:`uniform float uTime;uniform float uStart;varying float vTravel;varying float vHeight;varying float vSeed;
      void main(){float age=uTime-uStart;float front=mix(-.025,1.065,clamp(age/${duration.toFixed(1)},0.0,1.0));
      float approach=front-vHeight;
      float band=smoothstep(-.12,-.035,approach)*(1.0-smoothstep(.015,.08,approach));
      float fade=1.0-smoothstep(${(duration-.15).toFixed(2)},${(duration+.5).toFixed(2)},age);
      float pulse=.5+.5*sin(age*5.0+vSeed*30.0);
      float packet=clamp((approach+.10)/.13+vSeed*.24,0.0,1.0);
      float spark=exp(-pow((vTravel-packet)/.085,2.0));
      vec3 color=mix(vec3(.10,.48,.96),vec3(.66,.80,1.0),spark);
      gl_FragColor=vec4(color,band*fade*(.035*vTravel+pulse*.06+spark*.60));}`});
  const beams=new THREE.LineSegments(geometry,material);beams.frustumCulled=false;scene.add(beams);
  const fragments=new THREE.BufferGeometry();
  const targets=[],fragmentHeights=[],fragmentSeeds=[];
  for(let i=0;i<positions.length/6;i+=3){
    targets.push(...positions.slice(i*6+3,i*6+6));
    fragmentHeights.push(heights[i*2]);fragmentSeeds.push(seeds[i*2]);
  }
  fragments.setAttribute('position',new THREE.Float32BufferAttribute(targets,3));
  fragments.setAttribute('aHeight',new THREE.Float32BufferAttribute(fragmentHeights,1));
  fragments.setAttribute('aSeed',new THREE.Float32BufferAttribute(fragmentSeeds,1));
  const fragmentMaterial=new THREE.ShaderMaterial({
    transparent:true,depthWrite:false,blending:THREE.AdditiveBlending,
    uniforms:{uTime:time,uStart:start,uScale:{value:size.y}},
    vertexShader:`uniform float uTime,uStart,uScale;
      attribute float aHeight,aSeed;
      varying float vAlpha,vAngle,vSeed;
      void main(){
        float front=mix(-.025,1.065,clamp((uTime-uStart)/${duration.toFixed(1)},0.0,1.0));
        float landing=smoothstep(-.14,0.0,front-aHeight);
        float heading=aSeed*61.0+landing*1.8;
        vec3 drift=vec3(cos(heading),.45,sin(heading))*uScale*.14*(1.0-landing);
        vec4 view=modelViewMatrix*vec4(position+drift,1.0);
        gl_Position=projectionMatrix*view;
        gl_PointSize=clamp(uScale*(12.0+aSeed*14.0)/max(.15,-view.z),2.0,14.0);
        vAlpha=smoothstep(0.0,.35,landing)*(1.0-smoothstep(.83,1.0,landing));
        vAngle=heading+landing*4.5;vSeed=aSeed;
      }`,
    fragmentShader:`varying float vAlpha,vAngle,vSeed;
      void main(){
        vec2 uv=gl_PointCoord*2.0-1.0;
        vec2 p=mat2(cos(vAngle),sin(vAngle),-sin(vAngle),cos(vAngle))*uv;
        float shape=1.0-smoothstep(.69,.96,abs(p.x)*1.85+abs(p.y));
        float vein=1.0-smoothstep(.035,.16,abs(p.x));
        vec3 color=mix(vec3(.11,.55,1.0),vec3(.40,.70,1.0),vSeed);
        gl_FragColor=vec4(mix(color,vec3(.88,.94,1.0),vein),shape*vAlpha*.76);
      }`
  });
  const leaves=new THREE.Points(fragments,fragmentMaterial);
  leaves.frustumCulled=false;scene.add(leaves);
  const canvas=document.createElement('canvas');
  canvas.dataset.hologramIntro='true';
  Object.assign(canvas.style,{position:'absolute',inset:'0',width:'100%',height:'100%',pointerEvents:'none'});
  canvas.setAttribute('aria-hidden','true');host.prepend(canvas);
  const context=canvas.getContext('2d');
  const glyphs='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  function update(offset){
    const age=time.value-start.value;
    beams.position.copy(offset);beams.visible=age<duration+.5;
    leaves.position.copy(offset);leaves.visible=age<duration;
    const fade=1-THREE.MathUtils.smoothstep(age,duration+.4,duration+3.4);
    if(fade<=0){canvas.hidden=true;return;}
    const width=host.clientWidth,height=host.clientHeight;
    if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
    context.clearRect(0,0,width,height);context.font='11px ui-monospace,monospace';
    for(let column=0;column<30;column++){
      const x=(column+.5)*width/30;
      const lead=((column*173+age*(35+(column%5)*12))%(height+250))-100;
      for(let row=0;row<13;row++){
        const y=lead-row*17;if(y<0||y>height)continue;
        context.fillStyle=`rgba(45,140,255,${fade*(1-row/13)*.25})`;
        context.fillText(glyphs[(column*7+row*13+Math.floor(age*5))%glyphs.length],x,y);
      }
    }
  }
  return {update,dispose(){
    scene.remove(beams,leaves);geometry.dispose();material.dispose();
    fragments.dispose();fragmentMaterial.dispose();canvas.remove();
  }};
}
