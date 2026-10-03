// Card / edition / finish picker backed by the Scryfall API (https://scryfall.com/docs/api).
const API = "https://api.scryfall.com";

const nameInput = document.getElementById("card-name");
const suggestions = document.getElementById("card-suggestions");
const printingSelect = document.getElementById("printing");
const finishSelect = document.getElementById("finish");
const scryfallId = document.getElementById("scryfall-id");
const preview = document.getElementById("preview");

let printings = [];
let autocompleteTimer;
let loadedName = null;

function cardImage(card) {
  const uris = card.image_uris || (card.card_faces && card.card_faces[0].image_uris) || {};
  return uris.normal || uris.small;
}

nameInput.addEventListener("input", () => {
  clearTimeout(autocompleteTimer);
  const q = nameInput.value.trim();
  if (q.length < 2) return;
  autocompleteTimer = setTimeout(async () => {
    const res = await fetch(`${API}/cards/autocomplete?q=${encodeURIComponent(q)}`);
    if (!res.ok) return;
    const { data } = await res.json();
    suggestions.replaceChildren(...data.map((name) => new Option(name)));
    // Picking from the datalist fires "input" with an exact match; load printings right away.
    if (data.includes(nameInput.value)) loadPrintings(nameInput.value);
  }, 200);
});

nameInput.addEventListener("change", () => loadPrintings(nameInput.value.trim()));

async function loadPrintings(name) {
  if (!name || name === loadedName) return;
  loadedName = name;
  resetPrinting("Loading…");

  printings = [];
  let url = `${API}/cards/search?unique=prints&order=released&q=${encodeURIComponent(`!"${name}" game:paper`)}`;
  while (url) {
    const res = await fetch(url);
    if (!res.ok) break;
    const page = await res.json();
    printings.push(...page.data);
    url = page.has_more ? page.next_page : null;
  }
  if (name !== loadedName) return; // a newer lookup started meanwhile

  if (!printings.length) {
    resetPrinting("No printings found");
    return;
  }
  printingSelect.replaceChildren(
    ...printings.map((p, i) =>
      new Option(`${p.set_name} (${p.set.toUpperCase()} #${p.collector_number}) · ${p.released_at.slice(0, 4)}`, i)
    )
  );
  printingSelect.disabled = false;
  selectPrinting();
}

function resetPrinting(label) {
  printingSelect.replaceChildren(new Option(label, ""));
  printingSelect.disabled = true;
  scryfallId.value = "";
  preview.hidden = true;
}

function selectPrinting() {
  const p = printings[printingSelect.value];
  if (!p) return;
  scryfallId.value = p.id;

  // Only offer finishes this printing actually exists in.
  for (const opt of finishSelect.options) opt.disabled = !p.finishes.includes(opt.value);
  if (finishSelect.selectedOptions[0].disabled) {
    finishSelect.value = p.finishes[0];
  }

  const img = cardImage(p);
  preview.hidden = !img;
  if (img) preview.src = img;
}

printingSelect.addEventListener("change", selectPrinting);

document.getElementById("loan-form").addEventListener("submit", (e) => {
  if (!scryfallId.value) {
    e.preventDefault();
    alert("Pick a card and edition first.");
  }
});
