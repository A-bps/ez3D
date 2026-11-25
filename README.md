# ez3D

**Transforme imagens em modelos 3D no seu próprio computador.** Envie a imagem de um personagem ou objeto e receba um modelo `.glb` com textura, pronto para refinar no Blender ou levar para a Unreal Engine. Sem assinatura: depois que os modelos de IA são baixados, a geração roda na sua placa de vídeo.

## ⬇️ [Baixar o ez3D-Setup.exe (v0.1 beta)](https://github.com/A-bps/ez3D/releases/download/v0.1-beta/ez3D-Setup.exe)

[Ver a página da versão e as notas](https://github.com/A-bps/ez3D/releases) · Precisa de placa **NVIDIA com 24 GB**.

> **Versão beta.** Testada em um único PC (Windows 11, RTX 3090). A geração de modelos foi testada; esqueleto, animação por texto e exportação para a Unreal ainda não foram validados. O instalador ainda não foi testado do zero em outro computador.

## Dois geradores

| | **TRELLIS.2** | **Hunyuan3D** (opcional) |
|---|---|---|
| Entrada | 1 imagem | até 4 vistas: frente, lados e costas |
| Ideal para | quando você só tem uma foto; objetos e personagens com muito detalhe | quando você tem várias vistas do mesmo item; modelos leves para jogo e web |
| Textura | materiais PBR | textura de cor |
| Atenção | precisa imaginar o que não aparece, como as costas | as vistas precisam ser do mesmo item, na mesma pose |

## Tempos medidos

Duas gerações reais em uma **RTX 3090 (24 GB)**, com os modelos de IA já baixados.

| | **Vanellope** (personagem de animação) | **Boneca de teste** (imagens de exemplo do Hunyuan3D) |
|---|---|---|
| **Tempo** | **9 min 55 s** | **2 min 23 s** |
| Gerador | TRELLIS.2 (`microsoft/TRELLIS.2-4B`) | Hunyuan3D-2mv |
| Modelos usados | TRELLIS.2, com DINOv3 ViT-L/16 (Meta) para ler a imagem e RMBG-2.0 (BRIA) para o fundo | `hunyuan3d-dit-v2-mv` (forma) e `hunyuan3d-paint-v2-0-turbo` (textura) |
| Entrada | 1 imagem | 3 vistas: frente, lado esquerdo e costas |
| Qualidade | Máxima (personagem), textura PBR 4K | Equilibrada |
| Resultado | cerca de 1,96 milhão de triângulos, 128 MB | 40 mil triângulos, 3,3 MB |

**Não é uma comparação direta:** os personagens, as qualidades e o número de imagens foram diferentes. Os tempos variam conforme o computador e a imagem. A primeira geração de cada gerador pode demorar bem mais, porque ainda baixa arquivos.

Limitação observada: com uma única imagem, o capuz da blusa da Vanellope virou um volume deformado nas costas.

## O que mais o app tem
Recursos presentes no app, **ainda não validados** nesta versão:
- Esqueleto automático para personagens (UniRig / SkinTokens).
- Animação a partir de texto para humanoides (Kimodo).
- Exportação para a Unreal Engine.

## Requisitos
- Windows 11.
- Placa **NVIDIA com 24 GB** de memória de vídeo.
- Cerca de **80 GB livres** (mais, se instalar o Hunyuan3D).
- Conta gratuita no [Hugging Face](https://huggingface.co).
- Permissão para instalar WSL/Ubuntu (pode exigir reiniciar o PC).

## Como instalar
1. Baixe o [`ez3D-Setup.exe`](https://github.com/A-bps/ez3D/releases/download/v0.1-beta/ez3D-Setup.exe) e execute. Como o arquivo não tem assinatura digital, o Windows pode avisar que o editor é desconhecido.
2. Quando o instalador pedir, **aceite os termos** destes dois modelos com a sua conta e entre no Hugging Face. O acesso ao modelo da Meta pode passar por análise.
   - [`facebook/dinov3-vitl16-pretrain-lvd1689m`](https://huggingface.co/facebook/dinov3-vitl16-pretrain-lvd1689m)
   - [`briaai/RMBG-2.0`](https://huggingface.co/briaai/RMBG-2.0)
3. No fim, o instalador pergunta se você quer o **Hunyuan3D** (várias fotos).
4. Aguarde os downloads e abra o **ez3D** pelo menu Iniciar.

Se algo falhar, abra uma [issue](https://github.com/A-bps/ez3D/issues) com o trecho do erro de `%LOCALAPPDATA%\ez3D\install.log`. Antes de enviar, apague dados pessoais, como o nome do seu usuário.

Nenhum modelo de IA vem neste repositório: cada pessoa baixa os seus, com a própria conta.

## Licenças
O código do ez3D é MIT (veja `LICENSE`). Os modelos e ferramentas têm licenças próprias (veja `THIRD-PARTY.md`):
- **RMBG-2.0:** gratuito apenas para uso **não comercial**.
- **Hunyuan3D:** a licença **não vale na União Europeia, no Reino Unido nem na Coreia do Sul**.

Confira cada licença antes de usar os resultados comercialmente.
