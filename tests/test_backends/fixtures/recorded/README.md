# Recorded OPAC pages

Verbatim captures of the two live installations, run through `record_fixtures.py`
in the repository root. Nothing in this directory is hand-written or trimmed.

That is the point. The fixtures one directory up are constructed: they tell a
small, controlled story (four loans, some fees, one renewal) that the unit
tests assert against line by line. Constructed fixtures drift. Twice already a
backend has been "fixed" against markup that only ever existed in a fixture --
`#useraccount` on every Koha tab, `section#results` on the aDIS loan page --
and both times the live installations disagreed and every loan vanished. These
recordings are the counterweight: whatever the real servers send, they say so.

## Provenance

Captured 2026-08-31 from the accounts in `.secrets.yml`.

| File | Request | Notes |
| --- | --- | --- |
| `remseck_checkouts.html` | `GET /cgi-bin/koha/opac-user.pl` | Koha 22.11, 27 loans. Also the body the login `POST` returns. |
| `remseck_account.html` | `GET /cgi-bin/koha/opac-account.pl` | `#finestable` present with an empty `tbody`: no fees owed. |
| `remseck_logged_out.html` | same URL, no session | `form#auth`, none of the masthead markers. |
| `remseck_detail.html` | `GET /cgi-bin/koha/opac-detail.pl?biblionumber=…` | Carries `span[property="isbn"]`. |
| `remseck_detail_ean.html` | same, different record | A tiptoi puzzle: no ISBN, and an `ean` of `4005556001385`. |
| `stuttgart_home.html` | `GET /?service=…&sp=SOPAC` | Search mask; step 1 of the aDIS login. |
| `stuttgart_login_form.html` | `POST` of step 1 | The credentials form. |
| `stuttgart_account.html` | `POST` of the credentials | "Mein Konto", carrying `div#konto-services`. |
| `stuttgart_ausleihen.html` | `GET` the Ausleihen link | aDIS/BMS, 11 loans. |

No renewal response is recorded: renewing is a real change to a real account,
and the recorded Remseck loans were all inside their `no-renewal-before`
window anyway. The renewal fixtures one directory up stay constructed.

## What was removed

Identity, and nothing else:

- the account holder's name, in every greeting and breadcrumb;
- the Koha `borrowernumber`, rewritten to `10000001`;
- the aDIS `jsessionid` and its per-session object handles, remapped so that
  distinct handles stay distinct and the Ausleihen link still matches the page
  it fetches;
- the counter pickup code aDIS prints beside the loan list;
- Koha's `csrf_token`.

Titles, authors, call numbers, branches, due dates, barcodes, biblionumbers and
cover URLs are all as recorded. They are the data under test -- inventing them
is exactly how the constructed fixtures came to describe markup that does not
exist -- and they are catalogue records, not personal data. What they reveal
about the account holder is which children's books were on loan on one day.

## Refreshing

```sh
nix run .#record-fixtures -- /path/to/.secrets.yml
```

The recorder writes here directly and refuses to write anything it can still
find identifying data in. Expect the loan counts and dates in
`test_recorded_pages.py` to move with the recording; that test asserts the
shape of the markup and the corner cases, not the borrowing.
