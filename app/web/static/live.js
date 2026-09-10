/* Progressive enhancement for the cart's per-line edits.
 *
 * The no-JS path already works: each form posts and the server redirects back to
 * #line-<id>. That still costs a full document load, and a reload on a phone is
 * a visible flash plus a scroll restore you can feel. This layer keeps the same
 * forms and the same routes, but posts them in the background and swaps three
 * regions of the page in place, so nothing navigates and nothing scrolls.
 *
 * Deliberately no fragment endpoint: fetch follows the existing 303 and we parse
 * the same HTML the browser would have rendered. One render path, so the live
 * update can never drift from the reload it replaces.
 */
(function () {
  "use strict";

  // While a plan generates, the page carries a meta-refresh. Don't fight it.
  if (document.querySelector('meta[http-equiv="refresh"]')) return;

  var REGIONS = ["cart-body", "budget-region", "actions-region"];
  var root = document.getElementById("cart-body");
  if (!root || !window.fetch || !window.DOMParser) return;

  // Only now do the confirm buttons become redundant: CSS hides .js-optional
  // off this class, so a browser that never runs this file keeps them.
  document.documentElement.classList.add("js-live");

  // One request at a time. Two POSTs racing could be applied out of order and
  // leave the page showing a subtotal that never existed.
  var chain = Promise.resolve();

  function rowOf(el) {
    return el.closest ? el.closest("tr[id]") : null;
  }

  /* Flip the toggle before the round trip so the tap feels answered. The row's
   * height is identical in both states, so this can only ever change colour. */
  function optimisticToggle(form) {
    var button = form.querySelector(".toggle");
    var hidden = form.querySelector('input[name="included"]');
    var row = rowOf(form);
    if (!button || !hidden || !row) return;
    var turningOn = hidden.value === "on";
    button.dataset.state = turningOn ? "in" : "out";
    button.setAttribute("aria-pressed", turningOn ? "true" : "false");
    row.classList.toggle("dropped", !turningOn);
  }

  function swapIn(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    // If the response isn't a dashboard we recognise, let the browser have it.
    if (!doc.getElementById("cart-body")) return false;
    for (var i = 0; i < REGIONS.length; i++) {
      var id = REGIONS[i];
      var next = doc.getElementById(id);
      var current = document.getElementById(id);
      if (next && current) current.innerHTML = next.innerHTML;
    }
    return true;
  }

  function send(form) {
    var row = rowOf(form);
    var rowId = row ? row.id : null;
    // Which control the person was on, so focus survives the swap. Keyboard
    // users tab down this list; losing focus to <body> would send them back to
    // the top of the document on the next Tab.
    var focusSel = null;
    var active = document.activeElement;
    if (active && row && row.contains(active)) {
      if (active.classList.contains("toggle")) focusSel = ".toggle";
      else if (active.tagName === "SELECT") focusSel = "select";
      else if (active.tagName === "INPUT") focusSel = 'input[type="number"]';
    }

    if (row) row.classList.add("is-busy");

    var body;
    try {
      body = new FormData(form, form.__submitter || undefined);
    } catch (e) {
      body = new FormData(form); // FormData(form, submitter) is newer than fetch
    }

    return fetch(form.action, {
      method: "POST",
      body: body,
      redirect: "follow",
      headers: { "X-Requested-With": "fetch" },
      credentials: "same-origin",
    })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.text();
      })
      .then(function (html) {
        if (!swapIn(html)) {
          form.submit();
          return;
        }
        // getElementById, not a built selector: no escaping to get wrong, and
        // preventScroll so restoring focus cannot itself move the page.
        var rowAgain = rowId ? document.getElementById(rowId) : null;
        if (rowAgain && focusSel) {
          var again = rowAgain.querySelector(focusSel);
          if (again) again.focus({ preventScroll: true });
        }
      })
      .catch(function () {
        // Offline, server restarted, anything: fall back to the real submit so
        // the edit still lands and the person sees a normal page.
        form.submit();
      })
      .then(function () {
        var still = rowId ? document.getElementById(rowId) : null;
        if (still) still.classList.remove("is-busy");
      });
  }

  function queue(form) {
    chain = chain.then(function () {
      return send(form);
    });
  }

  /* Picking a different product, or changing a quantity, is the whole intent —
   * there is nothing left to confirm. Submitting on change is what lets the
   * .js-optional buttons go away. */
  document.addEventListener("change", function (ev) {
    var field = ev.target;
    if (!field.matches || !field.matches("form[data-live] select, form[data-live] input[type=number]")) return;
    var form = field.form;
    if (!form) return;
    form.__submitter = null;
    queue(form);
  });

  document.addEventListener("submit", function (ev) {
    var form = ev.target;
    if (!form.matches || !form.matches("form[data-live]")) return;
    ev.preventDefault();
    form.__submitter = ev.submitter || null;
    if (form.action.indexOf("/toggle") !== -1) optimisticToggle(form);
    queue(form);
  });
})();
