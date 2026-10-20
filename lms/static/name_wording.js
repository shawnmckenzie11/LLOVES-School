/**
 * MCK-183 follow-up: plain courses (non-math, no pack) say "first names".
 *
 * Every roster validation message that says Name(s) or Codename(s) is
 * reworded: "Names cannot contain commas." becomes "First names cannot
 * contain commas." The word is capitalised at the start of the message
 * or of a sentence and lower case elsewhere ("Duplicate first name: Sam").
 * A word that already follows "first" or "last" is left alone. The
 * student's own name, everything after the colon in a Duplicate message,
 * is never rewritten. Callers only use this on plain courses.
 * Mirrors ``plain_name_wording`` in ``app.py``.
 *
 * @param {unknown} message
 * @returns {string}
 */
export function plainNameWording(message) {
  const text = String(message ?? "");
  const duplicate = text.match(/^(Duplicate\b[^:]*:\s*)([\s\S]*)$/);
  if (duplicate) return rewritePlainNameWords(duplicate[1]) + duplicate[2];
  return rewritePlainNameWords(text);
}

/**
 * Replace Name/Codename words, leaving a word that follows first or last.
 *
 * @param {string} text
 * @returns {string}
 */
function rewritePlainNameWords(text) {
  return text.replace(/\b(Codenames?|codenames?|Names?|names?)\b/g, (word, _g, offset) => {
    const before = text.slice(0, offset);
    if (/\b(?:first|last)\s+$/i.test(before)) return word;
    const plural = word.toLowerCase().endsWith("s");
    const plain = plural ? "first names" : "first name";
    const lead = before.trimEnd();
    if (!lead || /[.!?]$/.test(lead)) return plain[0].toUpperCase() + plain.slice(1);
    return plain;
  });
}
