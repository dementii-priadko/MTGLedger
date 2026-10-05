// Board, "Looking for" tab: offering a card for an any-version request. The lender picks the exact printing
// and finish they'll lend; editions come from the Scryfall API like on the loan form.
(() => {
  const API = "https://api.scryfall.com";
  const dialog = document.getElementById("offer-dialog");
  if (!dialog) return;
  const form = dialog.querySelector("form");
  const grid = document.getElementById("offer-printings");
  const status = document.getElementById("offer-status");
  const sid = document.getElementById("offer-sid");
  const qty = document.getElementById("offer-qty");
  const submit = document.getElementById("offer-submit");
  const finishes = [...form.querySelectorAll("[name=finish]")];
  let loading = null;  // card name whose editions are loading (a newer open wins)

  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  };
  const setStatus = (text) => { status.hidden = !text; status.textContent = text || ""; };

  function choose(p) {
    sid.value = p.id;
    // Only finishes this printing exists in.
    for (const r of finishes) {
      r.disabled = !p.finishes.includes(r.value);
      r.closest(".pill-option").hidden = r.disabled;
    }
    if (!form.querySelector("[name=finish]:checked:not(:disabled)")) finishes.find((r) => !r.disabled).checked = true;
    submit.disabled = false;
  }

  async function open({ action, name, max }) {
    form.action = action;
    document.getElementById("offer-card").textContent = name;
    qty.max = max;
    qty.value = max;
    document.getElementById("offer-qty-field").hidden = Number(max) <= 1;
    sid.value = "";
    submit.disabled = true;
    grid.replaceChildren();
    setStatus(t("Loading editions…"));
    dialog.showModal();

    loading = name;
    const printings = [];
    let url = `${API}/cards/search?unique=prints&order=released&dir=desc&q=${encodeURIComponent(`!"${name}" game:paper`)}`;
    try {
      while (url) {
        const res = await fetch(url);
        if (!res.ok) break;
        const page = await res.json();
        printings.push(...page.data);
        url = page.has_more ? page.next_page : null;
      }
    } catch {
      if (loading === name) setStatus(t("Couldn’t reach Scryfall. Check your connection and try again."));
      return;
    }
    if (loading !== name) return;
    if (!printings.length) return setStatus(t("No paper printings found for “{name}”.", { name }));
    setStatus(null);
    grid.replaceChildren(...printings.map((p) => {
      const input = el("input", { type: "radio", name: "offer_printing", value: p.id });
      input.addEventListener("change", () => choose(p));
      const art = el("span", { className: "printing-art" });
      const uris = p.image_uris || p.card_faces?.[0]?.image_uris || {};
      if (uris.small) art.append(el("img", { src: uris.small, alt: "", loading: "lazy" }));
      return el("label", { className: "printing", title: `${p.set_name} (${p.set.toUpperCase()} #${p.collector_number})` },
        input, art,
        el("span", { className: "printing-set", textContent: p.set_name }),
        el("span", { className: "printing-meta", textContent: `${p.set.toUpperCase()} #${p.collector_number} · ${p.released_at.slice(0, 4)}` }));
    }));
  }

  document.querySelectorAll("[data-offer-any]").forEach((btn) => btn.addEventListener("click", () => open(btn.dataset)));
  dialog.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => dialog.close()));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); }); // backdrop
  dialog.addEventListener("close", () => { loading = null; });
})();
