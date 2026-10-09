/**
 * MCK-183 follow-up: plain courses (non-math, no pack) say "first names".
 *
 * Every roster validation message that says Codename(s) is reworded:
 * "Codenames cannot contain commas." becomes "First names cannot contain
 * commas." The word is capitalised at the start of the message or of a
 * sentence and lower case elsewhere ("Duplicate first name: Sam"). Math
 * courses keep Codename, so callers only use this on plain courses.
 * Mirrors ``plain_name_wording`` in ``app.py``.
 *
 * @param {unknown} message
 * @returns {string}
 */
export function plainNameWording(message) {
  const text = String(message ?? "");
  return text.replace(/\b(Codenames?|codenames?)\b/g, (word, _g, offset) => {
    const plural = word.toLowerCase().endsWith("s");
    const plain = plural ? "first names" : "first name";
    const before = text.slice(0, offset).trimEnd();
    if (!before || /[.!?]$/.test(before)) return plain[0].toUpperCase() + plain.slice(1);
    return plain;
  });
}
