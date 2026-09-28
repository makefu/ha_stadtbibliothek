/**
 * books-lent-card — grand overview of all lent library books.
 *
 * Reads the `loans` attribute of stadtbibliothek loan sensors
 * (custom_components/stadtbibliothek) and renders one HTML table:
 * Cover | Bibliothek | Titel | Fällig | Verl. möglich | Spätestens.
 *
 * Sorting: renewals_left ascending (shortest first), then due date ascending.
 * Row background encodes remaining renewals: red = 0, orange = 1, yellow = 2.
 * "Spätestens" = due_date + 30 days × renewals_left (one renewal = +30 days).
 * Quick-filter chips per library (Alle / Remseck / Stuttgart) sit in the header
 * so one can collect all books of a single library in one run.
 *
 * Rendering stability: HA hands the card a new hass object on every state
 * update; rebuilding innerHTML each time makes lazy covers reset and rows
 * jump. Therefore we re-render only when the loans payload actually changed
 * (JSON signature), keep all cells at fixed sizes, and never toggle
 * display:none on failed images (visibility only → no layout shift).
 *
 * Config:
 *   type: custom:books-lent-card
 *   entities: [sensor.stadtbibliothek_remseck_liam_loans, ...]
 */

const RENEWAL_DAYS = 30;
const ROW_BG = {
  0: "rgba(244, 67, 54, 0.32)",
  1: "rgba(255, 152, 0, 0.36)",
  2: "rgba(255, 235, 59, 0.38)",
};

