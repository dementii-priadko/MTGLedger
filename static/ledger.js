// Home page: lent/borrowed tabs and live search.
(() => {
  const tabs = [...document.querySelectorAll("[role=tab][data-tab]")];
  const store = {
    get: () => { try { return sessionStorage.getItem("ledger-tab"); } catch { return null; } },
    set: (v) => { try { sessionStorage.setItem("ledger-tab", v); } catch {} },
  };

  function select(name) {
    for (const tab of tabs) {
      const on = tab.dataset.tab === name;
      tab.setAttribute("aria-selected", on);
      tab.tabIndex = on ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !on;
    }
    store.set(name);
    history.replaceState(null, "", "#" + name);
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab.dataset.tab));
    tab.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      const next = tabs[(i + step + tabs.length) % tabs.length];
      next.focus();
      select(next.dataset.tab);
    });
  });

  const initial = location.hash.slice(1) || store.get();
  select(tabs.some((t) => t.dataset.tab === initial) ? initial : "lent");

  // Keep the selected tab when toggling "show returned".
  document.querySelector(".toggle")?.addEventListener("click", (e) => {
    e.currentTarget.href = e.currentTarget.href.split("#")[0] + location.hash;
  });

  const filter = document.getElementById("filter");
  filter?.addEventListener("input", () => {
    const q = filter.value.trim().toLowerCase();
    for (const panel of document.querySelectorAll(".panel")) {
      let any = false;
      for (const group of panel.querySelectorAll(".person-group")) {
        const personMatch = group.dataset.person.includes(q);
        let groupAny = false;
        for (const tile of group.querySelectorAll(".tile")) {
          const show = !q || personMatch || tile.dataset.search.includes(q);
          tile.hidden = !show;
          groupAny ||= show;
        }
        group.hidden = !groupAny;
        any ||= groupAny;
      }
      const none = panel.querySelector(".no-matches");
      if (none) none.hidden = any || !panel.querySelector(".person-group");
    }
  });

  // --- returning cards ------------------------------------------------------------

  // +/- steppers, clamped to the input's min/max.
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".stepper [data-step]");
    if (!btn) return;
    const input = btn.parentElement.querySelector("input");
    const next = (parseInt(input.value, 10) || 0) + Number(btn.dataset.step);
    input.value = Math.min(Number(input.max) || 99, Math.max(Number(input.min) || 1, next));
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });

  for (const dialog of document.querySelectorAll(".return-dialog")) {
    const boxes = [...dialog.querySelectorAll("input[name=loan_id]")];
    const selectAll = dialog.querySelector("[data-select-all]");
    const submit = dialog.querySelector("[data-submit-count]");
    const qtyFor = (box) => {
      const input = dialog.querySelector(`input[name="qty_${box.value}"]`);
      return input ? Math.min(Number(box.dataset.qty), Math.max(1, parseInt(input.value, 10) || 1)) : 1;
    };
    const update = () => {
      const total = boxes.filter((b) => b.checked).reduce((n, b) => n + qtyFor(b), 0);
      submit.disabled = total === 0;
      submit.textContent = total ? `Mark ${total} card${total === 1 ? "" : "s"} returned` : "Mark returned";
      selectAll.checked = boxes.every((b) => b.checked);
      selectAll.indeterminate = !selectAll.checked && boxes.some((b) => b.checked);
    };
    dialog.addEventListener("change", update);
    dialog.addEventListener("input", (e) => {
      // Adjusting a quantity implies that card came back.
      const m = e.target.name?.match(/^qty_(\d+)$/);
      if (m) dialog.querySelector(`input[name=loan_id][value="${m[1]}"]`).checked = true;
      update();
    });
    selectAll.addEventListener("change", () => {
      boxes.forEach((b) => { b.checked = selectAll.checked; });
      update();
    });
    dialog.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); }); // backdrop
    dialog.addEventListener("close", () => { dialog.querySelector("form").reset(); update(); });
    update();
  }
  // Loan times in the viewer's own timezone, so two loans from the same day can be told apart.
  for (const t of document.querySelectorAll("time[data-local]")) {
    const d = new Date(t.dateTime);
    if (isNaN(d)) continue;
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    const sameDay = d.toDateString() === new Date().toDateString();
    t.textContent = sameDay ? `today, ${time}` : `${d.toLocaleDateString([], { day: "numeric", month: "short" })}, ${time}`;
  }

  document.querySelectorAll("[data-open-dialog]").forEach((btn) =>
    btn.addEventListener("click", () => document.getElementById(btn.dataset.openDialog).showModal())
  );
})();
