#!/usr/bin/env python3
"""
ez3D Forge Mesh3D Phase 4 runner: complete UniRig inference.

Scope boundary:
- This script receives an existing GLB/FBX/OBJ mesh and produces a skinned FBX.
- It does not run FLUX, Pixal3D, Kimodo, FBX retargeting, or Unreal import.
- It intentionally wraps UniRig in a separate process so memory is released when
  the phase ends.

Why the wrapper exists:
- UniRig's extract.py is fragile with absolute Windows paths. We stage the input
  inside the UniRig checkout and call it via a relative path.
- The current Windows environment may not have a CUDA build of torch_cluster.
  CPU fallback is supported and was validated, though slower.
- UniRig imports flash_attn even when eager attention is selected. A small shim
  keeps that optional import from blocking the supported eager path on Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


RESULT_MARKER = "ez3D_UNIRIG_RESULT_JSON="


def _truthy(value: str | bool) -> bool:
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _json_result(**kwargs) -> None:
    print(RESULT_MARKER + json.dumps(kwargs, ensure_ascii=False), flush=True)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _run(cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> tuple[int, str]:
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    chunks: list[str] = []
    started = time.time()
    try:
        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if line:
                chunks.append(line)
                print(line, end="", flush=True)

            if proc.poll() is not None:
                rest = proc.stdout.read()
                if rest:
                    chunks.append(rest)
                    print(rest, end="", flush=True)
                return proc.returncode or 0, "".join(chunks)

            if timeout > 0 and (time.time() - started) > timeout:
                proc.kill()
                return -2, "".join(chunks) + f"\nTimeout after {timeout}s\n"

            time.sleep(0.05)
    finally:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def _make_flash_attn_shim(work_dir: Path) -> Path:
    shim = work_dir / "shims"
    module_dir = shim / "flash_attn" / "modules"
    module_dir.mkdir(parents=True, exist_ok=True)
    _write_text(shim / "flash_attn" / "__init__.py", "")
    _write_text(module_dir / "__init__.py", "")
    _write_text(
        module_dir / "mha.py",
        "import math\n"
        "import torch\n"
        "from torch import nn\n"
        "from torch.nn import functional as F\n\n"
        "class MHA(nn.Module):\n"
        "    def __init__(self, embed_dim, num_heads, cross_attn=False, **kwargs):\n"
        "        super().__init__()\n"
        "        if embed_dim % num_heads != 0:\n"
        "            raise ValueError('embed_dim must be divisible by num_heads')\n"
        "        self.embed_dim = embed_dim\n"
        "        self.num_heads = num_heads\n"
        "        self.head_dim = embed_dim // num_heads\n"
        "        self.Wq = nn.Linear(embed_dim, embed_dim)\n"
        "        self.Wkv = nn.Linear(embed_dim, embed_dim * 2)\n"
        "        self.out_proj = nn.Linear(embed_dim, embed_dim)\n\n"
        "    def forward(self, x, x_kv=None, **kwargs):\n"
        "        source = x if x_kv is None else x_kv\n"
        "        batch, query_len, _ = x.shape\n"
        "        key_len = source.shape[1]\n"
        "        q = self.Wq(x).view(batch, query_len, self.num_heads, self.head_dim).transpose(1, 2)\n"
        "        kv = self.Wkv(source).view(batch, key_len, 2, self.num_heads, self.head_dim)\n"
        "        k = kv[:, :, 0].transpose(1, 2)\n"
        "        v = kv[:, :, 1].transpose(1, 2)\n"
        "        out = F.scaled_dot_product_attention(q, k, v, dropout_p=0.0)\n"
        "        out = out.transpose(1, 2).contiguous().view(batch, query_len, self.embed_dim)\n"
        "        return self.out_proj(out)\n",
    )
    return shim


def _patch_model_config(unirig_root: Path, work_dir: Path, disable_flash: bool) -> Path:
    src = unirig_root / "configs" / "model" / "unirig_ar_350m_1024_81920_float32.yaml"
    text = _read_text(src)
    if disable_flash:
        text = text.replace("_attn_implementation: flash_attention_2", "_attn_implementation: eager")
        text = text.replace("flash: True", "flash: False")
    out = work_dir / "ez3d_unirig_ar_model.yaml"
    _write_text(out, text)
    return out


def _patch_task_config(
    unirig_root: Path,
    work_dir: Path,
    model_config: Path,
    checkpoint: Path,
    accelerator: str,
) -> Path:
    src = unirig_root / "configs" / "task" / "quick_inference_skeleton_articulationxl_ar_256.yaml"
    text = _read_text(src)
    text = text.replace(
        "resume_from_checkpoint: experiments/skeleton/articulation-xl_quantization_256/model.ckpt",
        "resume_from_checkpoint: " + checkpoint.as_posix(),
    )
    text = text.replace(
        "model: unirig_ar_350m_1024_81920_float32",
        "model: " + model_config.as_posix(),
    )
    if accelerator.lower() == "cpu":
        text = text.replace("precision: bf16-mixed", "precision: 32")
        text = text.replace("accelerator: gpu", "accelerator: cpu")
    out = work_dir / "ez3d_unirig_skeleton_task.yaml"
    _write_text(out, text)
    return out


def _patch_skin_configs(
    unirig_root: Path,
    work_dir: Path,
    checkpoint: Path,
    accelerator: str,
    disable_flash: bool,
) -> Path:
    model_src = unirig_root / "configs" / "model" / "unirig_skin.yaml"
    model_text = _read_text(model_src)
    if disable_flash:
        model_text = model_text.replace("flash: True", "flash: False")
        model_text = model_text.replace(
            "  enable_qknorm: True\n",
            "  enable_qknorm: True\n  enable_flash: False\n",
            1,
        )
    model_out = work_dir / "ez3d_unirig_skin_model.yaml"
    _write_text(model_out, model_text)

    task_src = unirig_root / "configs" / "task" / "quick_inference_unirig_skin.yaml"
    task_text = _read_text(task_src)
    task_text = task_text.replace(
        "resume_from_checkpoint: experiments/skin/articulation-xl/model.ckpt",
        "resume_from_checkpoint: " + checkpoint.as_posix(),
    )
    task_text = task_text.replace("model: unirig_skin", "model: " + model_out.as_posix())
    # UniRig skinning uses spconv implicit GEMM, which is CUDA-only. Skeleton
    # inference may stay on CPU, but skin weights must run on the GPU.
    task_out = work_dir / "ez3d_unirig_skin_task.yaml"
    _write_text(task_out, task_text)
    return task_out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input mesh path (.glb/.fbx/.obj/etc.)")
    parser.add_argument("--output", required=True, help="Output skeleton FBX path")
    parser.add_argument("--unirig-root", required=True, help="Local UniRig checkout directory")
    parser.add_argument("--skeleton-checkpoint", required=True, help="UniRig skeleton model.ckpt")
    parser.add_argument("--skin-checkpoint", required=True, help="UniRig skin model.ckpt")
    parser.add_argument("--work-dir", default="", help="Optional working directory")
    parser.add_argument("--accelerator", default="cpu", choices=["cpu", "gpu"], help="UniRig trainer accelerator")
    parser.add_argument("--disable-flash", default="true", help="Patch configs to avoid flash_attn")
    parser.add_argument("--faces-target-count", type=int, default=50000)
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()

    started = time.time()
    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    unirig_root = Path(args.unirig_root).expanduser().resolve()
    checkpoint = Path(args.skeleton_checkpoint).expanduser().resolve()
    skin_checkpoint = Path(args.skin_checkpoint).expanduser().resolve()
    work_dir = Path(args.work_dir).expanduser().resolve() if args.work_dir else output_path.parent / "_unirig_work" / output_path.stem

    try:
        if not input_path.exists():
            raise FileNotFoundError(f"input mesh not found: {input_path}")
        if not unirig_root.exists():
            raise FileNotFoundError(f"UniRig root not found: {unirig_root}")
        if not checkpoint.exists():
            raise FileNotFoundError(f"UniRig skeleton checkpoint not found: {checkpoint}")
        if not skin_checkpoint.exists():
            raise FileNotFoundError(f"UniRig skin checkpoint not found: {skin_checkpoint}")
        run_py = unirig_root / "run.py"
        if not run_py.exists():
            raise FileNotFoundError(f"UniRig run.py not found: {run_py}")

        work_dir.mkdir(parents=True, exist_ok=True)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        safe_stamp = f"{int(time.time())}_{os.getpid()}"
        stage_rel = Path("ez3d_stage") / safe_stamp
        stage_dir = unirig_root / stage_rel
        stage_dir.mkdir(parents=True, exist_ok=True)
        staged_input = stage_dir / input_path.name
        shutil.copy2(input_path, staged_input)

        npz_dir = work_dir / "npz"
        npz_dir.mkdir(parents=True, exist_ok=True)

        disable_flash = _truthy(args.disable_flash)
        model_config = _patch_model_config(unirig_root, work_dir, disable_flash)
        task_config = _patch_task_config(unirig_root, work_dir, model_config, checkpoint, args.accelerator)
        skin_task_config = _patch_skin_configs(
            unirig_root, work_dir, skin_checkpoint, args.accelerator, disable_flash
        )
        shim_root = _make_flash_attn_shim(work_dir)

        env = os.environ.copy()
        env["PYTHONPATH"] = str(shim_root) + os.pathsep + str(unirig_root) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        # UniRig's official Lightning checkpoints contain trusted Box metadata.
        # PyTorch 2.6 changed torch.load's default to weights_only=True.
        env.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")

        staged_rel_str = str(stage_rel / input_path.name)
        raw_npz = npz_dir / stage_rel / input_path.with_suffix("").name / "raw_data.npz"

        extract_cmd = [
            sys.executable,
            "-m",
            "src.data.extract",
            "--config",
            str(unirig_root / "configs" / "data" / "quick_inference.yaml"),
            "--require_suffix",
            "obj,fbx,FBX,dae,glb,gltf,vrm",
            "--faces_target_count",
            str(max(1000, int(args.faces_target_count))),
            "--num_runs",
            "1",
            "--force_override",
            "true",
            "--id",
            "0",
            "--time",
            "ez3d_unirig",
            "--input",
            staged_rel_str,
            "--output_dir",
            str(npz_dir),
        ]
        print("[UniRig] extract:", " ".join(extract_cmd), flush=True)
        extract_code, extract_out = _run(extract_cmd, unirig_root, env, args.timeout)
        # bpy sometimes exits with an access-violation code after successfully
        # writing raw_data.npz on Windows. The artifact is the contract here.
        if not raw_npz.exists():
            raise RuntimeError(f"UniRig extract failed (exit={extract_code}); raw_data.npz missing at {raw_npz}\n{extract_out[-4000:]}")

        skeleton_output = work_dir / "predicted_skeleton.fbx"
        skin_output = work_dir / "predicted_skin.fbx"

        run_cmd = [
            sys.executable,
            str(run_py),
            "--task",
            str(task_config),
            "--seed",
            str(args.seed),
            "--input",
            staged_rel_str,
            "--output",
            str(skeleton_output),
            "--npz_dir",
            str(npz_dir),
        ]
        print("[UniRig] skeleton:", " ".join(run_cmd), flush=True)
        run_code, run_out = _run(run_cmd, unirig_root, env, args.timeout)
        if run_code != 0:
            raise RuntimeError(f"UniRig skeleton inference failed (exit={run_code})\n{run_out[-4000:]}")
        if not skeleton_output.exists() or skeleton_output.stat().st_size <= 0:
            raise RuntimeError(f"UniRig did not produce a valid skeleton FBX: {skeleton_output}\n{run_out[-4000:]}")

        staged_skeleton = stage_dir / "predicted_skeleton.fbx"
        shutil.copy2(skeleton_output, staged_skeleton)
        skin_npz_dir = work_dir / "skin_npz"
        skin_npz_dir.mkdir(parents=True, exist_ok=True)
        staged_skeleton_rel = str(stage_rel / staged_skeleton.name)
        skin_raw_npz = skin_npz_dir / stage_rel / staged_skeleton.stem / "raw_data.npz"

        skin_extract_cmd = [
            sys.executable, "-m", "src.data.extract",
            "--config", str(unirig_root / "configs" / "data" / "quick_inference.yaml"),
            "--require_suffix", "obj,fbx,FBX,dae,glb,gltf,vrm",
            "--faces_target_count", str(max(1000, int(args.faces_target_count))),
            "--num_runs", "1", "--force_override", "true", "--id", "0",
            "--time", "ez3d_unirig_skin", "--input", staged_skeleton_rel,
            "--output_dir", str(skin_npz_dir),
        ]
        print("[UniRig] skin extract:", " ".join(skin_extract_cmd), flush=True)
        skin_extract_code, skin_extract_out = _run(skin_extract_cmd, unirig_root, env, args.timeout)
        if not skin_raw_npz.exists():
            raise RuntimeError(
                f"UniRig skin extract failed (exit={skin_extract_code}); raw_data.npz missing at {skin_raw_npz}\n"
                f"{skin_extract_out[-4000:]}"
            )

        skin_cmd = [
            sys.executable, str(run_py), "--task", str(skin_task_config),
            "--seed", str(args.seed), "--input", staged_skeleton_rel,
            "--output", str(skin_output), "--npz_dir", str(skin_npz_dir),
            "--data_name", "raw_data.npz",
        ]
        print("[UniRig] skin weights:", " ".join(skin_cmd), flush=True)
        skin_code, skin_out = _run(skin_cmd, unirig_root, env, args.timeout)
        if skin_code != 0 or not skin_output.exists() or skin_output.stat().st_size <= 0:
            raise RuntimeError(f"UniRig skin inference failed (exit={skin_code})\n{skin_out[-4000:]}")

        merge_cmd = [
            sys.executable, "-m", "src.inference.merge",
            "--require_suffix=obj,fbx,FBX,dae,glb,gltf,vrm", "--num_runs=1", "--id=0",
            f"--source={skin_output}", f"--target={staged_input}", f"--output={output_path}",
        ]
        print("[UniRig] merge:", " ".join(merge_cmd), flush=True)
        merge_code, merge_out = _run(merge_cmd, unirig_root, env, args.timeout)
        if not output_path.exists() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"UniRig merge failed (exit={merge_code})\n{merge_out[-4000:]}")

        _json_result(
            success=True,
            output=str(output_path),
            input=str(input_path),
            bytes=output_path.stat().st_size,
            accelerator=f"{args.accelerator}+gpu_skin",
            elapsed_seconds=round(time.time() - started, 3),
            skeleton=str(skeleton_output),
            skin=str(skin_output),
            note="full_rig_skin_weights_merged",
        )
        return 0
    except Exception as exc:
        _json_result(
            success=False,
            output=str(output_path),
            input=str(input_path),
            error=str(exc),
            accelerator=args.accelerator,
            elapsed_seconds=round(time.time() - started, 3),
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
