#!/usr/bin/env python
"""
ez3D Mesh3D Phase 3 runner: Pixal3D image -> GLB.

This script is intentionally standalone. The Unreal plugin calls it as a
subprocess so Pixal3D can live in its own Python/CUDA environment and can be
loaded/unloaded independently from Qwen/FLUX/Unreal.

It supports the Windows Pixal3D-ComfyUI CUDA wheel names
(`cumesh_vb`, `flex_gemm_ap`, `o_voxel_vb_ap`) by registering compatibility
aliases before importing the Pixal3D package.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import importlib.util
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any


def _bool_arg(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _print_json_result(success: bool, **payload: Any) -> None:
    data = {"success": bool(success), **payload}
    print("ez3D_PIXAL3D_RESULT_JSON=" + json.dumps(data, ensure_ascii=False), flush=True)


def _require_path(label: str, value: str, *, file: bool | None = None) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    if file is True and not path.is_file():
        raise FileNotFoundError(f"{label} is not a file: {path}")
    if file is False and not path.is_dir():
        raise FileNotFoundError(f"{label} is not a directory: {path}")
    return path


def _add_source_path(source_root: Path) -> None:
    root = str(source_root)
    if root not in sys.path:
        sys.path.insert(0, root)


def _import_first(names: tuple[str, ...]):
    last_error: Exception | None = None
    for name in names:
        try:
            return importlib.import_module(name), name
        except Exception as exc:  # pragma: no cover - diagnostic path
            last_error = exc
    raise ImportError(f"Could not import any of {names}: {last_error}")


def _load_module_from_file(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not create import spec for {module_name}: {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _ensure_cuda_aliases(source_root: Path) -> None:
    cumesh_impl, cumesh_name = _import_first(("cumesh_vb", "cumesh"))
    sys.modules.setdefault("cumesh", cumesh_impl)
    sys.modules.setdefault("cumesh.bvh", importlib.import_module(f"{cumesh_name}.bvh"))

    flex_impl, flex_name = _import_first(("flex_gemm_ap", "flex_gemm"))
    sys.modules.setdefault("flex_gemm", flex_impl)
    for name in ("ops", "ops.grid_sample", "ops.spconv", "utils", "kernels"):
        try:
            sys.modules.setdefault(f"flex_gemm.{name}", importlib.import_module(f"{flex_name}.{name}"))
        except Exception:
            pass

    o_voxel_impl, o_voxel_name = _import_first(("o_voxel_vb_ap", "o_voxel"))
    sys.modules.setdefault("o_voxel", o_voxel_impl)
    for name in ("convert", "io", "serialize"):
        try:
            sys.modules.setdefault(f"o_voxel.{name}", importlib.import_module(f"{o_voxel_name}.{name}"))
        except Exception:
            pass

    # The Windows o_voxel wheel used by Pixal3D-ComfyUI does not expose the
    # upstream postprocess module. Reuse the compatibility implementation from
    # Pixal3D-ComfyUI, but load the file directly so importing the package does
    # not require the ComfyUI runtime (`import comfy`).
    postprocess_path = source_root / "pixal3d_comfy" / "postprocess.py"
    if not postprocess_path.exists():
        raise FileNotFoundError(f"Pixal3D postprocess bridge not found: {postprocess_path}")
    postprocess = _load_module_from_file("pixal3d_comfy_postprocess_standalone", postprocess_path)
    sys.modules["o_voxel.postprocess"] = postprocess
    setattr(sys.modules["o_voxel"], "postprocess", postprocess)


def _configure_environment(source_root: Path, attention_backend: str) -> None:
    os.environ["OPENCV_IO_ENABLE_OPENEXR"] = "1"
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    os.environ["ATTN_BACKEND"] = attention_backend
    os.environ["SPARSE_ATTN_BACKEND"] = attention_backend
    os.environ.setdefault("FLEX_GEMM_AUTOTUNE_CACHE_PATH", str(source_root / "autotune_cache.json"))
    os.environ.setdefault("FLEX_GEMM_AUTOTUNER_VERBOSE", "1")


def _install_sparse_attention_bridge(source_root: Path, attention_backend: str) -> None:
    """Patch Pixal3D-ComfyUI sparse attention to use official SDPA when needed.

    Pixal3D-ComfyUI carries the practical Windows/NAF fallback patches we need,
    but its sparse attention config does not expose the official SDPA path. The
    official TencentARC Pixal3D checkout beside it does. For Windows machines
    without flash-attn/xformers, load only the official `full_attn.py` under the
    active package name and force `config.ATTN = "sdpa"`.
    """

    if attention_backend != "sdpa":
        return

    official_root = source_root.parent / "Pixal3D"
    official_full_attn = official_root / "pixal3d" / "modules" / "sparse" / "attention" / "full_attn.py"
    if not official_full_attn.exists():
        print(
            f"[Pixal3D] Sparse SDPA bridge skipped; official full_attn.py not found at {official_full_attn}",
            flush=True,
        )
        return

    from pixal3d.modules.sparse import config as sparse_config

    sparse_config.ATTN = "sdpa"
    _load_module_from_file("pixal3d.modules.sparse.attention.full_attn", official_full_attn)
    print(f"[Pixal3D] Sparse SDPA bridge active: {official_full_attn}", flush=True)


def _install_disabled_rembg() -> None:
    from pixal3d.pipelines import rembg
    from PIL import Image

    if hasattr(rembg, "DisabledRMBG"):
        return

    class DisabledRMBG:
        def to(self, _device: str):
            return self

        def cuda(self):
            return self

        def cpu(self):
            return self

        def __call__(self, _image: Image.Image) -> Image.Image:
            raise RuntimeError(
                "Pixal3D background removal is disabled in this standalone runner. "
                "Use --background-mode none or pass an RGBA image with --background-mode keep_alpha."
            )

    rembg.DisabledRMBG = DisabledRMBG


def _prepare_pipeline_config(model_path: Path) -> str:
    data = json.loads((model_path / "pipeline.json").read_text(encoding="utf-8"))
    rembg_config = data.get("args", {}).setdefault("rembg_model", {})
    rembg_config["name"] = "DisabledRMBG"
    rembg_config["args"] = {}
    generated = model_path / "_ez3d_standalone_pipeline.json"
    generated.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return generated.name


def _image_cond_configs(dino_path: Path) -> dict[str, dict[str, Any]]:
    model_name = str(dino_path)
    return {
        "ss": {
            "model_name": model_name,
            "image_size": 512,
            "grid_resolution": 16,
        },
        "shape_512": {
            "model_name": model_name,
            "image_size": 512,
            "grid_resolution": 32,
            "use_naf_upsample": True,
            "naf_target_size": 512,
            "naf_download_if_missing": False,
            "naf_fallback_mode": "duplicate_lr",
        },
        "shape_1024": {
            "model_name": model_name,
            "image_size": 1024,
            "grid_resolution": 64,
            "use_naf_upsample": True,
            "naf_target_size": 512,
            "naf_download_if_missing": False,
            "naf_fallback_mode": "duplicate_lr",
        },
        "tex_1024": {
            "model_name": model_name,
            "image_size": 1024,
            "grid_resolution": 64,
            "use_naf_upsample": True,
            "naf_target_size": 1024,
            "naf_download_if_missing": False,
            "naf_fallback_mode": "duplicate_lr",
        },
    }


def _build_image_cond_model(config: dict[str, Any], shared_dino_models: dict[str, Any]):
    from pixal3d.trainers.flow_matching.mixins.image_conditioned_proj import DinoV3ProjFeatureExtractor

    dino_key = str(config.get("model_name", ""))
    resolved = dict(config)
    shared = shared_dino_models.get(dino_key)
    if shared is not None:
        resolved["shared_dino_model"] = shared
    model = DinoV3ProjFeatureExtractor(**resolved, naf_low_vram=True)
    if dino_key and dino_key not in shared_dino_models:
        dino_model = getattr(model, "model", None)
        if dino_model is not None:
            shared_dino_models[dino_key] = dino_model
    model.eval()
    return model


def _pad_to_square(image, bg_color=(0, 0, 0)):
    from PIL import Image

    width, height = image.size
    if width == height:
        return image
    size = max(width, height)
    canvas = Image.new("RGB", (size, size), bg_color)
    canvas.paste(image.convert("RGB"), ((size - width) // 2, (size - height) // 2))
    return canvas


def _load_pipeline(model_path: Path, dino_path: Path, device: str, low_vram: bool):
    import torch
    from pixal3d.pipelines import Pixal3DImageTo3DPipeline

    print(f"[Pixal3D] Loading model: {model_path}", flush=True)
    pipeline_config = _prepare_pipeline_config(model_path)
    pipeline = Pixal3DImageTo3DPipeline.from_pretrained(str(model_path), config_file=pipeline_config)
    pipeline.low_vram = bool(low_vram)
    if low_vram:
        # Mirrors Pixal3D's official low-VRAM path: keep modules on CPU, but
        # let each stage know which CUDA device it should temporarily use.
        pipeline._device = torch.device(device)

    shared: dict[str, Any] = {}
    configs = _image_cond_configs(dino_path)
    print(f"[Pixal3D] Loading DINO image encoders: {dino_path}", flush=True)
    pipeline.image_cond_model_ss = _build_image_cond_model(configs["ss"], shared)
    pipeline.image_cond_model_shape_512 = _build_image_cond_model(configs["shape_512"], shared)
    pipeline.image_cond_model_shape_1024 = _build_image_cond_model(configs["shape_1024"], shared)
    pipeline.image_cond_model_tex_1024 = _build_image_cond_model(configs["tex_1024"], shared)

    if not low_vram:
        pipeline.cuda()
        for attr in ("image_cond_model_ss", "image_cond_model_shape_512", "image_cond_model_shape_1024", "image_cond_model_tex_1024"):
            getattr(pipeline, attr).to(device)
    return pipeline


def _export_glb(result_mesh, attr_layout: dict[str, Any], resolution: int, output_path: Path, decimation_target: int, texture_size: int, remesh: bool) -> None:
    import numpy as np
    import o_voxel

    mesh = result_mesh
    glb = o_voxel.postprocess.to_glb(
        vertices=mesh.vertices,
        faces=mesh.faces,
        attr_volume=mesh.attrs,
        coords=mesh.coords,
        attr_layout=attr_layout,
        grid_size=int(resolution),
        aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        decimation_target=int(decimation_target),
        texture_size=int(texture_size),
        remesh=bool(remesh),
        remesh_band=1,
        remesh_project=0,
        use_tqdm=True,
    )
    rot = np.array(
        [
            [-1, 0, 0, 0],
            [0, 0, -1, 0],
            [0, -1, 0, 0],
            [0, 0, 0, 1],
        ],
        dtype=np.float64,
    )
    glb.apply_transform(rot)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    glb.export(str(output_path), extension_webp=False)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    from PIL import Image

    image_path = _require_path("image", args.image, file=True)
    output_path = Path(args.output).expanduser().resolve()
    source_root = _require_path("pixal3d-source", args.pixal3d_source, file=False)
    model_path = _require_path("model-path", args.model_path, file=False)
    dino_path = _require_path("dino-path", args.dino_path, file=False)

    _add_source_path(source_root)
    _configure_environment(source_root, args.attention_backend)
    _ensure_cuda_aliases(source_root)
    _install_sparse_attention_bridge(source_root, args.attention_backend)
    _install_disabled_rembg()

    print(f"[Pixal3D] Torch={torch.__version__}, CUDA={torch.version.cuda}, GPU={torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'}", flush=True)
    pipeline = _load_pipeline(model_path, dino_path, args.device, args.low_vram)

    image = Image.open(image_path)
    if args.background_mode == "keep_alpha" and image.mode == "RGBA":
        image_preprocessed = _pad_to_square(image.convert("RGB"))
    elif args.background_mode == "none":
        image_preprocessed = _pad_to_square(image.convert("RGB"))
    else:
        raise RuntimeError("This standalone runner currently supports --background-mode none or RGBA keep_alpha only.")

    camera_params = {
        "camera_angle_x": float(args.manual_camera_angle_x),
        "distance": float(args.manual_distance),
        "mesh_scale": float(args.mesh_scale),
    }

    print(f"[Pixal3D] Running pipeline_type={args.pipeline_type}, steps={args.steps}, low_vram={args.low_vram}", flush=True)
    torch.manual_seed(int(args.seed))
    ss_sampler_override = {
        "steps": int(args.steps),
        "guidance_strength": float(args.guidance),
        "guidance_rescale": 0.7,
        "rescale_t": 5.0,
    }
    shape_sampler_override = {
        "steps": int(args.steps),
        "guidance_strength": float(args.guidance),
        "guidance_rescale": 0.5,
        "rescale_t": 3.0,
    }
    tex_sampler_override = {
        "steps": int(args.steps),
        "guidance_strength": float(args.texture_guidance),
        "guidance_rescale": 0.0,
        "rescale_t": 3.0,
    }

    mesh_list, (shape_slat, tex_slat, resolution) = pipeline.run(
        image_preprocessed,
        camera_params=camera_params,
        seed=int(args.seed),
        sparse_structure_sampler_params=ss_sampler_override,
        shape_slat_sampler_params=shape_sampler_override,
        tex_slat_sampler_params=tex_sampler_override,
        preprocess_image=False,
        return_latent=True,
        pipeline_type=str(args.pipeline_type),
        max_num_tokens=int(args.max_num_tokens),
    )

    mesh = mesh_list[0]
    print(f"[Pixal3D] Exporting GLB: {output_path}", flush=True)
    _export_glb(mesh, dict(pipeline.pbr_attr_layout), int(resolution), output_path, int(args.decimation_target), int(args.texture_size), bool(args.remesh))
    size = output_path.stat().st_size if output_path.exists() else 0

    del shape_slat, tex_slat, mesh_list, pipeline
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return {
        "output": str(output_path),
        "bytes": int(size),
        "pipeline_type": str(args.pipeline_type),
        "resolution": int(resolution),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="ez3D Pixal3D image-to-GLB runner")
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--pixal3d-source", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--dino-path", required=True)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--attention-backend", default="sdpa", choices=["sdpa", "naive", "flash_attn", "flash_attn_3"])
    parser.add_argument("--pipeline-type", default="1024_cascade")
    parser.add_argument("--background-mode", default="none", choices=["none", "keep_alpha"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--guidance", type=float, default=7.5)
    parser.add_argument("--texture-guidance", type=float, default=1.0)
    parser.add_argument("--max-num-tokens", type=int, default=49152)
    parser.add_argument("--manual-camera-angle-x", type=float, default=0.8575560450553894)
    parser.add_argument("--manual-distance", type=float, default=2.0)
    parser.add_argument("--mesh-scale", type=float, default=1.0)
    parser.add_argument("--decimation-target", type=int, default=1000000)
    parser.add_argument("--texture-size", type=int, default=2048)
    parser.add_argument("--remesh", type=_bool_arg, default=True)
    parser.add_argument("--low-vram", type=_bool_arg, default=True)
    args = parser.parse_args()

    started = time.time()
    try:
        payload = _run(args)
        payload["elapsed_seconds"] = round(time.time() - started, 3)
        _print_json_result(True, **payload)
        return 0
    except Exception as exc:
        import traceback

        traceback.print_exc()
        _print_json_result(False, error=f"{type(exc).__name__}: {exc}", elapsed_seconds=round(time.time() - started, 3))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
