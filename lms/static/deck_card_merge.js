/**
 * MCK-443: keep a removed deck card gone after (Re)move.
 *
 * The staff panel merges deck metadata with server/live cards. A card the
 * teacher just removed is no longer in the metadata, but the live-session
 * cards (or the previous local list) can still carry it, so the merge used
 * to paint it back until a page refresh.
 */

/**
 * Normalize a playlist id: drop ``stage:page:order:`` and fold ``-``/``_``.
 * @param {unknown} raw
 * @returns {string}
 */
export function deckItemAlias(raw) {
  const token = String(raw || "").trim();
  const peeled = token.replace(
    /^(?:join|teams|meet|round_3|round|play|summary):\d+:\d+:/i,
    ""
  );
  return peeled.toLowerCase().replace(/-/g, "_");
}

/**
 * Drop incoming cards the teacher removed, unless the deck lists them again.
 * @param {any[]} incoming Server or previous local cards.
 * @param {Iterable<string>} removedIds Ids removed this page load.
 * @param {Iterable<string>} deckIds Ids on the current merged deck.
 * @returns {any[]}
 */
export function withoutRemovedCards(incoming, removedIds, deckIds) {
  const removed = new Set([...removedIds].map(deckItemAlias));
  if (!removed.size) return Array.isArray(incoming) ? incoming : [];
  const onDeck = new Set([...deckIds].map(deckItemAlias));
  return (Array.isArray(incoming) ? incoming : []).filter((card) => {
    const alias = deckItemAlias(card?.id || card?.item_id);
    return !removed.has(alias) || onDeck.has(alias);
  });
}
