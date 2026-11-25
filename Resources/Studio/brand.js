// Nome e símbolo do produto num só lugar. Trocar o nome = editar BRAND.name.
window.BRAND={name:'ez3D',tagline:'Do seu desenho ao 3D animado'};
document.title=BRAND.name+' · Modelos e animações';
addEventListener('DOMContentLoaded',()=>{const b=document.querySelector('.brand');if(b)b.innerHTML='<img class="mark" src="ez3d-mark.svg" alt=""><span class="word"></span>',b.querySelector('.word').textContent=BRAND.name;});
