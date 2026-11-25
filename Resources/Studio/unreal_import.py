"""Executed by Unreal's Python commandlet, never by the studio interpreter."""
import json
import os
from pathlib import Path
import traceback
import re
import unicodedata
import unreal


def create_hologram(destination, skeletal):
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset('M_ez3D_Holograma', destination + '/Materiais',
                                  unreal.Material, unreal.MaterialFactoryNew())
    if not material:
        raise RuntimeError('Não foi possível criar o material de holograma.')
    material.set_editor_property('used_with_skeletal_mesh', skeletal)
    editor = unreal.MaterialEditingLibrary
    shading = unreal.MaterialShadingModel.MSM_UNLIT
    material.set_editor_property('shading_model', shading)
    effect = editor.create_material_expression(material, unreal.MaterialExpressionCustom, 0, 0)
    effect.set_editor_property('output_type', unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    inputs = []
    for name in ('World', 'Facing', 'Seconds', 'UV'):
        channel = unreal.CustomInput()
        channel.set_editor_property('input_name', name)
        inputs.append(channel)
    effect.set_editor_property('inputs', inputs)
    effect.set_editor_property('code', """
        float scan = exp(-pow((frac(World.z / 200.0 - Seconds * 0.23) - 0.5) * 23.0, 2.0));
        float phase = World.z * 1.6 - Seconds * 3.0;
        float stripes = pow(0.5 + 0.5 * sin(phase), 10.0)
                      * (1.0 - smoothstep(0.8, 3.0, fwidth(phase)));
        float2 cell = frac(UV * 96.0) - 0.5;
        float dots = 1.0 - smoothstep(0.06, 0.16, length(cell));
        float pulse = pow(0.5 + 0.5 * sin(World.z * 0.12 - Seconds * 2.4), 8.0);
        return float3(0.012, 0.095, 0.11)
             + float3(0.12, 1.0, 0.76) * (Facing * 0.85 + stripes * 0.20 + scan * 1.4)
             + float3(0.18, 0.43, 1.0) * (dots * (0.15 + pulse * 0.5));
    """)
    for index, (name, expression) in enumerate((
            ('World', unreal.MaterialExpressionWorldPosition),
            ('Facing', unreal.MaterialExpressionFresnel),
            ('Seconds', unreal.MaterialExpressionTime),
            ('UV', unreal.MaterialExpressionTextureCoordinate))):
        node = editor.create_material_expression(material, expression, -320, index * 140)
        if not editor.connect_material_expressions(node, '', effect, name):
            raise RuntimeError('Não foi possível ligar a entrada do holograma: ' + name)
    if not editor.connect_material_property(effect, '', unreal.MaterialProperty.MP_EMISSIVE_COLOR):
        raise RuntimeError('Não foi possível ligar a emissão do holograma.')
    editor.recompile_material(material)
    return material


request = json.loads(Path(os.environ['ez3D_STUDIO_IMPORT_REQUEST']).read_text(encoding='utf-8'))
result = {'success': False, 'assets': [], 'destination': request['destination']}
try:
    destination = request['destination']
    if not destination.startswith('/Game/ez3D/') or '..' in destination:
        raise ValueError('Destino inválido.')
    if unreal.EditorAssetLibrary.does_directory_exist(destination):
        existing = unreal.EditorAssetLibrary.list_assets(destination, recursive=True)
        if existing: raise RuntimeError('Esta criação já foi importada. Nada foi sobrescrito.')
    appearance_materials = []
    if request.get('skeletal') and request.get('appearance_source'):
        appearance = unreal.AssetImportTask()
        appearance.filename = request['appearance_source']
        appearance.destination_path = destination + '/Materiais'
        appearance.automated = True
        appearance.replace_existing = False
        appearance.save = True
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([appearance])
        appearance_assets = [unreal.load_asset(p) for p in unreal.EditorAssetLibrary.list_assets(appearance.destination_path, recursive=True)]
        appearance_materials = [a for a in appearance_assets if isinstance(a, unreal.MaterialInterface)]
        if not appearance_materials: raise RuntimeError('Os materiais PBR da malha de origem não foram importados.')
    task = unreal.AssetImportTask()
    task.filename = request['source']
    task.destination_path = destination + '/Modelo'
    task.destination_name = request.get('model_name','Modelo')
    task.automated = True
    task.replace_existing = False
    task.save = True
    if Path(request['source']).suffix.lower() == '.fbx':
        options = unreal.FbxImportUI()
        options.import_mesh = True
        options.import_as_skeletal = request.get('skeletal', False)
        options.import_animations = request.get('animated', False) and not request.get('animation_files')
        options.import_materials = not bool(appearance_materials)
        options.import_textures = not bool(appearance_materials)
        options.automated_import_should_detect_type = False
        options.mesh_type_to_import = unreal.FBXImportType.FBXIT_SKELETAL_MESH if options.import_as_skeletal else unreal.FBXImportType.FBXIT_STATIC_MESH
        task.options = options
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
    # The GLB imported for its materials may itself contain a skeletal mesh.
    # Animation FBX tracks must bind to the FBX mesh's reference skeleton,
    # never to that auxiliary GLB skeleton (different bone orientations).
    paths = unreal.EditorAssetLibrary.list_assets(task.destination_path, recursive=True)
    assets = [unreal.load_asset(p) for p in paths]
    assets = [a for a in assets if a]
    mesh_type = unreal.SkeletalMesh if request.get('skeletal') else unreal.StaticMesh
    meshes = [a for a in assets if isinstance(a, mesh_type)]
    if not meshes: raise RuntimeError('O importador não criou a malha esperada.')
    for index, filename in enumerate(request.get('animation_files', [])):
        clip = unreal.AssetImportTask()
        clip.filename = filename
        clip.destination_path = destination + '/Animations'
        labels = request.get('animation_names', [])
        label = labels[index] if index < len(labels) else ''
        ascii_label = unicodedata.normalize('NFKD', label).encode('ascii', 'ignore').decode()
        slug = re.sub(r'[^a-zA-Z0-9]+', '_', ascii_label).strip('_')[:48]
        clip.destination_name = f'{slug or "Motion"}_{index + 1:02d}'
        clip.automated = True; clip.replace_existing = False; clip.save = True
        options = unreal.FbxImportUI()
        options.import_mesh = False; options.import_as_skeletal = True
        options.import_animations = True
        options.skeleton = meshes[0].get_editor_property('skeleton')
        options.automated_import_should_detect_type = False
        options.mesh_type_to_import = unreal.FBXImportType.FBXIT_ANIMATION
        clip.options = options
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([clip])
    paths = unreal.EditorAssetLibrary.list_assets(destination, recursive=True)
    assets = [a for a in (unreal.load_asset(p) for p in paths) if a]
    if appearance_materials:
        by_name = {m.get_name(): m for m in appearance_materials}
        for mesh in meshes:
            slots = []
            for slot in mesh.get_editor_property('materials'):
                name = str(slot.get_editor_property('material_slot_name'))
                material = by_name.get(name)
                if material is None and len(appearance_materials) == 1: material = appearance_materials[0]
                if material is None: raise RuntimeError('Não foi possível associar o material da seção: ' + name)
                # Unreal arrays expose live struct proxies. Mutating them in
                # place can make the outer assignment look unchanged, then a
                # mesh rebuild restores the importer's default material.
                replacement = slot.copy()
                replacement.set_editor_property('material_interface', material)
                replacement.set_editor_property('material_slot_name', slot.get_editor_property('material_slot_name'))
                slots.append(replacement)
            mesh.set_editor_property('materials', slots)
    textures = [a for a in assets if isinstance(a, unreal.Texture2D)]
    if request.get('appearance_source') and not textures:
        raise RuntimeError('A malha foi criada, mas as texturas não foram importadas.')
    animations = [a for a in assets if isinstance(a, unreal.AnimSequence)]
    if request.get('animated') and not animations:
        raise RuntimeError('A malha foi criada, mas nenhuma animação foi importada.')
    if request.get('animated'):
        skeletons = [m.get_editor_property('skeleton') for m in meshes]
        if any(a.get_editor_property('skeleton') not in skeletons for a in animations):
            raise RuntimeError('A animação importada não usa o esqueleto da malha.')
        for animation in animations:
            if hasattr(animation, 'set_preview_mesh'):
                animation.set_preview_mesh(meshes[0])
    if appearance_materials:
        expected = {m.get_path_name() for m in appearance_materials}
        for mesh in meshes:
            actual = [s.get_editor_property('material_interface') for s in mesh.get_editor_property('materials')]
            if not actual or any(not m or m.get_path_name() not in expected for m in actual):
                raise RuntimeError('A associação dos materiais da malha não foi confirmada: ' + str([m.get_path_name() if m else None for m in actual]))
    hologram = create_hologram(destination, request.get('skeletal', False))
    assets.append(hologram)
    for asset in assets:
        if not unreal.EditorAssetLibrary.save_loaded_asset(asset, only_if_is_dirty=False):
            raise RuntimeError('Não foi possível salvar ' + asset.get_path_name())
    result.update(success=True, assets=[a.get_path_name() for a in assets],
                  hologram_material=hologram.get_path_name(),
                  textures=[a.get_path_name() for a in textures],
                  types={a.get_path_name(): a.get_class().get_name() for a in assets},
                  animation_count=len(animations), mesh_count=len(meshes),
                  primary_meshes=[m.get_path_name() for m in meshes],
                  primary_skeletons=[m.get_editor_property('skeleton').get_path_name() for m in meshes] if request.get('skeletal') else [],
                  animation_skeletons={a.get_path_name():a.get_editor_property('skeleton').get_path_name() for a in animations})
except Exception as exc:
    result.update(error=str(exc), traceback=traceback.format_exc())
finally:
    Path(request['receipt']).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ez3D_STUDIO_IMPORT=' + json.dumps(result, ensure_ascii=False))
if not result['success']:
    raise RuntimeError(result['error'])
