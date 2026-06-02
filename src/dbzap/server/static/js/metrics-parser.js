// Parse Prometheus text exposition format into structured JS objects
// Returns: { counters, histograms, gauges }
// counters[name][labelStr] = value
// histograms[name][labelStr] = { buckets: [[le, count], ...], count, sum }
// gauges[name] = value  OR  gauges[name][labelStr] = value

export function parsePrometheus(text) {
  const counters   = {};
  const histograms = {};
  const gauges     = {};

  for (const line of text.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;

    const { name, labels, value } = parseLine(trimmed);
    if (name === null) continue;

    if (name.endsWith('_bucket')) {
      const base = name.slice(0, -7);
      histograms[base] ??= {};
      const le = parseFloat(labels.le ?? 'Inf');
      const key = labelKey(labels, ['le']);
      histograms[base][key] ??= { buckets: [], count: 0, sum: 0 };
      histograms[base][key].buckets.push([le, value]);
    } else if (name.endsWith('_count')) {
      const base = name.slice(0, -6);
      const key = labelKey(labels);
      histograms[base] ??= {};
      histograms[base][key] ??= { buckets: [], count: 0, sum: 0 };
      histograms[base][key].count = value;
    } else if (name.endsWith('_sum')) {
      const base = name.slice(0, -4);
      const key = labelKey(labels);
      histograms[base] ??= {};
      histograms[base][key] ??= { buckets: [], count: 0, sum: 0 };
      histograms[base][key].sum = value;
    } else {
      const key = labelKey(labels);
      if (key === '') {
        gauges[name] = value;
      } else {
        if (typeof counters[name] !== 'object') counters[name] = {};
        counters[name][key] = value;
      }
    }
  }
  return { counters, histograms, gauges };
}

function parseLine(line) {
  const braceOpen  = line.indexOf('{');
  const braceClose = line.indexOf('}');
  let name, labelsStr, rest;

  if (braceOpen !== -1 && braceClose !== -1) {
    name      = line.slice(0, braceOpen);
    labelsStr = line.slice(braceOpen + 1, braceClose);
    rest      = line.slice(braceClose + 1).trim();
  } else {
    const sp = line.indexOf(' ');
    name      = line.slice(0, sp);
    labelsStr = '';
    rest      = line.slice(sp + 1).trim();
  }

  const value = parseFloat(rest.split(' ')[0]);
  if (isNaN(value)) return { name: null, labels: {}, value: 0 };

  const labels = {};
  if (labelsStr) {
    for (const pair of labelsStr.split(',')) {
      const eq = pair.indexOf('=');
      if (eq === -1) continue;
      const k = pair.slice(0, eq).trim();
      const v = pair.slice(eq + 1).trim().replace(/^"|"$/g, '');
      labels[k] = v;
    }
  }
  return { name, labels, value };
}

function labelKey(labels, exclude = []) {
  return Object.entries(labels)
    .filter(([k]) => !exclude.includes(k))
    .map(([k, v]) => `${k}="${v}"`)
    .join(',');
}

// Estimate percentile from histogram buckets using linear interpolation
export function estimatePercentile(buckets, p) {
  if (!buckets || buckets.length === 0) return 0;
  const sorted = [...buckets].sort((a, b) => a[0] - b[0]);
  const total = sorted[sorted.length - 1][1];
  if (total === 0) return 0;
  const target = total * p;

  let prevLe = 0, prevCount = 0;
  for (const [le, count] of sorted) {
    if (count >= target) {
      const countInBucket = count - prevCount;
      if (countInBucket === 0) return prevLe * 1000;
      const fraction = (target - prevCount) / countInBucket;
      const upper = isFinite(le) ? le : prevLe * 2;
      return (prevLe + fraction * (upper - prevLe)) * 1000; // ms
    }
    prevLe = le;
    prevCount = count;
  }
  return prevLe * 1000;
}
