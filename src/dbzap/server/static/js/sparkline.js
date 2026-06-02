// Canvas-based sparkline renderer (no external dependencies)
// data: number[]  (up to 60 points)
// color: CSS color string

export function drawSparkline(canvas, data, color = '#6366f1') {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  if (!data || data.length === 0) {
    ctx.fillStyle = '#2e3347';
    ctx.fillText('No data', w / 2 - 20, h / 2);
    return;
  }

  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pad = 4;

  const points = data.map((v, i) => ({
    x: pad + (i / (data.length - 1 || 1)) * (w - pad * 2),
    y: h - pad - ((v - min) / range) * (h - pad * 2),
  }));

  // Fill area under line
  ctx.beginPath();
  ctx.moveTo(points[0].x, h - pad);
  for (const { x, y } of points) ctx.lineTo(x, y);
  ctx.lineTo(points[points.length - 1].x, h - pad);
  ctx.closePath();
  ctx.fillStyle = color + '22';
  ctx.fill();

  // Line
  ctx.beginPath();
  ctx.moveTo(points[0].x, points[0].y);
  for (const { x, y } of points) ctx.lineTo(x, y);
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.stroke();
}

// FIFO ring buffer: keeps at most maxSize items
export class RingBuffer {
  constructor(maxSize = 60) {
    this._max = maxSize;
    this._data = [];
  }
  push(v) {
    this._data.push(v);
    if (this._data.length > this._max) this._data.shift();
  }
  get data() { return this._data; }
}
