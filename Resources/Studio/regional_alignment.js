// Same normalized thin-plate spline as regional_alignment.py, for the preview.
export function fitAlignment(target,reference){
  if(target.length!==6||reference.length!==6)throw Error('Marque os seis pontos nos dois lados.');
  const center=[0,1].map(i=>target.reduce((s,p)=>s+p[i],0)/6);
  const scale=Math.max(...[0,1].map(i=>Math.max(...target.map(p=>p[i]))-Math.min(...target.map(p=>p[i]))));
  if(scale<1e-5)throw Error('Os pontos do modelo estão muito próximos.');
  const points=target.map(p=>p.map((v,i)=>(v-center[i])/scale));
  for(let i=0;i<6;i++)for(let j=0;j<i;j++)if(Math.hypot(...points[i].map((v,k)=>v-points[j][k]))<.01)throw Error('Existem pontos repetidos no modelo.');
  const kernel=(a,b)=>{const r2=(a[0]-b[0])**2+(a[1]-b[1])**2;return r2*Math.log(Math.max(r2,1e-12));};
  const a=Array.from({length:9},()=>Array(11).fill(0));
  for(let i=0;i<6;i++){
    for(let j=0;j<6;j++)a[i][j]=kernel(points[i],points[j])+(i===j?1e-8:0);
    [1,...points[i]].forEach((v,j)=>{a[i][6+j]=v;a[6+j][i]=v;});a[i][9]=reference[i][0];a[i][10]=reference[i][1];
  }
  for(let col=0;col<9;col++){
    let pivot=col;for(let i=col+1;i<9;i++)if(Math.abs(a[i][col])>Math.abs(a[pivot][col]))pivot=i;
    if(Math.abs(a[pivot][col])<1e-12)throw Error('Distribua os pontos pelo rosto; não os coloque todos em linha.');
    [a[col],a[pivot]]=[a[pivot],a[col]];const d=a[col][col];for(let j=col;j<11;j++)a[col][j]/=d;
    for(let i=0;i<9;i++)if(i!==col){const k=a[i][col];for(let j=col;j<11;j++)a[i][j]-=k*a[col][j];}
  }
  return {center,scale,points,coeff:a.map(row=>row.slice(9))};
}
export function warpPoint(coords,fit){
  const p=coords.map((v,i)=>(v-fit.center[i])/fit.scale);
  const basis=fit.points.map(q=>{const r2=(p[0]-q[0])**2+(p[1]-q[1])**2;return r2*Math.log(Math.max(r2,1e-12));});
  basis.push(1,...p);return [0,1].map(c=>basis.reduce((s,v,i)=>s+v*fit.coeff[i][c],0));
}
