// Admin panel: filter table rows by their data-search text; placeholder dialogs.
(() => {
  document.querySelectorAll("[data-open-dialog]").forEach((btn) =>
    btn.addEventListener("click", () => document.getElementById(btn.dataset.openDialog).showModal())
  );
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); }); // backdrop
  }

  for (const input of document.querySelectorAll("[data-table-filter]")) {
    const rows = [...document.getElementById(input.dataset.tableFilter).tBodies[0].rows];
    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      for (const row of rows) row.hidden = q && !row.dataset.search.includes(q);
    });
  }
})();
