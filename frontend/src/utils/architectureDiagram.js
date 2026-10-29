/**
 * Architecture diagram renderer (Phase 8D).
 *
 * Turns the `diagram` graph returned by GET /api/projects/{id}/architecture
 * ({nodes, edges}) into a standalone SVG string. Nothing is invented here:
 * every box is a layer the backend actually detected (with its real file / LOC
 * / language numbers) and every arrow is a group of API calls the backend's
 * static analysis actually matched. Layout is the only thing computed.
 *
 * The same string is what the dashboard displays AND what "Export SVG" saves,
 * so the exported file is exactly the rendered diagram. All styling uses
 * presentation attributes (no external CSS) so the file is self-contained.
 *
 * Every label originates from an uploaded project, so all text is XML-escaped.
 */

const BOX_W = 210;
const BOX_H = 104;
const GAP_X = 70;
const GAP_Y = 56;
const MARGIN = 24;
const TITLE_H = 30;
const LEGEND_H = 34;

const MAIN_ROW = ["frontend", "api", "backend", "database"];
const SECOND_ROW = ["shared", "tests", "configuration", "unknown"];

const LAYER_COLORS = {
  frontend: "#2563eb",
  backend: "#7c3aed",
  api: "#0891b2",
  database: "#b45309",
  shared: "#4b5563",
  tests: "#15803d",
  configuration: "#6b7280",
  unknown: "#9ca3af",
};

const CONFIDENCE_COLORS = { high: "#16a34a", medium: "#d97706", low: "#dc2626" };

