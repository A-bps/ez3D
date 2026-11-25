#!/usr/bin/env python3
"""ez3D Forge Mesh3D Phase 5 runner: Kimodo text-to-motion.

This wrapper is intentionally conservative:
  - it does not retarget motion to a generated mesh/skeleton;
  - it does not export final Unreal-ready FBX;
  - it only asks Kimodo to generate motion files (.npz and optionally .bvh).

Retarget/export is Phase 6. The wrapper emits one machine-readable marker so
the Unreal plugin can distinguish CLI success from actual output success.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


RESULT_MARKER = "ez3D_KIMODO_RESULT_JSON="


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _json_marker(payload: dict[str, Any]) -> None:
    print(RESULT_MARKER + json.dumps(payload, ensure_ascii=True, sort_keys=True), flush=True)


def _bool_arg(value: str | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def _tail(text: str, max_chars: int = 8000) -> str:
    return text[-max_chars:] if len(text) > max_chars else text


def _has_local_llm2vec_weights(cache_dir: Path) -> bool:
    """Kimodo's local encoder is unusable until the gated Llama base is complete."""
    if not cache_dir.exists():
        return False
    patterns = ("*.safetensors", "pytorch_model*.bin")
    total_bytes = 0
    for pattern in patterns:
        for candidate in cache_dir.rglob(pattern):
            try:
                total_bytes += candidate.stat().st_size
            except OSError:
                pass
    return total_bytes >= 1_000_000_000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ez3D Forge Kimodo motion runner")
    parser.add_argument("--kimodo-root", required=True, help="Kimodo repository root.")
    parser.add_argument("--output-stem", required=True, help="Output stem without extension.")
    parser.add_argument("--prompt", required=True, help="Text prompt describing the animation/motion.")
    parser.add_argument("--model", default="Kimodo-SOMA-RP-v1", help="Kimodo model name or alias.")
    parser.add_argument("--duration", default="5.0", help="Motion duration in seconds.")
    parser.add_argument("--diffusion-steps", type=int, default=100, help="Kimodo diffusion steps.")
    parser.add_argument("--num-transition-frames", type=int, default=5)
    parser.add_argument("--seed", type=int, default=123)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--checkpoint-dir", default="", help="Directory that contains Kimodo model folders.")
    parser.add_argument("--huggingface-cache-dir", default="", help="HF cache for text encoder downloads/cache.")
    parser.add_argument("--text-encoder-device", default="cpu", help="cpu/cuda/auto for LLM2Vec encoder.")
    parser.add_argument("--text-encoder-mode", default="auto", help="auto/local/api for Kimodo text encoder.")
    parser.add_argument("--source-skeleton-fbx", default="", help="Optional provenance path from UniRig Phase 4.")
    parser.add_argument("--export-bvh", default="true")
    parser.add_argument("--bvh-standard-tpose", default="true")
    parser.add_argument("--no-postprocess", default="false")
    parser.add_argument("--save-example-dir", default="false")
    return parser.parse_args()


