// New-loan form: card search, edition grid, finish/quantity, live preview.
// Card data comes from the Scryfall API (https://scryfall.com/docs/api).
(() => {
  const API = "https://api.scryfall.com";
  const FINISH_LABEL = { nonfoil: "Non-foil", foil: "Foil", etched: "Etched foil" };
  const USERNAME_RE = /^[A-Za-z0-9_]{3,32}$/;

  const $ = (id) => document.getElementById(id);
  const form = $("loan-form");
  const other = $("other");
  const nameInput = $("card-name");
  const suggestions = $("card-suggestions");
  const printingsWrap = $("printings-wrap");
  const printingsEl = $("printings");
  const printingFilter = $("printing-filter");
  const printingCount = $("printing-count");
  const printingsStatus = $("printings-status");
  const quantity = $("quantity");
  const preview = $("preview");
  const previewArt = $("preview-art");
  const previewEmpty = $("preview-empty");
  const summary = $("summary");

  let printings = [];
  let selected = null;
  let loadedName = null;
  let activeIndex = -1;
  let autocompleteTimer;

  const image = (card, size) => {
    const uris = card.image_uris || (card.card_faces && card.card_faces[0].image_uris) || {};
    return uris[size] || uris.normal;
  };
  const direction = () => form.querySelector("[name=direction]:checked").value;
  const finish = () => form.querySelector("[name=finish]:checked")?.value;

  // --- who --------------------------------------------------------------------

  function syncDirection() {
    $("other-label").textContent = direction() === "lent" ? "Lent to" : "Borrowed from";
    updateSummary();
  }
  form.querySelectorAll("[name=direction]").forEach((r) => r.addEventListener("change", syncDirection));

  other.addEventListener("input", () => {
    other.value = other.value.replace(/^@+/, "");
    clearError(other);
    highlightChip();
    updateSummary();
  });
  document.querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      other.value = chip.dataset.username;
      clearError(other);
      highlightChip();
      updateSummary();
    })
  );
  function highlightChip() {
    document.querySelectorAll(".chip").forEach((c) =>
      c.classList.toggle("active", c.dataset.username.toLowerCase() === other.value.toLowerCase())
    );
  }

  // --- card search --------------------------------------------------------------

  nameInput.addEventListener("input", () => {
    clearError(nameInput);
    clearTimeout(autocompleteTimer);
    const q = nameInput.value.trim();
    if (q.length < 2) return closeSuggestions();
    autocompleteTimer = setTimeout(async () => {
      try {
        const res = await fetch(`${API}/cards/autocomplete?q=${encodeURIComponent(q)}`);
        if (!res.ok) return;
        const { data } = await res.json();
        if (nameInput.value.trim() === q) showSuggestions(data.slice(0, 10));
      } catch {}
    }, 180);
  });

  function showSuggestions(names) {
    activeIndex = -1;
    suggestions.replaceChildren(
      ...names.map((name, i) => {
        const li = document.createElement("li");
        li.role = "option";
        li.id = `sugg-${i}`;
        li.textContent = name;
        li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(name); });
        return li;
      })
    );
    suggestions.hidden = !names.length;
    nameInput.setAttribute("aria-expanded", names.length > 0);
  }

  function closeSuggestions() {
    suggestions.hidden = true;
    nameInput.setAttribute("aria-expanded", "false");
    nameInput.removeAttribute("aria-activedescendant");
  }

  function setActive(i) {
    const items = [...suggestions.children];
    if (!items.length) return;
    activeIndex = (i + items.length) % items.length;
    items.forEach((li, j) => li.setAttribute("aria-selected", j === activeIndex));
    nameInput.setAttribute("aria-activedescendant", items[activeIndex].id);
    items[activeIndex].scrollIntoView({ block: "nearest" });
  }

  nameInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setActive(activeIndex + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive(activeIndex - 1); }
    else if (e.key === "Escape") closeSuggestions();
    else if (e.key === "Enter") {
      e.preventDefault();
      const items = [...suggestions.children];
      if (!suggestions.hidden && items.length) choose(items[Math.max(activeIndex, 0)].textContent);
      else if (nameInput.value.trim()) choose(nameInput.value.trim());
    }
  });
  nameInput.addEventListener("blur", () => setTimeout(closeSuggestions, 100));

  function choose(name) {
    nameInput.value = name;
    closeSuggestions();
    loadPrintings(name);
  }

  // --- editions ---------------------------------------------------------------------

  async function loadPrintings(name) {
    if (name === loadedName) return;
    loadedName = name;
    selected = null;
    printings = [];
    printingsWrap.hidden = true;
    setStatus("Loading editions…");
    updatePreview();

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
      if (name === loadedName) setStatus("Couldn’t reach Scryfall. Check your connection and try again.");
      loadedName = null;
      return;
    }
    if (name !== loadedName) return; // a newer search started meanwhile
    if (!printings.length) return setStatus(`No paper printings found for “${name}”.`);

    setStatus(null);
    renderPrintings();
  }

  function renderPrintings() {
    printingCount.textContent = `· ${printings.length} printing${printings.length === 1 ? "" : "s"}`;
    printingFilter.hidden = printings.length <= 8;
    printingFilter.value = "";
    printingsEl.replaceChildren(
      ...printings.map((p, i) => {
        const label = document.createElement("label");
        label.className = "printing";
        label.dataset.search = `${p.set_name} ${p.set} ${p.released_at.slice(0, 4)} ${p.collector_number}`.toLowerCase();
        label.title = `${p.set_name} (${p.set.toUpperCase()} #${p.collector_number})`;
        label.innerHTML = `
          <input type="radio" name="scryfall_id">
          <span class="printing-art"><img loading="lazy" alt=""></span>
          <span class="printing-set"></span>
          <span class="printing-meta"></span>`;
        const input = label.querySelector("input");
        input.value = p.id;
        input.addEventListener("change", () => selectPrinting(i));
        const img = label.querySelector("img");
        const src = image(p, "small");
        if (src) img.src = src; else img.remove();
        label.querySelector(".printing-set").textContent = p.set_name;
        label.querySelector(".printing-meta").textContent =
          `${p.set.toUpperCase()} #${p.collector_number} · ${p.released_at.slice(0, 4)}${p.finishes.includes("nonfoil") ? "" : " · foil only"}`;
        return label;
      })
    );
    printingsWrap.hidden = false;
    printingsEl.querySelector("input").checked = true;
    selectPrinting(0);
  }

  printingFilter.addEventListener("input", () => {
    const q = printingFilter.value.trim().toLowerCase();
    for (const el of printingsEl.children) el.hidden = q && !el.dataset.search.includes(q);
  });

  function selectPrinting(i) {
    selected = printings[i];
    clearError(nameInput);
    // Only offer finishes this printing actually exists in.
    const radios = [...form.querySelectorAll("[name=finish]")];
    for (const r of radios) {
      const ok = selected.finishes.includes(r.value);
      r.disabled = !ok;
      r.closest(".pill-option").hidden = !ok;
    }
    if (!finish() || form.querySelector("[name=finish]:checked").disabled) {
      radios.find((r) => !r.disabled).checked = true;
    }
    updatePreview();
  }

  form.querySelectorAll("[name=finish]").forEach((r) => r.addEventListener("change", updatePreview));

  // --- quantity ---------------------------------------------------------------------

  document.querySelectorAll(".stepper [data-step]").forEach((b) =>
    b.addEventListener("click", () => {
      quantity.value = Math.min(99, Math.max(1, (parseInt(quantity.value, 10) || 1) + Number(b.dataset.step)));
      updateSummary();
    })
  );
  quantity.addEventListener("input", updateSummary);

  // --- preview ----------------------------------------------------------------------

  function updatePreview() {
    previewArt.className = `preview-card art finish-${selected ? finish() : "nonfoil"}`;
    if (selected) {
      preview.src = image(selected, "normal");
      preview.alt = selected.name;
    }
    preview.hidden = !selected;
    previewEmpty.hidden = !!selected;
    updateSummary();
  }

  function updateSummary() {
    if (!selected) { summary.innerHTML = "&nbsp;"; return; }
    const qty = parseInt(quantity.value, 10) || 1;
    const who = other.value ? `@${other.value}` : "someone";
    const what = `${qty > 1 ? qty + "× " : ""}${selected.name}`;
    const detail = `${FINISH_LABEL[finish()]} · ${selected.set.toUpperCase()} #${selected.collector_number}`;
    summary.replaceChildren(
      direction() === "lent" ? "You lent " : "You borrowed ",
      Object.assign(document.createElement("strong"), { textContent: what }),
      direction() === "lent" ? " to " : " from ",
      Object.assign(document.createElement("strong"), { textContent: who }),
      Object.assign(document.createElement("span"), { className: "muted", textContent: ` (${detail})` })
    );
  }

  // --- validation -------------------------------------------------------------------

  function setStatus(text) {
    printingsStatus.hidden = !text;
    printingsStatus.textContent = text || "";
  }

  function showError(input, message) {
    const field = input.closest(".field");
    field.classList.add("invalid");
    let msg = field.querySelector(".field-error");
    if (!msg) {
      msg = Object.assign(document.createElement("p"), { className: "field-error" });
      field.append(msg);
    }
    msg.textContent = message;
  }
  function clearError(input) {
    const field = input.closest(".field");
    field.classList.remove("invalid");
    field.querySelector(".field-error")?.remove();
  }

  form.addEventListener("submit", (e) => {
    let firstBad = null;
    if (!USERNAME_RE.test(other.value)) {
      showError(other, "Enter their Telegram username (letters, numbers, underscores).");
      firstBad ||= other;
    }
    if (!selected) {
      showError(nameInput, "Search for a card and pick an edition.");
      firstBad ||= nameInput;
    }
    if (firstBad) {
      e.preventDefault();
      firstBad.focus();
      return;
    }
    $("submit").disabled = true;
    $("submit").textContent = "Saving…";
  });

  // Re-enable the button if the page is restored from the back/forward cache.
  window.addEventListener("pageshow", () => {
    $("submit").disabled = false;
    $("submit").textContent = "Record loan";
  });

  syncDirection();
  highlightChip();
})();
