// New-loan form: card search, edition grid, finish/quantity, and a list of cards in this loan.
// Card data comes from the Scryfall API (https://scryfall.com/docs/api).
(() => {
  const API = "https://api.scryfall.com";
  const FINISH_LABEL = { nonfoil: "Non-foil", foil: "Foil", etched: "Etched foil" };
  const USERNAME_RE = /^[A-Za-z0-9_]{3,32}$/;
  const MAX_LINES = 50;

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
  const pick = $("pick");
  const pickArt = $("pick-art");
  const pickImg = $("pick-img");
  const pickQty = $("pick-qty");
  const cartList = $("cart-list");
  const cartError = $("cart-error");

  let printings = [];
  let selected = null;     // printing currently being picked (not yet in the cart)
  let selectedAny = false; // "Any version" picked (asking to borrow only): `selected` just stands in for the card
  let loadedName = null;
  let activeIndex = -1;
  let autocompleteTimer;
  let cart = [];           // [{id, name, set, number, image, finish, qty}]
  let editing = null;      // cart line whose edition is being changed: replaced when the pick is added
  let preferred = null;    // {id, finish, any} to preselect once that card's editions load

  const image = (card, size) => {
    const uris = card.image_uris || (card.card_faces && card.card_faces[0].image_uris) || {};
    return uris[size] || uris.normal;
  };
  const direction = () => form.querySelector("[name=direction]:checked").value;
  // Posting to the board (Missing / Looking for) instead of recording a loan: no "who".
  const board = ["missing", "wanted"].includes(direction()) ? direction() : null;
  const pickFinish = () => form.querySelector("[name=pick_finish]:checked")?.value;
  const el = (tag, props = {}, ...children) => {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
  };

  // --- who --------------------------------------------------------------------

  function syncWho() {
    if (board) return;
    $("other-label").textContent = direction() === "lent" ? t("Lent to") : t("Borrowed from");
    const who = USERNAME_RE.test(other.value) ? `@${other.value}` : "…";
    $("cart-who").textContent = direction() === "lent" ? t("Lending to {who}", { who }) : t("Borrowing from {who}", { who });
  }
  form.querySelectorAll("[name=direction]").forEach((r) => r.addEventListener("change", syncWho));

  other?.addEventListener("input", () => {
    other.value = other.value.replace(/^@+/, "");
    clearError(other);
    highlightChip();
    syncWho();
  });
  document.querySelectorAll(".chip").forEach((chip) =>
    chip.addEventListener("click", () => {
      other.value = chip.dataset.username;
      clearError(other);
      highlightChip();
      syncWho();
    })
  );
  function highlightChip() {
    if (board) return;
    document.querySelectorAll(".chip").forEach((c) =>
      c.classList.toggle("active", c.dataset.username.toLowerCase() === other.value.toLowerCase())
    );
  }

  // --- card search --------------------------------------------------------------

  nameInput.addEventListener("input", () => {
    editing = null;
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
        const li = el("li", { role: "option", id: `sugg-${i}`, textContent: name });
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
    printings = [];
    setSelected(null);
    printingsWrap.hidden = true;
    setStatus(t("Loading editions…"));

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
      if (name === loadedName) setStatus(t("Couldn’t reach Scryfall. Check your connection and try again."));
      loadedName = null;
      return;
    }
    if (name !== loadedName) return; // a newer search started meanwhile
    if (!printings.length) return setStatus(t("No paper printings found for “{name}”.", { name }));

    setStatus(null);
    renderPrintings();
  }

  function renderPrintings() {
    printingCount.textContent = `· ${tn("{n} printing", "{n} printings", printings.length)}`;
    printingFilter.hidden = printings.length <= 8;
    printingFilter.value = "";
    printingsEl.replaceChildren(
      ...printings.map((p, i) => {
        const label = el("label", {
          className: "printing",
          title: `${p.set_name} (${p.set.toUpperCase()} #${p.collector_number})`,
        });
        label.dataset.search = `${p.set_name} ${p.set} ${p.released_at.slice(0, 4)} ${p.collector_number}`.toLowerCase();
        const input = el("input", { type: "radio", name: "pick_printing", value: p.id });
        input.addEventListener("change", () => setSelected(printings[i]));
        const art = el("span", { className: "printing-art" });
        const src = image(p, "small");
        if (src) art.append(el("img", { src, alt: "", loading: "lazy" }));
        label.append(
          input,
          art,
          el("span", { className: "printing-set", textContent: p.set_name }),
          el("span", {
            className: "printing-meta",
            textContent: `${p.set.toUpperCase()} #${p.collector_number} · ${p.released_at.slice(0, 4)}` +
              (p.finishes.includes("nonfoil") ? "" : ` · ${t("foil only")}`),
          })
        );
        return label;
      })
    );
    // Asking to borrow: "Any version" comes first (and is the default); the lender picks when offering.
    if (board === "wanted") {
      const input = el("input", { type: "radio", name: "pick_printing", value: "any" });
      input.addEventListener("change", () => setSelected(printings[0], true));
      const art = el("span", { className: "printing-art printing-any" }, el("span", { textContent: "?" }));
      const label = el("label", { className: "printing" }, input, art,
        el("span", { className: "printing-set", textContent: t("Any version") }),
        el("span", { className: "printing-meta", textContent: t("The lender chooses") }));
      label.dataset.search = t("Any version").toLowerCase();
      printingsEl.prepend(label);
    }
    printingsWrap.hidden = false;
    const inputs = printingsEl.querySelectorAll("input");
    if (board === "wanted" && (!preferred || preferred.any)) {
      inputs[0].checked = true;
      setSelected(printings[0], true);
    } else {
      const start = Math.max(0, printings.findIndex((p) => p.id === preferred?.id));
      inputs[start + (board === "wanted" ? 1 : 0)].checked = true;
      setSelected(printings[start]);
      const finish = form.querySelector(`[name=pick_finish][value="${preferred?.finish}"]`);
      if (finish && !finish.disabled) { finish.checked = true; syncPickArt(); }
    }
    preferred = null;
  }

  printingFilter.addEventListener("input", () => {
    const q = printingFilter.value.trim().toLowerCase();
    for (const node of printingsEl.children) node.hidden = q && !node.dataset.search.includes(q);
  });

  function setSelected(p, any = false) {
    selected = p;
    selectedAny = any;
    pick.hidden = !p;
    if (!p) return;
    $("pick-finish-field").hidden = any;  // any version means any finish too
    // Only offer finishes this printing actually exists in.
    const radios = [...form.querySelectorAll("[name=pick_finish]")];
    for (const r of radios) {
      const ok = p.finishes.includes(r.value);
      r.disabled = !ok;
      r.closest(".pill-option").hidden = !ok;
    }
    if (!pickFinish() || form.querySelector("[name=pick_finish]:checked").disabled) {
      radios.find((r) => !r.disabled).checked = true;
    }
    $("pick-name").textContent = `${p.name} · ${any ? t("Any version") : `${p.set.toUpperCase()} #${p.collector_number}`}`;
    pickImg.src = image(p, "normal");
    pickImg.alt = p.name;
    syncPickArt();
  }

  function syncPickArt() {
    pickArt.className = `pick-art art finish-${(!selectedAny && pickFinish()) || "nonfoil"}`;
  }
  form.querySelectorAll("[name=pick_finish]").forEach((r) => r.addEventListener("change", syncPickArt));

  const clampQty = (n) => Math.min(99, Math.max(1, n || 1));
  document.querySelectorAll(".stepper [data-step]").forEach((b) =>
    b.addEventListener("click", () => {
      pickQty.value = clampQty((parseInt(pickQty.value, 10) || 1) + Number(b.dataset.step));
    })
  );

  function resetPicker() {
    editing = null;
    loadedName = null;
    printings = [];
    setSelected(null);
    printingsWrap.hidden = true;
    printingsEl.replaceChildren();
    setStatus(null);
    nameInput.value = "";
    pickQty.value = 1;
    form.querySelector("[name=pick_finish][value=nonfoil]").checked = true;
  }

  // --- the loan's card list -------------------------------------------------------

  // `any`: "Any version" request; the printing only stands in for the card (its finish just has to exist).
  const cartLine = (card, finish, qty, any = false) => ({
    any,
    id: card.id,
    name: card.name,
    set: card.set.toUpperCase(),
    number: card.collector_number,
    image: image(card, "small"),
    finish: any ? (card.finishes.includes("nonfoil") ? "nonfoil" : card.finishes[0]) : finish,
    qty: clampQty(qty),
  });
  const tooManyLines = () =>
    tn("A loan can have at most {n} different card.", "A loan can have at most {n} different cards.", MAX_LINES);

  // Put a line in the cart, merging with the same printing+finish; returns the cart line, or null when full.
  function addLine(line) {
    const same = cart.find((c) => line.any ? c.any && c.name === line.name
                                           : !c.any && c.id === line.id && c.finish === line.finish);
    if (same) {
      same.qty = clampQty(same.qty + line.qty);
      return same;
    }
    if (cart.length >= MAX_LINES) return null;
    cart.push(line);
    return line;
  }

  function addSelected() {
    if (!selected) return false;
    // Changing an edition: the new pick replaces the old line.
    if (editing && cart.includes(editing)) cart.splice(cart.indexOf(editing), 1);
    const added = addLine(cartLine(selected, pickFinish(), parseInt(pickQty.value, 10), selectedAny));
    if (!added) {
      showCartError(tooManyLines());
      return false;
    }
    renderCart(added);
    resetPicker();
    return true;
  }

  // Load a cart line back into the picker to choose another edition or finish.
  function editLine(line) {
    resetPicker();
    editing = line;
    preferred = { id: line.id, finish: line.finish, any: line.any };
    nameInput.value = line.name;
    pickQty.value = line.qty;
    loadPrintings(line.name);
    $("picker").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  // --- pasting a decklist -------------------------------------------------------------
  // "4 Lightning Bolt", optionally "(SET) 123" for an exact printing and *F* / *E* for foil / etched
  // (Arena, MTGO and Moxfield exports). Lines without a count are headers ("Deck", "Sideboard", "Name …").
  // Sideboard cards count like the rest: you lend or borrow the whole thing.
  function parseDecklist(text) {
    const lines = new Map();
    for (let raw of text.split(/\r?\n/)) {
      raw = raw.trim().replace(/^sb:\s*/i, "");  // MTGO marks sideboard lines with "SB:"
      const m = raw.match(/^(\d+)\s*x?\s+(.+)$/i);
      if (!m) continue;
      const qty = parseInt(m[1], 10);
      if (!qty) continue;
      let name = m[2], finish = null, set = null, number = null;
      const f = name.match(/\s+\*([FE])\*$/i);
      if (f) { finish = f[1].toUpperCase() === "F" ? "foil" : "etched"; name = name.slice(0, f.index); }
      const s = name.match(/\s+[(\[]([A-Za-z0-9]{2,6})[)\]](?:\s+(\S+))?$/);
      if (s) { set = s[1].toLowerCase(); number = s[2] || null; name = name.slice(0, s.index); }
      name = name.trim();
      const key = [name.toLowerCase(), set, number, finish].join("|");
      const line = lines.get(key) || { name, set, number, finish, qty: 0 };
      line.qty += qty;
      lines.set(key, line);
    }
    return [...lines.values()];
  }

  // Resolve parsed lines with /cards/collection (75 per request); returns a card or null per line.
  async function lookUp(entries) {
    const identifier = (e) =>
      e.set && e.number ? { set: e.set, collector_number: e.number } : e.set ? { name: e.name, set: e.set } : { name: e.name };
    const found = [];
    for (let i = 0; i < entries.length; i += 75) {
      const res = await fetch(`${API}/cards/collection`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifiers: entries.slice(i, i + 75).map(identifier) }),
      });
      if (!res.ok) throw new Error(`Scryfall ${res.status}`);
      found.push(...(await res.json()).data);
    }
    const lower = (s) => s.toLowerCase();
    const matches = (e, c) =>
      e.set && e.number
        ? c.set === e.set && lower(c.collector_number) === lower(e.number)
        : (!e.set || c.set === e.set) && [c.name, ...c.name.split(" // ")].some((n) => lower(n) === lower(e.name));
    const cards = entries.map((e) => found.find((c) => matches(e, c)) || null);
    // A name can resolve to a digital-only card; fall back to its newest paper printing.
    for (const [i, c] of cards.entries()) {
      if (!c || c.games.includes("paper")) continue;
      const q = encodeURIComponent(`!"${c.name}" game:paper`);
      const res = await fetch(`${API}/cards/search?unique=prints&order=released&dir=desc&q=${q}`);
      cards[i] = res.ok ? (await res.json()).data[0] : null;
    }
    return cards;
  }

  const importText = $("import-text");
  const importButton = $("import-add");
  function importStatus(text, isError) {
    const status = $("import-status");
    status.hidden = !text;
    status.textContent = text || "";
    status.classList.toggle("field-error", !!isError);
  }

  importButton.addEventListener("click", async () => {
    const entries = parseDecklist(importText.value);
    if (!entries.length) {
      return importStatus(t("No cards found in that list. Each line should start with a number, like “4 Lightning Bolt”."), true);
    }
    importButton.disabled = true;
    importStatus(t("Looking up cards…"));
    let cards;
    try {
      cards = await lookUp(entries);
    } catch {
      importButton.disabled = false;
      return importStatus(t("Couldn’t reach Scryfall. Check your connection and try again."), true);
    }
    importButton.disabled = false;
    const notFound = [];
    let added = 0, full = false;
    entries.forEach((e, i) => {
      const card = cards[i];
      if (!card) return notFound.push(e);
      const finish = e.finish && card.finishes.includes(e.finish) ? e.finish
        : card.finishes.includes("nonfoil") ? "nonfoil" : card.finishes[0];
      // Asking to borrow without a set code: any version will do.
      const any = board === "wanted" && !e.set && !e.finish;
      if (addLine(cartLine(card, finish, e.qty, any))) added += e.qty;
      else full = true;
    });
    renderCart();
    const messages = [];
    if (added) messages.push(tn("Added {n} card.", "Added {n} cards.", added), t("Tap a card in the list to change its edition."));
    if (notFound.length) messages.push(t("Not found on Scryfall: {names}", { names: notFound.map((e) => e.name).join(", ") }));
    if (full) messages.push(tooManyLines());
    importStatus(messages.join(" "), notFound.length > 0 || full);
    // Leave only what's left to fix, so pressing the button again doesn't add the rest twice.
    if (!full) {
      importText.value = notFound.map((e) =>
        `${e.qty} ${e.name}` + (e.set ? ` (${e.set.toUpperCase()})${e.number ? " " + e.number : ""}` : "") +
        (e.finish ? ` *${e.finish === "foil" ? "F" : "E"}*` : "")).join("\n");
    }
  });

  $("add-card").addEventListener("click", () => {
    if (addSelected()) nameInput.focus();
  });

  function renderCart(highlight) {
    cartError.hidden = true;
    cartList.replaceChildren(
      ...cart.map((line, i) => {
        const minus = el("button", { type: "button", className: "cart-btn", textContent: "−", ariaLabel: t("One less {name}", { name: line.name }) });
        const plus = el("button", { type: "button", className: "cart-btn", textContent: "+", ariaLabel: t("One more {name}", { name: line.name }) });
        const remove = el("button", { type: "button", className: "cart-btn cart-remove", ariaLabel: t("Remove {name}", { name: line.name }), title: t("Remove") });
        remove.innerHTML = '<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15"/></svg>';
        minus.disabled = line.qty <= 1;
        minus.addEventListener("click", () => { line.qty = clampQty(line.qty - 1); renderCart(); });
        plus.addEventListener("click", () => { line.qty = clampQty(line.qty + 1); renderCart(); });
        remove.addEventListener("click", () => { cart.splice(i, 1); renderCart(); });

        const thumb = el("span", { className: `cart-thumb art finish-${line.finish}` });
        if (line.image) thumb.append(el("img", { src: line.image, alt: "" }));
        const detail = line.any ? t("Any version")
          : `${line.set} #${line.number}` + (line.finish === "nonfoil" ? "" : ` · ${t(FINISH_LABEL[line.finish])}`);
        // Tapping the name loads the card back into the picker to change its edition.
        const info = el("button", { type: "button", className: "cart-info", title: t("Change edition") },
          el("strong", { textContent: line.name }),
          el("span", { className: "muted", textContent: detail }));
        info.addEventListener("click", () => editLine(line));
        const li = el(
          "li",
          { className: "cart-item" },
          thumb,
          info,
          el("span", { className: "cart-qty-controls" },
            minus, el("span", { className: "cart-qty", textContent: line.qty }), plus),
          remove
        );
        if (line === highlight) li.classList.add("just-added");
        return li;
      })
    );
    const total = cart.reduce((n, c) => n + c.qty, 0);
    $("cart-count").textContent = tn("{n} card", "{n} cards", total);
    $("cart-empty").hidden = cart.length > 0;
    $("submit").textContent = board === "missing"
      ? (total > 1 ? tn("Add {n} card to Missing", "Add {n} cards to Missing", total) : t("Add to Missing"))
      : board === "wanted"
      ? (total > 1 ? tn("Ask for {n} card", "Ask for {n} cards", total) : t("Ask around"))
      : (total > 1 ? tn("Record loan · {n} card", "Record loan · {n} cards", total) : t("Record loan"));

    // Hidden fields the server reads (one triple per line), plus display data to restore the list after an error.
    $("cart-inputs").replaceChildren(
      ...cart.flatMap((c) => [
        el("input", { type: "hidden", name: "scryfall_id", value: c.id }),
        el("input", { type: "hidden", name: "finish", value: c.finish }),
        el("input", { type: "hidden", name: "quantity", value: c.qty }),
        el("input", { type: "hidden", name: "any_printing", value: c.any ? "1" : "0" }),
      ])
    );
    $("cart-json").value = JSON.stringify(cart);
  }

  function restoreCart() {
    try {
      const saved = JSON.parse($("cart-json").value || "[]");
      if (!Array.isArray(saved)) return;
      cart = saved
        .filter((c) => c && typeof c.id === "string" && FINISH_LABEL[c.finish])
        .slice(0, MAX_LINES)
        .map((c) => ({
          id: c.id, name: String(c.name || t("Unknown card")), set: String(c.set || ""), number: String(c.number || ""),
          image: typeof c.image === "string" && c.image.startsWith("https://") ? c.image : null,
          finish: c.finish, qty: clampQty(parseInt(c.qty, 10)), any: c.any === true,
        }));
    } catch {
      cart = [];
    }
  }

  // --- validation & submit ------------------------------------------------------

  function setStatus(text) {
    printingsStatus.hidden = !text;
    printingsStatus.textContent = text || "";
  }
  function showError(input, message) {
    const field = input.closest(".field");
    field.classList.add("invalid");
    let msg = field.querySelector(".field-error");
    if (!msg) field.append((msg = el("p", { className: "field-error" })));
    msg.textContent = message;
  }
  function clearError(input) {
    const field = input.closest(".field");
    field.classList.remove("invalid");
    field.querySelector(".field-error")?.remove();
  }
  function showCartError(message) {
    cartError.textContent = message;
    cartError.hidden = false;
  }

  form.addEventListener("submit", (e) => {
    // A card that's picked but not yet added counts too, so single-card loans need no extra click.
    if (selected) addSelected();
    let firstBad = null;
    if (!board && !USERNAME_RE.test(other.value)) {
      showError(other, t("Enter their Telegram username (letters, numbers, underscores)."));
      firstBad ||= other;
    }
    if (!cart.length) {
      showCartError(board ? t("Add at least one card.") : t("Add at least one card to the loan."));
      firstBad ||= nameInput;
    }
    if (firstBad) {
      e.preventDefault();
      firstBad.focus();
      return;
    }
    $("submit").disabled = true;
    $("submit").textContent = t("Saving…");
  });

  // Re-enable the button if the page is restored from the back/forward cache.
  window.addEventListener("pageshow", () => {
    $("submit").disabled = false;
    renderCart();
  });

  restoreCart();
  renderCart();
  syncWho();
  highlightChip();
})();
