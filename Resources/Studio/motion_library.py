"""Reusable SOMA clips and lightweight animated catalog previews."""
import argparse
import importlib.util
from pathlib import Path

CLIPS = {
    'idle': 'Repouso', 'walk': 'Caminhada', 'run': 'Corrida',
    'jog': 'Trote no lugar', 'turn': 'Caminhada com curva',
    'crouch': 'Agachar e levantar', 'wave': 'Acenar',
    'jump': 'Pular', 'clap': 'Aplaudir', 'sidestep': 'Passo lateral',
    'sit': 'Sentar',
}
FILES = ('local_rotations_xyzw.f32', 'root_positions.f32')


def clip_folder(root, ident):
    if ident not in CLIPS:
        raise ValueError('Animação desconhecida.')
    folder = root / 'motion-library' / ident
    if not all((folder / filename).is_file() for filename in (*FILES, 'preview.gif')):
        raise ValueError('Esta animação ainda está indisponível.')
    return folder


def catalog(root):
    result = []
    for ident, name in CLIPS.items():
        try:
            folder = clip_folder(root, ident)
        except ValueError:
            continue
        result.append(dict(id=ident, name=name, preview=str(folder / 'preview.gif')))
    return result


def render_preview(folder, kimodo_root):
    import numpy as np
    from PIL import Image, ImageDraw
    from scipy.spatial.transform import Rotation

    spec = importlib.util.spec_from_file_location('soma_export', kimodo_root / 'scripts/export_glb.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    skeleton = module.SKELETONS['soma30']
    parents = skeleton['parents']
    rotations = np.fromfile(folder / FILES[0], dtype='<f4').reshape(-1, len(parents), 4)
    roots = np.fromfile(folder / FILES[1], dtype='<f4').reshape(-1, 3)
    if len(roots) < 2 or len(roots) != len(rotations) or not np.isfinite(rotations).all() or not np.isfinite(roots).all():
        raise ValueError(f'Movimento inválido em {folder.name}')
    frames = []
    for index in range(0, len(roots), 2):
        matrices = Rotation.from_quat(rotations[index]).as_matrix()
        positions = [np.array([0, roots[index, 1], 0])]
        for joint, parent in enumerate(parents[1:], 1):
            positions.append(positions[parent] + matrices[parent] @ skeleton['offsets'][joint])
            matrices[joint] = matrices[parent] @ matrices[joint]
        points = [(64 + 43 * (.65*x + .76*z), 98 - 45*y + 7*(.76*x - .65*z)) for x, y, z in positions]
        image = Image.new('RGB', (128, 112), '#242131')
        draw = ImageDraw.Draw(image)
        draw.ellipse((27, 94, 101, 106), fill='#302942', outline='#453b59')
        for joint in (1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 16, 17, 18, 19, 22, 23, 24, 25, 26, 27, 28, 29):
            draw.line((points[parents[joint]], points[joint]), fill='#ae9af5', width=3)
        x, y = points[6]
        draw.ellipse((x-4, y-5, x+4, y+5), fill='#dcd2ff')
        frames.append(image)
    temporary = folder / 'preview.tmp'
    frames[0].save(temporary, format='GIF', save_all=True, append_images=frames[1:], duration=[70, 70, 60] * (len(frames)//3) + [70] * (len(frames)%3), loop=0)
    temporary.replace(folder / 'preview.gif')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('kimodo_root', type=Path)
    args = parser.parse_args()
    for ident in CLIPS:
        folder = args.root / 'motion-library' / ident
        if all((folder / filename).is_file() for filename in FILES):
            render_preview(folder, args.kimodo_root)

