#!/usr/bin/env python3
"""Run exactly one local ComfyUI 3D task and print a machine-readable result.

This runner is deliberately a thin bridge.  It never chains geometry, texturing,
rigging, animation, or Unreal import by itself.  The plugin chooses one explicit
mode per invocation and validates the resulting GLB before updating its catalog.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from urllib.parse import urlparse

import requests
from pipeline_resources import release_comfy


RESULT_MARKER = "ez3D_COMFY_MESH3D_RESULT_JSON="


def ensure_server(base_url: str, comfy_root: str, timeout_seconds: int = 180) -> None:
    try:
        response = requests.get(f"{base_url}/system_stats", timeout=3)
        response.raise_for_status()
        return
    except requests.RequestException:
        pass

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8189
    main_py = os.path.join(comfy_root, "main.py")
    if not os.path.isfile(main_py):
        raise FileNotFoundError(f"ComfyUI main.py not found: {main_py}")

    log_path = os.path.join(os.path.dirname(comfy_root), "comfy3d_autostart.log")
    log_file = open(log_path, "a", encoding="utf-8", errors="replace")
    server_env = os.environ.copy()
    server_env.update({
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8:replace",
        "PYTHONLEGACYWINDOWSSTDIO": "0",
        # A 24 GB RTX can keep Hunyuan Paint resident and avoid very slow CPU shuttling.
        "ez3D_HY3D_FULL_GPU": "1",
    })
    subprocess.Popen(
        [sys.executable, "-u", main_py, "--listen", host, "--port", str(port)],
        cwd=comfy_root,
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        creationflags=(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        ),
        close_fds=True,
        env=server_env,
    )

    started = time.monotonic()
    last_error = ""
    while time.monotonic() - started < timeout_seconds:
        try:
            response = requests.get(f"{base_url}/system_stats", timeout=3)
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            last_error = str(exc)
            time.sleep(2)
    raise TimeoutError(f"ComfyUI did not start at {base_url}: {last_error}")


def safe_name(value: str, fallback: str) -> str:
    value = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in value.strip())
    return (value.strip("_") or fallback)[:80]


def upload_image(base_url: str, image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        response = requests.post(
            f"{base_url}/upload/image",
            files={"image": (os.path.basename(image_path), image_file, "application/octet-stream")},
            data={"overwrite": "true"},
            timeout=120,
        )
    response.raise_for_status()
    payload = response.json()
    name = payload.get("name") or payload.get("filename")
    subfolder = payload.get("subfolder") or ""
    if not name:
        raise RuntimeError(f"ComfyUI did not return an uploaded image name: {payload}")
    return f"{subfolder}/{name}".strip("/")


def queue(base_url: str, workflow: dict) -> str:
    response = requests.post(
        f"{base_url}/prompt",
        json={"prompt": workflow, "client_id": f"ez3d-mesh3d-{uuid.uuid4()}"},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {payload}")
    return prompt_id


def wait_for_history(base_url: str, prompt_id: str, timeout_seconds: int) -> dict:
    started = time.monotonic()
    last_error = ""
    while time.monotonic() - started < timeout_seconds:
        try:
            # ComfyUI can block HTTP while a first-run model download/load is happening.
            # A short polling timeout must not turn that into a false tool failure.
            response = requests.get(f"{base_url}/history/{prompt_id}", timeout=300)
            response.raise_for_status()
        except requests.Timeout:
            time.sleep(1.0)
            continue
        payload = response.json()
        entry = payload.get(prompt_id, payload)
        status = entry.get("status", {}) if isinstance(entry, dict) else {}
        messages = status.get("messages", []) if isinstance(status, dict) else []
        for message in messages:
            if isinstance(message, (list, tuple)) and message and message[0] == "execution_error":
                last_error = json.dumps(message[1] if len(message) > 1 else message, ensure_ascii=False)
        if isinstance(entry, dict) and status.get("completed"):
            if status.get("status_str") != "success":
                raise RuntimeError(f"ComfyUI job failed: {last_error or status}")
            return entry
        time.sleep(1.0)
    detail = f" Last ComfyUI error: {last_error}" if last_error else ""
    raise TimeoutError(f"ComfyUI 3D job timed out after {timeout_seconds}s.{detail}")


def geometry_workflow(uploaded_image: str, project_name: str, resolution: int, steps: int, additional_images=None) -> dict:
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": uploaded_image}},
        "2": {"class_type": "DownloadAndLoadStableXModel", "inputs": {"model": "yoso-normal-v1-8-1"}},
        "3": {"class_type": "StableXProcessImage", "inputs": {
            "pipeline": ["2", 0], "image": ["1", 0],
            "processing_resolution": resolution, "controlnet_strength": 1.0, "seed": 42,
        }},
        "4": {"class_type": "IF_TrellisCheckpointLoader", "inputs": {
            "model_name": "trellis-normal-v0-1", "dinov2_model": "dinov2_vitl14_reg",
            "use_fp16": True, "attn_backend": "xformers", "sparse_backend": "spconv",
            "spconv_algo": "implicit_gemm", "smooth_k": True,
        }},
        "5": {"class_type": "IF_TrellisImageTo3D", "inputs": {
            "model": ["4", 0], "mode": "single", "images": ["3", 0], "seed": 42,
            "ss_guidance_strength": 7.5, "ss_sampling_steps": steps,
            "slat_guidance_strength": 3.0, "slat_sampling_steps": steps,
            "mesh_simplify": 0.90, "multimode": "stochastic", "project_name": project_name,
        }},
    }
    batch = ['3', 0]
    for index, view in enumerate(additional_images or []):
        load, normal, combine = [str(10 + index * 3 + offset) for offset in range(3)]
        graph[load] = {'class_type':'LoadImage','inputs':{'image':view}}
        graph[normal] = {'class_type':'StableXProcessImage','inputs':{
            'pipeline':['2',0], 'image':[load,0], 'processing_resolution':resolution,
            'controlnet_strength':1.0,'seed':42}}
        graph[combine] = {'class_type':'ImageBatch','inputs':{'image1':batch,'image2':[normal,0]}}
        batch = [combine,0]
    if additional_images:
        graph['5']['inputs'].update(mode='multi',images=batch)
    return graph


def texture_workflow(image_path: str, mesh_path: str, output_name: str, view_size: int, texture_size: int) -> dict:
    return {
        "1": {"class_type": "Hy3D21LoadMesh", "inputs": {"glb_path": mesh_path}},
        "2": {"class_type": "Hy3D21MeshUVWrap", "inputs": {"trimesh": ["1", 0]}},
        "3": {"class_type": "Hy3D21CameraConfig", "inputs": {
            "camera_azimuths": "0, 90, 180, 270, 0, 180",
            "camera_elevations": "0, 0, 0, 0, 90, -90",
            "view_weights": "1, 0.5, 1, 0.5, 1, 1", "ortho_scale": 1.1,
        }},
        "4": {"class_type": "LoadImage", "inputs": {"image": image_path}},
        "5": {"class_type": "Hy3DMultiViewsGenerator", "inputs": {
            "trimesh": ["2", 0], "camera_config": ["3", 0], "image": ["4", 0],
            "view_size": view_size, "steps": 10, "guidance_scale": 3.0,
            # Node 2 already performs the UV unwrap. Repeating it here made dense
            # Hi3D meshes spend several extra minutes in a second UV pass.
            "texture_size": texture_size, "unwrap_mesh": False, "seed": 42,
        }},
        "6": {"class_type": "Hy3DBakeMultiViews", "inputs": {
            "pipeline": ["5", 0], "camera_config": ["5", 5], "albedo": ["5", 1], "mr": ["5", 2],
        }},
        "7": {"class_type": "Hy3DInPaint", "inputs": {
            "pipeline": ["6", 0], "albedo": ["6", 1], "albedo_mask": ["6", 2],
            "mr": ["6", 3], "mr_mask": ["6", 4], "output_mesh_name": output_name,
        }},
    }


def find_comfy_output(comfy_root: str, mode: str, project_name: str) -> str:
    if mode == "geometry":
        return os.path.join(comfy_root, "output", project_name, f"{project_name}.glb")
    return os.path.join(comfy_root, "output", f"{project_name}.glb")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("geometry", "texture"), required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument('--extra-image', action='append', default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--mesh", default="")
    parser.add_argument("--comfy-root", required=True)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--geometry-resolution", type=int, default=1024)
    parser.add_argument("--geometry-steps", type=int, default=12)
    parser.add_argument("--texture-view-size", type=int, default=512)
    parser.add_argument("--texture-size", type=int, default=1024)
    args = parser.parse_args()

    result = {"success": False, "mode": args.mode, "output": args.output, "error": "", "elapsed_seconds": 0.0}
    started = time.monotonic()
    server_ready = False
    try:
        image = os.path.abspath(args.image)
        mesh = os.path.abspath(args.mesh) if args.mesh else ""
        output = os.path.abspath(args.output)
        comfy_root = os.path.abspath(args.comfy_root)
        name = safe_name(args.project_name, "ez3DMesh")
        if not os.path.isfile(image):
            raise FileNotFoundError(f"input image not found: {image}")
        if len(args.extra_image)>3: raise ValueError('São aceitas até quatro vistas por personagem.')
        for view in args.extra_image:
            if not os.path.isfile(view): raise FileNotFoundError(f'input view not found: {view}')
        if args.mode == "texture" and not os.path.isfile(mesh):
            raise FileNotFoundError(f"input mesh not found: {mesh}")

        base_url = args.base_url.rstrip("/")
        ensure_server(base_url, comfy_root)
        server_ready = True
        uploaded_image = upload_image(base_url, image)
        if args.mode == "geometry":
            views = [upload_image(base_url, os.path.abspath(v)) for v in args.extra_image]
            workflow = geometry_workflow(uploaded_image, name, max(512, min(args.geometry_resolution, 2048)), max(1, min(args.geometry_steps, 40)), views)
        else:
            workflow = texture_workflow(uploaded_image, mesh, name, max(512, min(args.texture_view_size, 1024)), max(512, min(args.texture_size, 2048)))

        prompt_id = queue(base_url, workflow)
        wait_for_history(base_url, prompt_id, args.timeout)
        generated = find_comfy_output(comfy_root, args.mode, name)
        if not os.path.isfile(generated) or os.path.getsize(generated) < 1024:
            raise RuntimeError(f"ComfyUI completed but did not write a valid GLB: {generated}")
        os.makedirs(os.path.dirname(output), exist_ok=True)
        shutil.copy2(generated, output)
        result.update({"success": True, "output": output, "bytes": os.path.getsize(output), "prompt_id": prompt_id})
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if server_ready:
            try:
                result['resource_handoff'] = release_comfy(base_url)
            except Exception as exc:
                result['success'] = False
                result['resource_handoff'] = {'released': False}
                result['error'] = (result['error'] + f' Resource handoff failed: {exc}').strip()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        print(RESULT_MARKER + json.dumps(result, ensure_ascii=False))
    return 0 if result["success"] else 2


if __name__ == "__main__":
    sys.exit(main())
