// Draws one log row's slice of the commit graph from GraphRow.edges (see gitpanel/git/graph.py).
// Lanes never move sideways between rows, so each row is drawn on its own and they line up.
import { svg } from './dom.js';

export const LANE = 16;
const PAD = 10;
const LANES = 7; // lane colour classes s0..s6 / f0..f6

export const laneX = (lane) => PAD + lane * LANE;
export const graphWidth = (maxLane) => PAD * 2 + Math.min(maxLane, 14) * LANE;

export function drawGraph(graph, markers, rowH) {
  const mid = rowH / 2;
  const el = svg('svg', { class: 'graph' });
  const xc = laneX(graph.col);
  const cls = (c) => `s${((c % LANES) + LANES) % LANES}`;
  for (const [kind, lane, color] of graph.edges) {
    const x = laneX(lane);
    let d;
    if (kind === 'pass') d = `M${x} 0V${rowH}`;
    else if (kind === 'in') {
      d = lane === graph.col ? `M${x} 0V${mid}` : `M${x} 0C${x} ${mid * 0.85} ${xc} ${mid * 0.35} ${xc} ${mid}`;
    } else {
      d = lane === graph.col
        ? `M${xc} ${mid}V${rowH}`
        : `M${xc} ${mid}C${xc} ${mid + (rowH - mid) * 0.65} ${x} ${mid + (rowH - mid) * 0.15} ${x} ${rowH}`;
    }
    el.append(svg('path', { d, class: cls(color) }));
  }
  const nodeColor = markers.find((m) => m.node)?.node; // "unpushed" | "incoming"
  const isHead = markers.some((m) => m.kind === 'head');
  const merge = graph.edges.filter((e) => e[0] === 'out').length > 1;
  const lc = ((graph.color % LANES) + LANES) % LANES;
  const laneStroke = `stroke: var(--lane-${lc})`;
  if (isHead) el.append(svg('circle', { cx: xc, cy: mid, r: 8.5, class: 'halo', style: laneStroke }));
  if (nodeColor) el.append(svg('circle', { cx: xc, cy: mid, r: 5, class: nodeColor }));
  else if (merge) el.append(svg('circle', { cx: xc, cy: mid, r: 4, class: 'merge', style: laneStroke }));
  else el.append(svg('circle', { cx: xc, cy: mid, r: 5, class: `f${lc}` }));
  return el;
}
