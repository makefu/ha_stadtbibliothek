# Books-lent dashboard example

Grand overview of every lent book across all configured library accounts, as a
full-width panel dashboard: cover images, due dates, remaining renewals, and a
red/orange/yellow row color-coding by remaining renewals — sorted so the books
that must be handed back soonest are on top. Click a library chip
(`Alle | Remseck | Stuttgart`, derived from the data) to show only that
library's books, which is exactly what you need to collect them in one batch.

## Why a custom card

Home Assistant's builtin markdown card runs its content through the `xss`
library's `filterXSS` (see `markdown-worker.ts` in the HA frontend), which
**strips `style` attributes** — per-row background colors are impossible there,
and images plus fixed column widths want real DOM control anyway. The card is
therefore a ~200-line vanilla-JS custom element with no build step and no
dependencies.

## Files

|File|Purpose|
|---|---|
|`books-lent-card.js`|The custom card (custom element `books-lent-card`).|
|`dashboard-example.yaml`|Dashboard config: one panel view containing the card.|
|`books-lent-card-test.html`|Standalone render harness — open `http://<ha>/local/books-lent-card-test.html#<long-lived-token>` to render the card outside the HA frontend (handy for headless/visual checks).|

## Install

1. Copy `books-lent-card.js` into your HA config's `www/` directory.
2. Register it as a Lovelace dashboard resource (raw resources /
   `lovelace.resources` via UI or the `/api/config/lovelace/resources` REST
   API):
   - `url: /local/books-lent-card.js?v=1` (type `module`)
   - The `?v=` query is **required**: HA serves `/local/` with
     `Cache-Control: public, max-age=2678400`, so edits only reach browsers
     when the URL changes — bump it on every redeploy.
3. Create a dashboard (`Settings → Dashboards → Add dashboard`, e.g. url_path
   `buecher-uebersicht`, "Open in new tab" to take control of its storage
   mode) and put the config from `dashboard-example.yaml` in. Swap the two
   `sensor.stadtbibliothek_*_loans` entity ids for your own — the sensors are
   created by this integration, one per configured account; only the `*loans`
   sensors carry the per-book `loans` attribute.

## Card behavior (contract it implements)

- Reads `attributes.loans` of every entity in `entities:` — each loan dict
  needs `title`, `due_date` (`YYYY-MM-DD`), `library`, `renewals_left`;
  optional: `cover_url`, `detail_url`.
- Sort: `renewals_left` ascending, then `due_date` ascending.
- Row background: red at 0 remaining renewals, orange at 1, yellow at 2,
  none above. "Spätestens" column = `due_date + 30 days × renewals_left`
  (one renewal extends the due date by 30 days).
- Quick filter chips per distinct `library` value; clicking the active chip
  resets to all.
- Render stability: the card signatures the loans payload and only rebuilds
  its DOM when it changes, so unrelated HA state updates (which hand the card
  a fresh `hass` object every time) never make the table blink or jump;
  `table-layout: fixed` + explicit column widths keep rows from reflowing
  while lazy cover images load.

## Maintenance note

This copy lives here as an example. The deployment that renders it on the
author's instance is tracked in the `claude-hass` notebook repo
(`www/books-lent-card.js` + `automations/buecher_uebersicht_dashboard.md`); if
you adopt the card, this file becomes yours to own.