function fmtDe(iso) {
  if (!iso) return "";
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function addDays(iso, days) {
  const dt = new Date(`${iso}T00:00:00`);
  dt.setDate(dt.getDate() + days);
  const p = (n) => String(n).padStart(2, "0");
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

class BooksLentCard extends HTMLElement {
  constructor() {
    super();
    this._filter = "";
    this._rows = [];
    this._sig = null;
    this._bound = false;
  }

  setConfig(config) {
    if (!config.entities || !Array.isArray(config.entities) || !config.entities.length) {
      throw new Error("books-lent-card requires a non-empty 'entities' list");
    }
    this._config = config;
  }

  connectedCallback() {
    if (this._bound) return;
    this._bound = true;
    this.addEventListener("click", (e) => {
      const chip = e.target.closest(".blc-fil");
      if (!chip) return;
      const lib = chip.dataset.lib || "";
      this._filter = this._filter === lib ? "" : lib;
      this._render();
    });
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._config) return;
    const sig = JSON.stringify(
      this._config.entities.map((id) => {
        const st = hass.states[id];
        return st && st.attributes ? st.attributes.loans : null;
      })
    );
    if (sig !== this._sig) {
      this._sig = sig;
      this._rows = this._loans();
      this._render();
    }
  }

  getCardSize() {
    return 12;
  }

  _loans() {
    const rows = [];
    for (const entityId of this._config.entities) {
      const st = this._hass.states[entityId];
      const loans = st && st.attributes && st.attributes.loans;
      if (!Array.isArray(loans)) continue;
      for (const l of loans) {
        const due = l.due_date || "";
        const rl = Number.isFinite(l.renewals_left) ? l.renewals_left : 0;
        rows.push({
          lib: l.library || "",
          title: l.title || "",
          due,
          rl,
          latest: due ? addDays(due, RENEWAL_DAYS * rl) : "",
          cover: l.cover_url || "",
          detail: l.detail_url || "",
        });
      }
    }
    rows.sort((a, b) => a.rl - b.rl || a.due.localeCompare(b.due) || a.lib.localeCompare(b.lib) || a.title.localeCompare(b.title));
    return rows;
  }

  _render() {
    if (!this._hass || !this._config) return;
    const rows = this._filter ? this._rows.filter((r) => r.lib === this._filter) : this._rows;
    const libs = [...new Set(this._rows.map((r) => r.lib))].sort();

    let html = `<div class="blc-legend"><b>${rows.length}${rows.length !== this._rows.length ? " von " + this._rows.length : ""} Ausleihen</b>`;
    for (const bg of [0, 1, 2]) {
      html += `<span class="blc-chip" style="background:${ROW_BG[bg]}">${bg} Verl.</span>`;
    }
    html += `<span class="blc-fil-group">`;
    html += `<button type="button" class="blc-fil${this._filter === "" ? " blc-on" : ""}" data-lib="">Alle</button>`;
    for (const lib of libs) {
      html += `<button type="button" class="blc-fil${this._filter === lib ? " blc-on" : ""}" data-lib="${escapeHtml(lib)}">${escapeHtml(lib)}</button>`;
    }
    html += `</span>`;
    html += `<span class="blc-note">Sortiert: wenigste verbleibende Verlängerungen zuerst, dann Fälligkeit. "Spätestens" = fällig + 30 Tage je Verlängerung.</span></div>`;

    html +=
      `<table class="blc-table"><colgroup>` +
      `<col style="width:64px" /><col style="width:110px" /><col />` +
      `<col style="width:104px" /><col style="width:120px" /><col style="width:114px" />` +
      `</colgroup><thead><tr>` +
      `<th>Cover</th><th>Bibliothek</th><th>Titel</th><th>Fällig</th><th>Verl. möglich</th><th>Spätestens</th>` +
      `</tr></thead><tbody>`;

    for (const r of rows) {
      const bg = r.rl > 2 ? "transparent" : ROW_BG[r.rl];
      const img = r.cover
        ? `<img src="${r.cover}" width="48" height="64" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'" />`
        : `<span class="blc-nocover"></span>`;
      const title = r.detail
        ? `<a href="${r.detail}" target="_blank" rel="noopener noreferrer">${escapeHtml(r.title)}</a>`
        : escapeHtml(r.title);
      html +=
        `<tr style="background:${bg}">` +
        `<td class="blc-cover">${img}</td>` +
        `<td>${escapeHtml(r.lib)}</td>` +
        `<td>${title}</td>` +
        `<td>${fmtDe(r.due)}</td>` +
        `<td>${r.rl}</td>` +
        `<td>${fmtDe(r.latest)}</td>` +
        `</tr>`;
    }
    html += `</tbody></table>`;

    this.innerHTML = `<style>
      :host { display: block; }
      .blc-legend { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 4px; color: var(--primary-text-color, inherit); min-height: 40px; }
      .blc-chip { padding: 1px 8px; border-radius: 10px; font-size: 12px; }
      .blc-fil-group { display: inline-flex; gap: 4px; margin-left: 8px; }
      .blc-fil { font: inherit; font-size: 13px; padding: 3px 12px; border-radius: 16px; border: 1px solid var(--mdc-variant-border-color, var(--divider-color, #e0e0e0)); background: transparent; color: var(--primary-text-color, inherit); cursor: pointer; }
      .blc-fil.blc-on { background: var(--primary-color, #03a9f4); border-color: var(--primary-color, #03a9f4); color: var(--text-primary-color, #fff); }
      .blc-note { font-size: 12px; opacity: 0.7; }
      .blc-table { width: 100%; border-collapse: collapse; table-layout: fixed; color: var(--primary-text-color, inherit); }
      .blc-table th, .blc-table td { border-bottom: 1px solid var(--divider-color, #e0e0e0); padding: 6px 10px; text-align: left; vertical-align: middle; overflow-wrap: anywhere; }
      .blc-table th { font-size: 12px; text-transform: uppercase; opacity: 0.7; }
      .blc-table td { height: 76px; }
      .blc-cover { width: 64px; }
      .blc-cover img { height: 64px; width: 48px; object-fit: contain; border-radius: 4px; background: var(--card-background-color, #fff); }
      .blc-nocover { display: inline-block; height: 64px; width: 48px; }
      .blc-table a { color: var(--primary-color, #03a9f4); text-decoration: none; }
    </style>${html}`;
  }
}

customElements.define("books-lent-card", BooksLentCard);