export function escapeXml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function truncate(text, max) {
  const s = String(text ?? "");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function languagesLabel(languages) {
  const parts = Object.entries(languages || {}).map(([name, count]) => `${name} ${count}`);
  return parts.length ? parts.join(", ") : "no languages";
}

/** Positions for every node. Returns {positions: Map(id -> {x,y}), width, height}. */
export function layoutDiagram(diagram) {
  const nodes = diagram?.nodes || [];
  const ids = new Set(nodes.map((n) => n.id));
  const main = MAIN_ROW.filter((id) => ids.has(id));
  const second = SECOND_ROW.filter((id) => ids.has(id));
  // Any layer name this renderer does not know still gets drawn.
  const known = new Set([...MAIN_ROW, ...SECOND_ROW]);
  const extras = nodes.map((n) => n.id).filter((id) => !known.has(id));
  const secondRow = [...second, ...extras];

  const positions = new Map();
  const place = (row, rowIndex) =>
    row.forEach((id, i) => {
      positions.set(id, {
        x: MARGIN + i * (BOX_W + GAP_X),
        y: MARGIN + TITLE_H + rowIndex * (BOX_H + GAP_Y),
      });
    });
  let rowIndex = 0;
  if (main.length) place(main, rowIndex++);
  if (secondRow.length) place(secondRow, rowIndex++);

  const columns = Math.max(main.length, secondRow.length, 1);
  const rows = Math.max(rowIndex, 1);
  return {
    positions,
    width: MARGIN * 2 + columns * BOX_W + (columns - 1) * GAP_X,
    height: MARGIN * 2 + TITLE_H + rows * BOX_H + (rows - 1) * GAP_Y + LEGEND_H,
  };
}

// Point where the line from rect-centre `from` towards `to` leaves the rect.
function clipToBox(from, to) {
  const cx = from.x + BOX_W / 2;
  const cy = from.y + BOX_H / 2;
  const tx = to.x + BOX_W / 2;
  const ty = to.y + BOX_H / 2;
  const dx = tx - cx;
  const dy = ty - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const scale = Math.min(
    dx !== 0 ? BOX_W / 2 / Math.abs(dx) : Infinity,
    dy !== 0 ? BOX_H / 2 / Math.abs(dy) : Infinity,
  );
  return { x: cx + dx * scale, y: cy + dy * scale };
}

function renderNode(node, pos) {
  const color = LAYER_COLORS[node.layer] || LAYER_COLORS.unknown;
  const conf = CONFIDENCE_COLORS[node.confidence] || "#6b7280";
  const badges = [];
  if (node.unmatched_calls) badges.push(`${node.unmatched_calls} unmatched call${node.unmatched_calls === 1 ? "" : "s"}`);
  if (node.unused_endpoints) badges.push(`${node.unused_endpoints} potentially unused`);
  if (node.uncertain) badges.push(`${node.uncertain} uncertain`);
  if (node.internal_matches) badges.push(`${node.internal_matches} internal match${node.internal_matches === 1 ? "" : "es"}`);
  const badgeLine = badges.length ? truncate(badges.join(" · "), 34) : "";
  const x = pos.x;
  const y = pos.y;
  const dashed = node.layer === "unknown" ? ' stroke-dasharray="6 4"' : "";
  return [
    `<g data-layer="${escapeXml(node.id)}">`,
    `<rect x="${x}" y="${y}" width="${BOX_W}" height="${BOX_H}" rx="10" fill="#ffffff" stroke="${color}" stroke-width="2"${dashed}/>`,
    `<rect x="${x}" y="${y}" width="${BOX_W}" height="26" rx="10" fill="${color}"/>`,
    `<rect x="${x}" y="${y + 16}" width="${BOX_W}" height="10" fill="${color}"/>`,
    `<text x="${x + 12}" y="${y + 18}" font-size="13" font-weight="700" fill="#ffffff">${escapeXml(node.label)}</text>`,
    `<circle cx="${x + BOX_W - 14}" cy="${y + 13}" r="5" fill="${conf}" stroke="#ffffff" stroke-width="1.5"/>`,
    `<text x="${x + 12}" y="${y + 46}" font-size="12" fill="#111827">${escapeXml(node.files)} file${node.files === 1 ? "" : "s"} · ${escapeXml(node.loc)} LOC</text>`,
    `<text x="${x + 12}" y="${y + 64}" font-size="11" fill="#4b5563">${escapeXml(truncate(languagesLabel(node.languages), 34))}</text>`,
    `<text x="${x + 12}" y="${y + 82}" font-size="10" fill="#6b7280">confidence: ${escapeXml(node.confidence)}</text>`,
    badgeLine
      ? `<text x="${x + 12}" y="${y + 96}" font-size="10" fill="#b45309">${escapeXml(badgeLine)}</text>`
      : "",
    "</g>",
  ].join("");
}

/** Build the complete standalone SVG markup for a diagram graph. */
export function renderArchitectureSvg(diagram, { title = "Architecture diagram" } = {}) {
  const { positions, width, height } = layoutDiagram(diagram);
  const nodes = (diagram?.nodes || []).filter((n) => positions.has(n.id));
  const edges = diagram?.edges || [];

  const parts = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" role="img" aria-label="${escapeXml(title)}">`,
    `<defs><marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="#374151"/></marker></defs>`,
    `<rect x="0" y="0" width="${width}" height="${height}" fill="#ffffff"/>`,
    `<text x="${MARGIN}" y="${MARGIN + 8}" font-size="15" font-weight="700" fill="#111827">${escapeXml(title)}</text>`,
  ];

  for (const edge of edges) {
    const a = positions.get(edge.source);
    const b = positions.get(edge.target);
    if (!a || !b) continue;
    const p1 = clipToBox(a, b);
    const p2 = clipToBox(b, a);
    const mx = (p1.x + p2.x) / 2;
    const my = (p1.y + p2.y) / 2;
    const conf = CONFIDENCE_COLORS[edge.confidence] || "#374151";
    const dash = edge.confidence === "low" ? ' stroke-dasharray="5 4"' : "";
    parts.push(
      `<g data-edge="${escapeXml(edge.source)}-${escapeXml(edge.target)}">`,
      `<line x1="${p1.x.toFixed(1)}" y1="${p1.y.toFixed(1)}" x2="${p2.x.toFixed(1)}" y2="${p2.y.toFixed(1)}" stroke="#374151" stroke-width="2"${dash} marker-end="url(#arch-arrow)"/>`,
      `<rect x="${(mx - 62).toFixed(1)}" y="${(my - 22).toFixed(1)}" width="124" height="30" rx="6" fill="#ffffff" stroke="${conf}" stroke-width="1"/>`,
      `<text x="${mx.toFixed(1)}" y="${(my - 9).toFixed(1)}" font-size="10" text-anchor="middle" fill="#111827">${escapeXml(truncate(edge.label, 24))}</text>`,
      `<text x="${mx.toFixed(1)}" y="${(my + 3).toFixed(1)}" font-size="9" text-anchor="middle" fill="${conf}">${escapeXml(edge.confidence)} confidence</text>`,
      `</g>`,
    );
  }

  for (const node of nodes) parts.push(renderNode(node, positions.get(node.id)));

  const legendY = height - LEGEND_H / 2 + 2;
  const note = edges.length
    ? "Arrows = frontend API calls matched to backend endpoints by static analysis."
    : "No matched frontend→backend API calls were found, so no arrows are drawn.";
  parts.push(
    `<text x="${MARGIN}" y="${legendY}" font-size="10" fill="#6b7280">${escapeXml(note)} Dot colour = layer confidence.</text>`,
    "</svg>",
  );
  return parts.join("");
}

/** Download an SVG string as a file (browser only). */
export function downloadSvgString(markup, filename) {
  const doc = `<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n${markup}`;
  const blob = new Blob([doc], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
