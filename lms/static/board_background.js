/**
 * Paint a question image under whiteboard ink on canvases and tiles.
 */

/** @type {Map<string, Promise<HTMLImageElement | null>>} */
const loaded = new Map();

/**
 * CSS background for a live board canvas (student or teacher panel).
 *
 * @param {HTMLCanvasElement} canvas Target canvas.
 * @param {string} imageUrl Public image URL, or blank to clear.
 */
export function applyCanvasBoardBackground(canvas, imageUrl) {
  if (!(canvas instanceof HTMLCanvasElement)) return;
  const url = String(imageUrl || "").trim();
  if (!url) {
    canvas.style.backgroundImage = "";
    return;
  }
  const safe = url.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  canvas.style.backgroundImage = `url("${safe}")`;
  canvas.style.backgroundSize = "contain";
  canvas.style.backgroundRepeat = "no-repeat";
  canvas.style.backgroundPosition = "center";
}

/**
 * Load one board background image with a simple in-memory cache.
 *
 * @param {string} imageUrl
 * @returns {Promise<HTMLImageElement | null>}
 */
export function loadBoardBackgroundImage(imageUrl) {
  const url = String(imageUrl || "").trim();
  if (!url) return Promise.resolve(null);
  const existing = loaded.get(url);
  if (existing) return existing;
  const pending = new Promise((resolve) => {
    const img = new Image();
    img.decoding = "async";
    img.onload = () => resolve(img);
    img.onerror = () => resolve(null);
    img.src = url;
  });
  loaded.set(url, pending);
  return pending;
}

/**
 * Draw a fitted background bitmap, then leave the context ready for ink.
 *
 * @param {CanvasRenderingContext2D} ctx
 * @param {number} width Pixel width of the canvas backing store.
 * @param {number} height Pixel height of the canvas backing store.
 * @param {HTMLImageElement | null} image Loaded background image.
 */
export function drawFittedBoardBackground(ctx, width, height, image) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  if (!image || !image.naturalWidth || !image.naturalHeight) return;
  const scale = Math.min(width / image.naturalWidth, height / image.naturalHeight);
  const drawW = image.naturalWidth * scale;
  const drawH = image.naturalHeight * scale;
  const x = (width - drawW) / 2;
  const y = (height - drawH) / 2;
  ctx.drawImage(image, x, y, drawW, drawH);
}

/**
 * True when a question payload uses its image only as the board background.
 *
 * @param {any} content Student- or staff-facing question payload.
 * @returns {boolean}
 */
export function questionImageIsBoardBackground(content) {
  const body = content && typeof content === "object" ? content : {};
  const mode = String(body.answer_with || "").toLowerCase();
  if (mode !== "board" && mode !== "both") return false;
  if (!String(body.image_url || "").trim()) return false;
  return body.image_as_board_background !== false;
}