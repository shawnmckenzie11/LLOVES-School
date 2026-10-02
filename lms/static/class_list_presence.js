/**
 * ClassList presence from light /state polls (MCK-119).
 *
 * A full /state carries ``class_list`` rows with a boolean ``present``.
 * The staff tab caches those rows and the ClassList trusts that boolean.
 * A light /state (the join tap's fetch and every fallback poll) leaves out
 * ``class_list`` and only sends ``attendees``. Without an overlay the
 * cached ``present: false`` from before the join keeps a new joiner
 * unticked, or hidden under Hide Absent, until a ``state_seq`` bump forces
 * a full snapshot. A join does not bump ``state_seq``.
 */

/**
 * Positive integer id, or 0.
 * @param {unknown} raw
 * @returns {number}
 */
function positiveId(raw) {
  if (raw == null || raw === "") return 0;
  const id = Number(raw);
  return Number.isFinite(id) && id > 0 ? id : 0;
}

/**
 * Roster id on a cached ``class_list`` row. These rows are roster
 * students, so ``id`` is the roster id too (``adoptClassListRows`` sets
 * both).
 * @param {any} row
 * @returns {number}
 */
function classListRosterId(row) {
  return positiveId(row?.student_id ?? row?.id);
}

/**
 * Roster id on a ``/state`` attendee, or 0 for a guest. An attendee's
 * ``id`` is the attendee row's own key, never a roster id, so only
 * ``student_id`` counts; a guest has ``student_id: null`` (MCK-119 gate
 * MED-1: falling back to ``id`` ticked the roster student with that id).
 * @param {any} person
 * @returns {number}
 */
function attendeeRosterId(person) {
  return positiveId(person?.student_id);
}

/**
 * Overlay attendee presence onto cached ClassList rows.
 *
 * ``attendees`` is the full attendee list from one /state body (present
 * and left). A roster row with an attendee who has no ``left_at`` is
 * present. A row whose attendee left is not present. A row with no
 * attendee was never joined. Guests (``student_id: null``) are not
 * ClassList rows and are skipped, whatever their attendee ``id``. Rows
 * that do not change keep their identity.
 *
 * @param {any[]} rows Cached ``class_list`` rows.
 * @param {any[]} attendees ``attendees`` from the same /state body.
 * @returns {any[]} Rows with ``present``, ``joined``, and ``left_at`` synced.
 */
export function overlayClassListPresence(rows, attendees) {
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length || !Array.isArray(attendees)) return list;
  const byId = new Map();
  for (const person of attendees) {
    const id = attendeeRosterId(person);
    if (!id) continue;
    const prior = byId.get(id);
    // A rejoin can leave an older left row; any open row wins.
    if (!prior || (prior.left_at && !person?.left_at)) byId.set(id, person);
  }
  return list.map((row) => {
    const id = classListRosterId(row);
    if (!id) return row;
    const person = byId.get(id);
    const leftAt = person ? person.left_at || null : null;
    const present = Boolean(person) && !leftAt;
    const joined = Boolean(person);
    if (
      row.present === present &&
      row.joined === joined &&
      (row.left_at || null) === leftAt
    ) {
      return row;
    }
    return { ...row, present, joined, left_at: leftAt };
  });
}