def main() -> int:
    _configure_stdio()
    args = parse_args()
    started = time.time()

    root = Path(args.kimodo_root).resolve()
    output_stem = Path(args.output_stem).resolve()
    npz_path = output_stem.with_suffix(".npz")
    bvh_path = output_stem.with_suffix(".bvh")
    source_skeleton = Path(args.source_skeleton_fbx).resolve() if args.source_skeleton_fbx else None

    try:
        if not root.exists():
            raise FileNotFoundError(f"Kimodo root not found: {root}")
        if not (root / "kimodo").exists():
            raise FileNotFoundError(f"Kimodo package folder not found under root: {root / 'kimodo'}")
        if not args.prompt.strip():
            raise ValueError("Animation prompt is empty.")
        if source_skeleton and not source_skeleton.exists():
            raise FileNotFoundError(f"Source skeleton FBX not found: {source_skeleton}")

        output_stem.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8:replace"
        env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
        env["KMP_DUPLICATE_LIB_OK"] = env.get("KMP_DUPLICATE_LIB_OK", "TRUE")
        env["PYTORCH_CUDA_ALLOC_CONF"] = env.get("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

        existing_pythonpath = env.get("PYTHONPATH", "")
        root_str = str(root)
        env["PYTHONPATH"] = root_str if not existing_pythonpath else root_str + os.pathsep + existing_pythonpath

        if args.checkpoint_dir.strip():
            checkpoint_dir = Path(args.checkpoint_dir).resolve()
            if not checkpoint_dir.exists():
                raise FileNotFoundError(f"Kimodo checkpoint dir not found: {checkpoint_dir}")
            env["CHECKPOINT_DIR"] = str(checkpoint_dir)
        if args.huggingface_cache_dir.strip():
            hf_cache = Path(args.huggingface_cache_dir).resolve()
            hf_cache.mkdir(parents=True, exist_ok=True)
            env["HUGGINGFACE_CACHE_DIR"] = str(hf_cache)
            env["HF_HOME"] = str(hf_cache)
            if args.text_encoder_mode.strip().lower() in {"local", "auto"} and not _has_local_llm2vec_weights(hf_cache):
                raise RuntimeError(
                    "Kimodo bloqueado antes da inferencia: o text encoder LLM2Vec depende de "
                    "meta-llama/Meta-Llama-3-8B-Instruct, mas os pesos locais estao incompletos. "
                    "A conta Hugging Face configurada precisa aceitar a licenca e receber acesso ao repositorio gated."
                )
        if args.text_encoder_device.strip():
            env["TEXT_ENCODER_DEVICE"] = args.text_encoder_device.strip()
        if args.text_encoder_mode.strip():
            env["TEXT_ENCODER_MODE"] = args.text_encoder_mode.strip()

        cmd = [
            sys.executable,
            "-u",
            "-m",
            "kimodo.scripts.generate",
            args.prompt.strip(),
            "--model",
            args.model.strip() or "Kimodo-SOMA-RP-v1",
            "--duration",
            str(args.duration),
            "--num_samples",
            "1",
            "--diffusion_steps",
            str(max(1, int(args.diffusion_steps))),
            "--num_transition_frames",
            str(max(0, int(args.num_transition_frames))),
            "--output",
            str(output_stem),
            "--seed",
            str(int(args.seed)),
        ]

        if _bool_arg(args.export_bvh, True):
            cmd.append("--bvh")
        if _bool_arg(args.bvh_standard_tpose, True):
            cmd.append("--bvh_standard_tpose")
        if _bool_arg(args.no_postprocess, False):
            cmd.append("--no-postprocess")
        if _bool_arg(args.save_example_dir, False):
            cmd.append("--save_example_dir")

        print("[ez3DKimodo] launching Kimodo CLI", flush=True)
        print("[ez3DKimodo] command: " + " ".join(cmd), flush=True)
        print(f"[ez3DKimodo] CHECKPOINT_DIR={env.get('CHECKPOINT_DIR', '')}", flush=True)
        print(f"[ez3DKimodo] TEXT_ENCODER_MODE={env.get('TEXT_ENCODER_MODE', '')}", flush=True)
        print(f"[ez3DKimodo] TEXT_ENCODER_DEVICE={env.get('TEXT_ENCODER_DEVICE', '')}", flush=True)
        if source_skeleton:
            print(f"[ez3DKimodo] source skeleton provenance={source_skeleton}", flush=True)

        completed = subprocess.run(
            cmd,
            cwd=str(root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, int(args.timeout)),
        )

        if completed.stdout:
            print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n", flush=True)

        npz_bytes = _file_size(npz_path)
        bvh_bytes = _file_size(bvh_path)
        export_bvh = _bool_arg(args.export_bvh, True)

        success = completed.returncode == 0 and npz_bytes > 0 and (not export_bvh or bvh_bytes > 0)
        error = ""
        if not success:
            if completed.returncode != 0:
                error = f"Kimodo CLI failed with exit code {completed.returncode}."
            elif npz_bytes <= 0:
                error = f"Kimodo did not produce a non-empty NPZ: {npz_path}"
            elif export_bvh and bvh_bytes <= 0:
                error = f"Kimodo did not produce a non-empty BVH: {bvh_path}"

        _json_marker(
            {
                "success": success,
                "output_stem": str(output_stem),
                "npz": str(npz_path),
                "bvh": str(bvh_path) if export_bvh else "",
                "npz_bytes": npz_bytes,
                "bvh_bytes": bvh_bytes,
                "model": args.model,
                "prompt": args.prompt,
                "duration": str(args.duration),
                "diffusion_steps": int(args.diffusion_steps),
                "seed": int(args.seed),
                "source_skeleton_fbx": str(source_skeleton) if source_skeleton else "",
                "return_code": completed.returncode,
                "elapsed_seconds": round(time.time() - started, 3),
                "error": error,
                "stdout_tail": _tail(completed.stdout or "", 4000),
                "note": "motion_only_npz_bvh_phase5_no_retarget",
            }
        )
        return 0 if success else 2

    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        _json_marker(
            {
                "success": False,
                "output_stem": str(output_stem),
                "npz": str(npz_path),
                "bvh": str(bvh_path),
                "npz_bytes": _file_size(npz_path),
                "bvh_bytes": _file_size(bvh_path),
                "model": args.model,
                "prompt": args.prompt,
                "source_skeleton_fbx": str(source_skeleton) if source_skeleton else "",
                "return_code": -2,
                "elapsed_seconds": round(time.time() - started, 3),
                "error": f"Kimodo timed out after {args.timeout}s.",
                "stdout_tail": _tail(stdout, 4000),
                "note": "motion_only_npz_bvh_phase5_timeout",
            }
        )
        return 2
    except Exception as exc:  # noqa: BLE001 - plugin runner must serialize failures.
        _json_marker(
            {
                "success": False,
                "output_stem": str(output_stem),
                "npz": str(npz_path),
                "bvh": str(bvh_path),
                "npz_bytes": _file_size(npz_path),
                "bvh_bytes": _file_size(bvh_path),
                "model": args.model,
                "prompt": args.prompt,
                "source_skeleton_fbx": str(source_skeleton) if source_skeleton else "",
                "return_code": -1,
                "elapsed_seconds": round(time.time() - started, 3),
                "error": f"{type(exc).__name__}: {exc}",
                "stdout_tail": "",
                "note": "motion_only_npz_bvh_phase5_exception",
            }
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
