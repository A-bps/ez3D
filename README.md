# ez3D

Do seu desenho ao 3D animado, rodando 100% no seu PC.

> **Versão beta (v0.1).** Testada em um único PC (Windows 11, RTX 3090). Geração de modelo testada; esqueleto e animação por texto ainda não foram validados.

Cole uma imagem de personagem ou objeto e o ez3D cria o modelo 3D, monta o esqueleto, gera animações a partir de texto e exporta para a Unreal Engine.

## O que ele faz
- **Imagem para 3D** (TRELLIS.2): malha com textura PBR.
- **Esqueleto automático** (UniRig / SkinTokens) para personagens.
- **Animação por texto** (Kimodo): 90 quadros a 30 fps, para humanoides.
- **Exportação** para Unreal Engine.

## Requisitos
- Windows 11, placa **NVIDIA com 24 GB** de memória, cerca de **80 GB livres**.
- Conta no [Hugging Face](https://huggingface.co) (gratuita).
- Algumas aprovações do Windows (instalação do WSL/Ubuntu) e possível reinício.

## Instalar
1. Baixe o `ez3D-Setup.exe` na página **Releases** e execute. O Windows pode avisar que o editor é desconhecido, porque o arquivo não tem assinatura digital. (Ou baixe o repositório e execute `Install-ez3D.ps1`.)
2. Quando pedir, **aceite os termos** dos dois modelos abaixo com a sua conta e entre no Hugging Face pelo navegador:
   - `facebook/dinov3-vitl16-pretrain-lvd1689m` (Meta)
   - `briaai/RMBG-2.0` (BRIA)
3. Aguarde. A primeira instalação baixa vários gigabytes e leva um tempo.
4. Abra o **ez3D** pelo menu Iniciar.

Nenhum modelo vem neste repositório: cada pessoa baixa os seus, com a própria conta.

## Licenças
O código do ez3D é MIT (arquivo `LICENSE`). Os modelos e ferramentas usados têm licenças próprias, veja `THIRD-PARTY.md`. **Atenção:** o RMBG-2.0 é gratuito apenas para uso **não comercial**.

## Várias fotos (opcional): Hunyuan3D
Envie frente, costas e lados do mesmo objeto. No fim da instalação, o instalador pergunta se você quer esse gerador.

**Aviso:** a licença dele não vale na União Europeia, no Reino Unido nem na Coreia do Sul. Veja `THIRD-PARTY.md`.
