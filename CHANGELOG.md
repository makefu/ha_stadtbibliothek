# Changelog

## v1.4.1

- Packaging: `pyproject.toml` carries the released version. The flake derives
  the derivation version from `manifest.json`, so every build from v1.4.0
  failed `pythonMetadataCheckPhase` (derivation 1.4.0 vs metadata 1.3.0),
  which blocked any consumer building the library from a tag. No runtime
  change.

## v1.4.0

### Covers for lent books

- Stuttgart (aDIS): the loan listing renders no jacket images, so covers are
  now looked up per loan — the integration POSTs the loan title to the OPAC's
  catalogue search and reads the jacket from the result rows (`img[data-src]`)
  or from a single-hit detail page. Covers come from the Deutsche
  Nationalbibliothek cover API (`api.vlb.de`) with the OPAC's client token.
  Title matching normalises (NFKC, case, brackets, ss/ß) and rejects detail
  pages whose "Gesucht wurde mit" echo doesn't contain the query (aDIS serves
  the previous result set when queried too fast); one retry with
  subtitle/brackets stripped; per-loan failures are swallowed so one bad
  lookup never fails the whole refresh.
- Coordinator calls `fetch_details()` per loan only when the backend
  advertises `supports_details`.
- Remseck (Koha): cover and catalogue links Koha already sends in the
  checkout table are now carried (`td.jacketcell img`, `no-image` placeholders
  filtered).
- `LoanItem` gained `barcode`, `publisher`, `isbn`, `cover_url`, `detail_url`;
  all of them surface in the `loans` attribute of the loans sensor via the
  serializer.
- Live measurement: 24/28 Stuttgart loans get covers (rest are games/CDs
  without a vlb image); 25/27 for Remseck.
- Deploying needs one HA **restart**, not just a config-entry reload: HA keeps
  imported custom-component modules in memory.

### Robustness

- Backends distinguish an empty account from an unreadable page: a missing
  login/listing marker raises `ParseError` (→ sensor `unavailable`) instead of
  silently reporting zero loans, which used to be able to erase a borrowing
  history.
- Remseck: logged-in detection uses the masthead markers shared by every OPAC
  page; the previous `#useraccount`-only check misjudged current installs.
- Stuttgart: the title cell is classified by shape (media-type prefix, barcode
  pattern) instead of by row position.
- httpx client is only closed if the backend created it; OPAC base URL is
  overridable per instance.

### Tooling

- Backend registry replaces the duplicated `BACKEND_MAP`.
- Flake exposes the library as a Python module, not only as an application.
- Test suite records the live OPACs into fixtures
  (`tests/test_backends/fixtures/recorded/`) and asserts the parsers against
  them, including the new Stuttgart search fixtures.

### Docs / examples

- `examples/books-lent-dashboard/`: ready-made Lovelace panel dashboard +
  dependency-free custom card (`books-lent-card`) rendering the `loans`
  attributes of all accounts into one colour-coded, renewals-sorted table with
  covers and per-library filter chips.

## v1.2.1

- Coordinator: `create_backend` is async.
- Flake: ruff and ty checks in the nix build.

## v1.2.0

- Fix Remseck renewal: renew by `itemnumber` + `borrowernumber` instead of
  barcode.
- Enrich `renew_loan` / `renew_all` service responses with per-item results.

## v1.1.0

- Stuttgart (aDIS): implement loan renewal (correct button/checkbox values).
- `renew_all` for Stuttgart defaults to a 7-day due-date threshold.
- CLI: share common logic between the two backends, add `item_id` to status
  output.
