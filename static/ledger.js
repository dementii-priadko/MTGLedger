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
})();
