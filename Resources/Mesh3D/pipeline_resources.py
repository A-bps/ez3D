"""Resource handoff for dedicated ez3D ComfyUI servers; never kills a process."""
import time
import requests


def release_comfy(base_url, timeout=30, max_reserved_bytes=256 * 1024 * 1024):
    """Fail closed when another job owns the server or unloading cannot be verified.

    ComfyUI acknowledges /free before its worker handles it. Observe PyTorch's
    reserved-memory counters afterwards rather than treating HTTP 200 as release.
    This measures this server's allocator, not other apps or total GPU usage.
    """
    url = base_url.rstrip('/')
    queue = requests.get(url + '/queue', timeout=5)
    queue.raise_for_status()
    payload = queue.json()
    if not isinstance(payload, dict) or 'queue_running' not in payload or 'queue_pending' not in payload:
        raise RuntimeError('Resposta de fila inválida; liberação não confirmada.')
    if payload['queue_running'] or payload['queue_pending']:
        raise RuntimeError('Servidor ocupado; nenhuma tarefa foi interrompida e a próxima etapa deve aguardar.')
    response = requests.post(url + '/free', json={'unload_models': True, 'free_memory': True}, timeout=15)
    response.raise_for_status()
    deadline = time.monotonic() + timeout
    while True:
        response = requests.get(url + '/system_stats', timeout=5)
        response.raise_for_status()
        devices = response.json().get('devices', [])
        cuda = [d for d in devices if d.get('type') == 'cuda']
        if not devices:
            raise RuntimeError('Servidor não informou os dispositivos; liberação não confirmada.')
        if any(d.get('type') not in ('cpu', 'cuda') for d in devices):
            raise RuntimeError('Tipo de dispositivo desconhecido; liberação não confirmada.')
        if all(isinstance(d.get('torch_vram_total'), (int, float)) and
               0 <= d['torch_vram_total'] <= max_reserved_bytes for d in cuda):
            return {'released': True, 'scope': 'comfy_allocator', 'reserved_bytes':
                    sum(d['torch_vram_total'] for d in cuda)}
        if time.monotonic() >= deadline:
            raise TimeoutError('Modelo ainda ocupa VRAM ou contador indisponível; próxima etapa bloqueada.')
        time.sleep(0.25)
