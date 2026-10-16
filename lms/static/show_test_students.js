/**
 * Teacher switch for roster rows flagged is_test_student.
 *
 * Default is hidden. Turning it on sets a staff cookie and reloads with
 * an explicit query value so exports and the page agree.
 */
(function () {
  var box = document.getElementById("show-test-students");
  if (!box) return;
  box.addEventListener("change", function () {
    var on = box.checked;
    if (on) {
      document.cookie = "lloves_show_test_students=1; Path=/; SameSite=Lax";
    } else {
      document.cookie = "lloves_show_test_students=; Path=/; Max-Age=0; SameSite=Lax";
    }
    var url = new URL(window.location.href);
    url.searchParams.set("show_test_students", on ? "1" : "0");
    window.location.assign(url.toString());
  });
})();
