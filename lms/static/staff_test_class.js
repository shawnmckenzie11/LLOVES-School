/**
 * Confirm and delete one test live class (MCK-269).
 */
import { api } from "/static/common.js";

/**
 * Ask, then delete a test class. Live runs are refused by the server.
 * @param {number} classId
 * @param {number} sessionId
 * @returns {Promise<boolean>} True when the test class was deleted.
 */
export async function deleteTestClass(classId, sessionId) {
  const sid = Number(sessionId || 0);
  if (!sid) return false;
  const dialog = document.getElementById("delete-test-dialog");
  if (!(dialog instanceof HTMLDialogElement)) return false;
  const yes = await new Promise((resolve) => {
    const onClose = () => {
      dialog.removeEventListener("close", onClose);
      resolve(dialog.returnValue === "delete");
    };
    dialog.addEventListener("close", onClose);
    dialog.showModal();
  });
  if (!yes) return false;
  await api(
    `/api/classes/${classId}/live-sessions/${sid}/delete-test`,
    { method: "POST", body: "{}" }
  );
  return true;
}
