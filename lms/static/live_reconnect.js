/**
 * Two-stage live reconnect chrome.
 *
 * Brief drop: keep the last calm frame and show "Reconnecting…".
 * If the drop sticks: upgrade copy to "Still reconnecting — Retry"
 * and reveal Retry. Never blank the deck.
 */

export const RECONNECT_COPY_BRIEF = "Reconnecting…";
export const RECONNECT_COPY_STICKY = "Still reconnecting — Retry";
export const RECONNECT_BRIEF_MS = 2500;

/**
 * Drive one reconnect strip through brief → sticky stages.
 * Repeated ``show()`` calls do not restart the brief timer, so a 1–2s
 * poll interval can still reach the sticky string.
 *
 * @param {{
 *   root?: { hidden?: boolean, querySelector?: Function, setAttribute?: Function, removeAttribute?: Function } | null,
 *   retry?: { hidden?: boolean } | null,
 *   briefMs?: number,
 *   setTimer?: typeof setTimeout,
 *   clearTimer?: typeof clearTimeout,
 * }} opts
 * @returns {{ show: () => void, hide: () => void }}
 */
export function bindReconnectBanner(opts = {}) {
  const root = opts.root || null;
  const retry =
    opts.retry
    || root?.querySelector?.(".live-reconnect-retry")
    || null;
  const copy = root?.querySelector?.(".live-reconnect-copy") || null;
  const briefMs = Number(opts.briefMs) > 0 ? Number(opts.briefMs) : RECONNECT_BRIEF_MS;
  const setTimer = opts.setTimer || setTimeout;
  const clearTimerFn = opts.clearTimer || clearTimeout;
  let timer = null;
  let sticky = false;

  /**
   * Write the visible reconnect sentence.
   * @param {string} text
   */
  function setCopy(text) {
    if (copy) copy.textContent = text;
  }

  /**
   * Hide Retry on the brief drop; show it once the drop sticks.
   * @param {boolean} hidden
   */
  function setRetryHidden(hidden) {
    if (retry) retry.hidden = hidden;
  }

  /**
   * Mark brief vs sticky on the strip for CSS and tests.
   * @param {"brief"|"sticky"} stage
   */
  function setStage(stage) {
    if (!root?.setAttribute) return;
    root.setAttribute("data-reconnect-stage", stage);
  }

  /**
   * Brief drop copy; Retry stays hidden.
   */
  function applyBrief() {
    sticky = false;
    setCopy(RECONNECT_COPY_BRIEF);
    setRetryHidden(true);
    setStage("brief");
  }

  /**
   * Sticky-stage copy once the drop has lasted past ``briefMs``.
   */
  function applySticky() {
    sticky = true;
    setCopy(RECONNECT_COPY_STICKY);
    setRetryHidden(false);
    setStage("sticky");
  }

  /**
   * Cancel a pending brief → sticky upgrade.
   */
  function clearPending() {
    if (timer != null) {
      clearTimerFn(timer);
      timer = null;
    }
  }

  /**
   * Reveal the strip. First show is brief; later shows keep the same stage.
   */
  function show() {
    if (root) root.hidden = false;
    if (sticky) {
      applySticky();
      return;
    }
    if (timer != null) return;
    applyBrief();
    timer = setTimer(() => {
      timer = null;
      if (root && root.hidden) return;
      applySticky();
    }, briefMs);
  }

  /**
   * Hide the strip and reset to the brief sentence for the next drop.
   */
  function hide() {
    if (root) root.hidden = true;
    clearPending();
    applyBrief();
  }

  applyBrief();
  return { show, hide };
}
