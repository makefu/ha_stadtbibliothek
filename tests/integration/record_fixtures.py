"""Re-record the fixtures in tests/test_backends/fixtures/recorded/.

Walks both live OPACs with real credentials, writes every response body out
verbatim, then strips the account holder from it. Read-only against the
libraries: it logs in, reads, and never renews or changes anything.

    nix run .#record-fixtures -- /path/to/.secrets.yml

Refuses to write if the anonymiser can still find identifying data in a page,
because a fixture is only useful if it can be committed.
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup, Tag

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from custom_components.stadtbibliothek.backends.remseck import RemseckBackend  # noqa: E402
from custom_components.stadtbibliothek.backends.stuttgart import StuttgartBackend  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "test_backends" / "fixtures" / "recorded"

FAKE_JSESSIONID = "0123456789ABCDEF0123456789ABCDEF"
FAKE_BORROWERNUMBER = "10000001"
FAKE_ABHOLCODE = "Mu1234"
#: A German-catalogue-shaped placeholder, matching the constructed fixtures.
FAKE_FORENAME = "Max"
FAKE_SURNAME = "Mustermann"

#: aDIS addresses server-side objects as _<hex>_<hex> and the pair is session
#: scoped. Remapped rather than blanked: distinct handles have to stay
#: distinct, or the Ausleihen link stops being distinguishable from any other.
_ADIS_HANDLE = re.compile(r"_[0-9A-F]{8}_[0-9A-F]{8}")
_JSESSIONID = re.compile(r"(?<=jsessionid=)[0-9A-F]{16,}")
_ABHOLCODE = re.compile(r"(Abholcode:\s*)\S+")
_CSRF = re.compile(r'(name="csrf_token"[^>]*value=")[^"]*(")')


class Anonymiser:
    """Removes identity from a recorded page, consistently across pages.

    Names are taken as a set of tokens rather than a forename/surname pair:
    the two OPACs print the account holder differently -- Koha as "Vorname
    Nachname", aDIS as "Nachname, Vorname" -- and both are read off their own
    pages, so a middle name or a spelling only one of them uses is covered.
    """

    def __init__(self, names: set[str], borrowernumber: str | None) -> None:
        self._names = {name for name in names if len(name) > 1}
        self._borrowernumber = borrowernumber
        self._handles: dict[str, str] = {}
        # The first token stands in for the forename, the rest for the surname,
        # so the substitution still reads as a name rather than as one word
        # repeated.
        ordered = sorted(self._names)
        self._replacements = {
            name: (FAKE_FORENAME if index == 0 else FAKE_SURNAME) for index, name in enumerate(ordered)
        }

    def _handle(self, match: re.Match[str]) -> str:
        key = match.group(0)
        if key not in self._handles:
            nth = len(self._handles) + 1
            self._handles[key] = f"_{nth:08X}_{nth:08X}"
        return self._handles[key]

    def scrub(self, text: str) -> str:
        for name, fake in self._replacements.items():
            text = re.sub(rf"\b{re.escape(name)}\b", fake, text)
        if self._borrowernumber:
            text = text.replace(self._borrowernumber, FAKE_BORROWERNUMBER)
        text = _JSESSIONID.sub(FAKE_JSESSIONID, text)
        text = _ADIS_HANDLE.sub(self._handle, text)
        text = _ABHOLCODE.sub(rf"\g<1>{FAKE_ABHOLCODE}", text)
        text = _CSRF.sub(r"\g<1>0000000000000000\g<2>", text)
        return text

    def leaks(self, text: str) -> list[str]:
        found = [name for name in self._names if re.search(rf"\b{re.escape(name)}\b", text)]
        for label, pattern in (
            ("jsessionid", rf"jsessionid=(?!{FAKE_JSESSIONID})[0-9A-F]{{16,}}"),
            ("Abholcode", rf"Abholcode:\s*(?!{FAKE_ABHOLCODE})\S+"),
        ):
            if re.search(pattern, text):
                found.append(label)
        if self._borrowernumber and self._borrowernumber in text:
            found.append("borrowernumber")
        return found


class Recorder:
    def __init__(self) -> None:
        self.pages: dict[str, str] = {}

    def add(self, name: str, text: str) -> None:
        self.pages[name] = text
        print(f"  recorded {name}.html ({len(text)} bytes)")

    def write(self, anonymiser: Anonymiser) -> int:
        scrubbed = {name: anonymiser.scrub(text) for name, text in self.pages.items()}
        failed = 0
        for name, text in sorted(scrubbed.items()):
            leaks = anonymiser.leaks(text)
            if leaks:
                print(f"  REFUSING {name}.html: still contains {', '.join(leaks)}")
                failed += 1
        if failed:
            return failed
        OUT.mkdir(parents=True, exist_ok=True)
        for name, text in sorted(scrubbed.items()):
            (OUT / f"{name}.html").write_text(text, encoding="utf-8")
            print(f"  wrote {name}.html")
        return 0


def _koha_patron_names(soup: BeautifulSoup) -> set[str]:
    """Read the account holder's name out of Koha's welcome line."""
    label = soup.select_one("span.userlabel")
    if label is None:
        return set()
    # "Willkommen, Herr Vorname Nachname" -- the title sits in its own span.
    for title in label.select("span.patron-title"):
        title.decompose()
    return set(label.get_text(" ", strip=True).replace("Willkommen,", "").split())


#: aDIS greets the account holder on its overview page.
_ADIS_GREETING = re.compile(r"Hallo\s+([^<]+?)\s*</span>")


def _adis_patron_names(html: str) -> set[str]:
    match = _ADIS_GREETING.search(html)
    return set(match.group(1).split()) if match else set()


async def record_remseck(recorder: Recorder, username: str, password: str) -> tuple[set[str], str | None]:
    print("Remseck (Koha/LMSCloud)")
    backend = RemseckBackend()
    client = backend._client

    await backend.login(username, password)

    resp = await client.get(f"{backend.base_url}/cgi-bin/koha/opac-user.pl")
    resp.raise_for_status()
    recorder.add("remseck_checkouts", resp.text)
    soup = BeautifulSoup(resp.text, "lxml")

    resp = await client.get(f"{backend.base_url}/cgi-bin/koha/opac-account.pl")
    resp.raise_for_status()
    recorder.add("remseck_account", resp.text)

    # The logged-out page, for the ParseError path. A separate client, so the
    # session we just established stays untouched.
    async with httpx.AsyncClient(follow_redirects=True) as anonymous:
        resp = await anonymous.get(f"{backend.base_url}/cgi-bin/koha/opac-user.pl")
        recorder.add("remseck_logged_out", resp.text)

    # One detail page with an ISBN and one without, so the EAN corner case
    # keeps a real example: non-book media carry a product GTIN in that field.
    loans = await backend.get_loans()
    borrowernumber = backend._borrowernumber
    want = {"remseck_detail": True, "remseck_detail_ean": False}
    for loan in loans:
        if not loan.detail_url or not want:
            continue
        resp = await client.get(loan.detail_url)
        resp.raise_for_status()
        has_isbn = 'property="isbn"' in resp.text
        for name, wanted_isbn in list(want.items()):
            if wanted_isbn is has_isbn:
                recorder.add(name, resp.text)
                del want[name]
                break
    if want:
        print(f"  WARNING: no record on loan for {', '.join(want)}")

    await backend.close()

    names = _koha_patron_names(soup)
    if len(names) < 2:
        raise SystemExit("Could not read the patron name off the Koha page; refusing to record blind.")
    return names, borrowernumber


async def record_stuttgart(recorder: Recorder, username: str, password: str) -> set[str]:
    print("Stuttgart (aDIS/BMS)")
    backend = StuttgartBackend()
    client = backend._client

    # login() does not keep the pages it walks through, so the three steps are
    # repeated here. Any divergence from the backend is a bug in this script.
    resp = await client.get(f"{backend.base_url}{backend.START_PATH}")
    resp.raise_for_status()
    recorder.add("stuttgart_home", resp.text)

    soup = BeautifulSoup(resp.text, features="html.parser")
    form = soup.find("form")
    if not isinstance(form, Tag):
        raise SystemExit("No form on the aDIS start page")
    login_url = f"{backend.base_url}{form.attrs['action']}"

    data = backend._extract_hidden_inputs(form)
    data["SUO1_AUTHFU_1_hidden"] = ""
    data["select"] = "- Alle -"
    data["selected"] = "ZTEXT       *SBK"
    data["Form0"] = (
        "focus,keyCode,stz,source,selected,requestCount,scriptEnabled,"
        "scrollPos,scrDim,winDim,imgDim,SUO1_AUTHFU_1,$Autosuggest,"
        "select,$FormConditional,textButton,$FormConditional$0,"
        "textButton$0,$FormConditional$1,$FormConditional$2,"
        "$FormConditional$3,$FormConditional$4"
    )
    data.pop("textButton$0", None)

    resp = await client.post(login_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp.raise_for_status()
    recorder.add("stuttgart_login_form", resp.text)

    soup = BeautifulSoup(resp.text, features="html.parser")
    form = soup.find("form")
    if not isinstance(form, Tag):
        raise SystemExit("No aDIS login form")
    data = backend._extract_hidden_inputs(form)
    data["$Textfield"] = username
    data["$Textfield$0"] = password
    data["focus"] = "$$GFBO_2"
    data["scriptEnabled"] = "true"
    for key in ("textButton$0", "textButton$1", "textButton$2"):
        data.pop(key, None)
    data["Form0"] = (
        "focus,keyCode,stz,source,select,selected,requestCount,"
        "scriptEnabled,scrollPos,scrDim,winDim,imgDim,$Textfield,"
        "$Textfield$0,$FormConditional,textButton,$FormConditional$0,"
        "textButton$0,$FormConditional$1,textButton$1,"
        "$FormConditional$2,textButton$2"
    )

    resp = await client.post(login_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    resp.raise_for_status()
    recorder.add("stuttgart_account", resp.text)

    soup = BeautifulSoup(resp.text, features="html.parser")
    ausleihen_url = None
    for link in soup.select("div#konto-services li a"):
        if "Ausleihen" in link.text:
            ausleihen_url = f"{backend.base_url}{link.attrs['href']}"
            break
    if ausleihen_url is None:
        raise SystemExit("Login succeeded but no Ausleihen link; nothing to record")

    resp = await client.get(ausleihen_url)
    resp.raise_for_status()
    recorder.add("stuttgart_ausleihen", resp.text)

    # The cover route: fetch_details() searches the catalogue for each loan
    # title and reads the jacket off the result row or the single-hit
    # Vollanzeige. The unit tests key their search router on the *constructed*
    # fixture's titles (fixtures/stuttgart_ausleihen.html, one directory up),
    # not on whatever happens to be out on the live account today, so those
    # are the queries recorded here. The last one is a title the OPAC answers
    # with a single Vollanzeige, keeping a real example of the detail-page
    # cover route.
    constructed = OUT.parent / "stuttgart_ausleihen.html"
    # Parse it through the real backend over a transport that just hands the
    # page back, so the queries stay the ones the tests route on.
    stub = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=constructed.read_text()))
    )
    probe = StuttgartBackend(stub, base_url=backend.base_url)
    probe._ausleihen_url = f"{backend.base_url}/aDISWeb/app"
    queries = [loan.title for loan in await probe.get_loans()] + ["Der Koboldmaki und der große Sturm"]
    await probe.close()
    for index, query in enumerate(queries):
        mask = await client.get(f"{backend.base_url}{backend.START_PATH}")
        mask.raise_for_status()
        sform = backend._find_search_form(BeautifulSoup(mask.text, features="html.parser"))
        if sform is None:
            raise SystemExit("No search form on the aDIS start page")
        sdata = backend._extract_hidden_inputs(sform)
        sdata["$Autosuggest"] = query
        sdata["SRCHAW"] = "Katalog"
        sdata["textButton"] = "Suchen"
        sdata.pop("textButton$0", None)
        resp = await client.post(
            f"{backend.base_url}{sform.attrs['action']}",
            data=sdata,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        kind = (
            "Vollanzeige" if "show-full-basics" in resp.text else ("Trefferliste" if "rList_li" in resp.text else "?")
        )
        recorder.add(f"stuttgart_search_{index:02d}", resp.text)
        print(f"    [{index}] {query[:40]:40} {kind}")

    await backend.close()

    names = _adis_patron_names(recorder.pages["stuttgart_account"])
    if len(names) < 2:
        raise SystemExit("Could not read the patron name off the aDIS page; refusing to record blind.")
    return names


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    secrets = yaml.safe_load(Path(sys.argv[1]).read_text())

    recorder = Recorder()
    names, borrowernumber = await record_remseck(
        recorder, str(secrets["remseck_username"]), str(secrets["remseck_password"])
    )
    names |= await record_stuttgart(recorder, str(secrets["stuttgart_username"]), str(secrets["stuttgart_password"]))

    print(f"Anonymising {len(names)} name token(s) as {FAKE_FORENAME} {FAKE_SURNAME}")
    return recorder.write(Anonymiser(names, borrowernumber))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
