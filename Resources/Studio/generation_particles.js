export function createGenerationParticles(canvas) {
  const context = canvas.getContext('2d');
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const pointer = {x: -1000, y: -1000, inside: false};
  const lens = {x: 0, y: 0, strength: 0};
  let yaw = 0, pitch = 0, drag;
  let source, points = [], frame = 0, started = 0;

  function sample(image) {
    const buffer = document.createElement('canvas');
    const resolution = 240;
    const ratio = resolution / Math.max(image.naturalWidth, image.naturalHeight);
    const w = buffer.width = Math.max(1, Math.round(image.naturalWidth * ratio));
    const h = buffer.height = Math.max(1, Math.round(image.naturalHeight * ratio));
    const ctx = buffer.getContext('2d', {willReadFrequently: true});
    ctx.drawImage(image, 0, 0, w, h);
    const {data} = ctx.getImageData(0, 0, w, h);
    const border = [];
    for (let x = 0; x < w; x++) border.push(x, (h - 1) * w + x);
    for (let y = 1; y < h - 1; y++) border.push(y * w, y * w + w - 1);
    const palette = [0, w - 1, (h - 1) * w, h * w - 1].map(p => data.slice(p * 4, p * 4 + 4));
    const background = new Uint8Array(w * h);
    const queue = [];
    function visit(p) {
      if (background[p]) return;
      const i = p * 4;
      if (data[i + 3] < 45 || palette.some(c => c[3] >= 45 &&
        Math.hypot(data[i] - c[0], data[i + 1] - c[1], data[i + 2] - c[2]) < 28)) {
        background[p] = 1;
        queue.push(p);
      }
    }
    for (const p of border) visit(p);
    for (let n = 0; n < queue.length; n++) {
      const p = queue[n], x = p % w;
      if (x > 0) visit(p - 1);
      if (x < w - 1) visit(p + 1);
      if (p >= w) visit(p - w);
      if (p < w * (h - 1)) visit(p + w);
    }
    const result = [];
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const p = y * w + x, i = p * 4;
        if (background[p] || data[i + 3] < 45) continue;
        const hash = Math.imul(p ^ (p >>> 7), 0x57a8ffb);
        const phase = ((hash ^ (hash >>> 16)) >>> 0) / 4294967296;
        const jitter = (Math.imul(hash, 0x2d8cff2d) >>> 0) / 4294967296;
        const light = (data[i] * .21 + data[i + 1] * .72 + data[i + 2] * .07) / 255;
        result.push({x: (x + phase - w / 2) / resolution, y: (y + jitter - h / 2) / resolution, phase, light, depth: (jitter - .5) * .16});
      }
    }
    return result;
  }

  function draw(now) {
    frame = requestAnimationFrame(draw);
    if (!points.length) return;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    if (!w || !h) return;
    const dpr = Math.min(devicePixelRatio || 1, 2);
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, w, h);
    const gentle = reducedMotion.matches;
    const cy = Math.cos(yaw), sy = Math.sin(yaw);
    const cp = Math.cos(pitch), sp = Math.sin(pitch);
    const time = (now - started) / 1000;
    const scale = Math.min(w * .86, h * .66);
    lens.x += (pointer.x - lens.x) * .16;
    lens.y += (pointer.y - lens.y) * .16;
    lens.strength += ((pointer.inside && !drag ? 1 : 0) - lens.strength) * .13;
    const radius = Math.min(115, scale * .28);
    const scan = Math.sin(time * (gentle ? .85 : 1.7)) * .55;
    const assembly = gentle ? 0 : Math.max(0, 1 - time / 1.6) ** 2;
    context.fillStyle = '#f2f3f5';
    for (const point of points) {
      const {phase, light} = point;
      const drift = Math.sin(time * (1.7 + phase) + phase * 80);
      const ripple = Math.sin(time * 2.5 - point.y * 15 + point.x * 8);
      const motion = gentle ? .28 : 1;
      const spread = scale * assembly * .16;
      const angle = phase * 70;
      const z = point.depth * cy - point.x * sy;
      const x = w / 2 + (point.x * cy + point.depth * sy) * scale
        + (drift * 5 + ripple * 6) * motion + Math.cos(angle) * spread;
      const y = h * .44 + (point.y * cp - z * sp) * scale
        + drift * 3 * motion + Math.sin(angle) * spread;
      const dx = x - lens.x, dy = y - lens.y;
      const distance = Math.hypot(dx, dy) / radius;
      const focus = Math.max(0, 1 - distance) ** 2 * lens.strength;
      const sweep = Math.max(0, 1 - Math.abs(point.y - scan) * 10) * (gentle ? .45 : 1);
      const shimmer = Math.max(0, drift) ** 4 * .16 * motion;
      context.globalAlpha = Math.min(1, .36 + light * .35 + sweep * .38 + focus * .10 + shimmer);
      const size = .75 + phase * 1.25 + sweep * .85;
      const lift = focus * 2.1;
      context.fillRect(x + dx * lift - size / 2, y + dy * lift - size / 2, size, size);
    }
    context.globalAlpha = 1;

  }

  canvas.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY};
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener('lostpointercapture', () => { drag = null; });
  canvas.addEventListener('pointercancel', () => { pointer.inside = false; });

  canvas.addEventListener('pointermove', event => {
    if (drag?.id === event.pointerId) {
      yaw += (event.clientX - drag.x) * .012;
      pitch = Math.max(-.65, Math.min(.65, pitch + (event.clientY - drag.y) * .008));
      drag.x = event.clientX;
      drag.y = event.clientY;
    }
    const bounds = canvas.getBoundingClientRect();
    pointer.x = event.clientX - bounds.left;
    pointer.y = event.clientY - bounds.top;
    if (!pointer.inside && lens.strength < .05) {
      lens.x = pointer.x;
      lens.y = pointer.y;
    }
    pointer.inside = true;
  });
  canvas.addEventListener('pointerleave', () => { pointer.inside = false; });

  return url => {
    if (url === source) return;
    source = url;
    if (drag && canvas.hasPointerCapture(drag.id)) canvas.releasePointerCapture(drag.id);
    drag = null;
    yaw = pitch = 0;
    points = [];
    cancelAnimationFrame(frame);
    canvas.hidden = true;
    pointer.inside = false;
    lens.strength = 0;
    if (!url || !context) return;
    const image = new Image();
    image.onload = () => {
      if (source !== url) return;
      points = sample(image);
      if (!points.length) return;
      canvas.hidden = false;
      started = performance.now();
      frame = requestAnimationFrame(draw);
    };
    image.src = url;
  };
}
