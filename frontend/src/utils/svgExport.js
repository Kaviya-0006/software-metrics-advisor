const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * Combine one or more ALREADY-RENDERED <svg> elements into a single
 * standalone, self-contained SVG document, and trigger a browser
 * download of it.
 *
 * This deliberately does NOT redraw or regenerate any chart -- it clones
 * the exact SVG nodes already on screen (from HealthGauge.jsx and
 * HistoryTrends.jsx, the only two genuinely SVG-based visualizations in
 * this dashboard) and stacks them vertically with a title above each,
 * reusing each chart's own `aria-label` as that title when present.
 *
 * CategoryScoresChart.jsx and RiskOverview.jsx are div/CSS-bar based in
 * the current implementation, not SVG -- they are intentionally never
 * passed to this function, since including them would mean redrawing
 * them as SVG, which this feature does not do.
 *
 * @param {{svg: SVGSVGElement, title?: string}[]} sections
 * @param {string} filename
 */
export function exportDashboardSVG(sections, filename) {
  const usable = sections.filter((s) => s && s.svg instanceof SVGSVGElement);
  if (usable.length === 0) {
    throw new Error("No SVG visualizations are currently available to export.");
  }

  const MARGIN = 16;
  const TITLE_HEIGHT = 22;

  // Work out each chart's own intrinsic size from its viewBox (preferred,
  // since it reflects the chart's real internal coordinate system) or,
  // failing that, its explicit width/height attributes.
  const placed = [];
  let y = MARGIN;
  let maxWidth = 0;

  for (const { svg, title } of usable) {
    let width;
    let height;
    const viewBox = svg.getAttribute("viewBox");
    if (viewBox) {
      const parts = viewBox.split(/\s+/).map(Number);
      if (parts.length === 4 && parts.every((n) => !Number.isNaN(n))) {
        [, , width, height] = parts;
      }
    }
    if (!width || !height) {
      width = svg.width?.baseVal?.value || 400;
      height = svg.height?.baseVal?.value || 200;
    }

    const resolvedTitle = title || svg.getAttribute("aria-label") || "";
    placed.push({ svg, title: resolvedTitle, width, height, y: y + TITLE_HEIGHT });
    y += TITLE_HEIGHT + height + MARGIN;
    maxWidth = Math.max(maxWidth, width);
  }

  const totalWidth = maxWidth + MARGIN * 2;
  const totalHeight = y;

  const root = document.createElementNS(SVG_NS, "svg");
  root.setAttribute("xmlns", SVG_NS);
  root.setAttribute("width", String(totalWidth));
  root.setAttribute("height", String(totalHeight));
  root.setAttribute("viewBox", `0 0 ${totalWidth} ${totalHeight}`);

  // Self-contained styling, since a downloaded standalone file can't rely
  // on the app's external stylesheet or CSS custom properties -- these
  // values are the same literal colors index.css uses for these charts.
  const style = document.createElementNS(SVG_NS, "style");
  style.textContent = `
    text { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    .export-section-title { font-size: 13px; font-weight: 700; fill: #111827; }
    .trend-chart-gridline { stroke: #e5e7eb; stroke-width: 1; }
    .trend-chart-line { fill: none; stroke: #4f46e5; stroke-width: 2; }
    .trend-chart-point { fill: #4f46e5; stroke: #fff; stroke-width: 1.5; }
    .trend-chart-point-current { fill: #16a34a; }
    .trend-chart-axis-label { font-size: 9px; fill: #6b7280; }
    .health-gauge-score { font-size: 30px; font-weight: 800; fill: #111827; }
    .health-gauge-max { font-size: 13px; fill: #9ca3af; }
  `;
  root.appendChild(style);

  const background = document.createElementNS(SVG_NS, "rect");
  background.setAttribute("x", "0");
  background.setAttribute("y", "0");
  background.setAttribute("width", String(totalWidth));
  background.setAttribute("height", String(totalHeight));
  background.setAttribute("fill", "#ffffff");
  root.appendChild(background);

  for (const { svg, title, width, height, y: chartY } of placed) {
    if (title) {
      const titleEl = document.createElementNS(SVG_NS, "text");
      titleEl.setAttribute("x", String(MARGIN));
      titleEl.setAttribute("y", String(chartY - 6));
      titleEl.setAttribute("class", "export-section-title");
      titleEl.textContent = title;
      root.appendChild(titleEl);
    }

    // Clone the chart's real rendered <svg> node as-is (its own viewBox
    // and internal markup untouched) and position it as a nested SVG --
    // a nested <svg> establishes its own coordinate viewport per the SVG
    // spec, which is exactly what's needed here: each chart keeps its
    // own internal geometry exactly as rendered, only its placement in
    // the combined document changes.
    const clone = svg.cloneNode(true);
    clone.setAttribute("x", String(MARGIN));
    clone.setAttribute("y", String(chartY));
    clone.setAttribute("width", String(width));
    clone.setAttribute("height", String(height));
    // Remove any leftover aria-label on the clone -- its content is
    // already promoted to the visible title text element above, and
    // duplicating it as a non-visible attribute on a static export file
    // serves no purpose.
    clone.removeAttribute("aria-label");
    clone.removeAttribute("role");
    root.appendChild(clone);
  }

  const serializer = new XMLSerializer();
  const markup =
    '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' +
    serializer.serializeToString(root);

  const blob = new Blob([markup], { type: "image/svg+xml" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}
