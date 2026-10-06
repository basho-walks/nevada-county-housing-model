"""Download CDI insurance snapshots and build data/insurance_zip_year.csv keyed on (zcta, year, policy_form).

Values the source does not publish stay NaN, never 0. FAIR Plan files (S9, S10) carry no reuse grant,
so they are only described here and never parsed into a committed output (docs/schema.md section 8).
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pandas as pd

from ingest.keys import DuplicateKeyError, assert_unique_keys, map_zip_to_zcta, normalize_year, normalize_zip
from ingest.manifest import sha256, verify_manifest, write_manifest

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw" / "insurance"
RESTRICTED = DATA / "raw" / "restricted" / "insurance"
OUTPUT = DATA / "insurance_zip_year.csv"
UNMATCHED = DATA / "interim" / "unmatched" / "insurance_zip_year.csv"

KEY = ("zcta", "year", "policy_form")
POLICY_FORMS = ("admitted_homeowners", "dwelling_fire", "fair_plan", "supplemental")

# docs/schema.md section 2: core and fringe ZIPs. 95712 and 95924 have no 2020 ZCTA and stay unmatched.
TARGET_ZIPS = frozenset({"95945", "95949", "95959", "95946", "95975", "95986", "95960", "95977", "95602", "95712", "95924"})
TARGET_ZCTAS = frozenset(TARGET_ZIPS - {"95712", "95924"})

CDI_TERMS = "CDI public domain (https://www.insurance.ca.gov/privacy-policy/, Ownership); docs/sources.md S1"
CFP_TERMS = "California FAIR Plan terms grant no reuse (https://www.cfpnet.com/terms-and-conditions/); docs/sources.md S9"
CDI_WRR = "https://www.insurance.ca.gov/01-consumers/200-wrr/upload/"
CDI_HO = "https://www.insurance.ca.gov/0400-news/0200-studies-reports/0250-homeowners-study/upload/"
CFP = "https://www.cfpnet.com/wp-content/uploads/"

# S2 policy codes kept in the output. FP is undefined by CDI (docs/sources.md S2); RT, CO, MH are other forms.
S2_FORMS = {"HO": "admitted_homeowners", "DO": "dwelling_fire", "DT": "dwelling_fire"}
S2_COVERAGE_BASIS = {2018: "end_of_year", 2019: "end_of_year", 2020: "end_of_year"}  # start_of_year from 2021
MIX_SHIFT_FORM_SHARE = 0.05  # absolute change in a form's share of S2 admitted exposure, year on year
MIX_SHIFT_OCCUPANCY = 0.10  # absolute change in the owner-occupied share of dwelling-fire exposure
S6_PCT_TOLERANCE = 0.0006  # published percentages are rounded to 0.1 point


@dataclass(frozen=True)
class Source:
    sid: str  # docs/sources.md ID
    name: str
    vintage: str
    url: str
    sha256: str  # as audited in docs/sources.md; a different hash is a new vintage
    restricted: bool = False

    @property
    def filename(self) -> str:
        return self.url.rsplit("/", 1)[1]

    @property
    def directory(self) -> Path:
        return (RESTRICTED if self.restricted else RAW) / self.name / self.vintage


def _s(sid, name, vintage, url, digest, restricted=False):
    return Source(sid, name, vintage, url, digest, restricted)


SOURCES = (
    _s("S1", "cdi_s1_part1_summary", "2008-2023", CDI_HO + "A-Part-I-Data-Summary.xlsx", "09df35ad84d73736f60873e053990ce9dc567fddf076487a30500a46f8308e65"),
    _s("S1", "cdi_s1_part1_summary", "2001-2017", CDI_HO + "A-Homeowners-2_v1-2.pdf", "c540bd4fc4b430d216a55fb6d67c9b8e8ef8c558f7f1b271184195fa2d298234"),
    _s("S1", "cdi_s1_part1_summary", "2001-2017", CDI_HO + "A-DF-Owner-Occupied_v1.pdf", "9a420af3a1a2f3a7d26bf6aca571788b03164e7ae21fa18bfd83afc12b98d7ff"),
    _s("S1", "cdi_s1_part1_summary", "2001-2017", CDI_HO + "A-DF-Rental-2_v1.pdf", "62879ea4cad1b9ad3654e9057b16b53c08f88a8c51113124d942e0e59f804ba6"),
    _s("S1", "cdi_s1_part1_summary", "2001-2017", CDI_HO + "A-Mobile-Homes_v1.pdf", "719818f4b93181d6657bfa981de42dac4698349a8585b523781662b8d2d3febb"),
    _s("S1", "cdi_s1_part1_summary", "2001-2017", CDI_HO + "A-Condo_v1.pdf", "46acf8fe43650e7d096e8ecf5bb3c9dd3413cdae442d79e65353583cb59f71ee"),
    _s("S1", "cdi_s1_part1_summary", "2001-2017", CDI_HO + "A-Renters_v1.pdf", "5264b3cbe2eba64452da672f0b5afb37095f47e8837f4fe8d1c98216aacb5695"),
    _s("S2", "cdi_s2_sb824_zip", "2018-2023", CDI_WRR + "Residential-Property-Coverage-Amounts-Wildfire-Risk-and-Losses.xlsx", "1db226b44fbcccae99afff2f6732dae6f656913425d17930df5ff93e7237cf70"),
    _s("S2", "cdi_s2_sb824_zip", "2018-2023", CDI_WRR + "Format-of-the-Report-and-Methodology.docx", "ee879bce1540aec040030be9b70a9c0ec4d35e5718714e728b4cb8e63ade0d6d"),
    _s("S3", "cdi_s3_nonrenewal_zip", "2015-2021", CDI_WRR + "Residential-Property-Voluntary-Market-New-Renew-NonRenew-by-ZIP-2015-2021.xlsx", "e225867ebafb56a073e8049afaef2bb0cb45d7d6bc2cb46dabc988ea8e69b667"),
    _s("S3", "cdi_s3_nonrenewal_zip", "2020-2023", CDI_WRR + "Residential-Insurance-Voluntary-Market-New-Renew-NonRenew-by-ZIP-2020-2023.xlsx", "316d0ee62f110f9f7f762f052c4e9bb09fb05cd6d9074a796b740247a8f0636d"),
    _s("S4", "cdi_s4_nonrenewal_county", "2015-2021", CDI_WRR + "Residential-Insurance-Policy-Analysis-by-County-2015-to-2021-2.pdf", "85b270f2ec2bee40b7641c55b27eaf664609cdf0bf2b9e9c1488ffd08d20441a"),
    _s("S4", "cdi_s4_nonrenewal_county", "2020-2023", CDI_WRR + "Residential-Insurance-Policy-Analysis-by-County-2020-to-2023-2.pdf", "c1da5395f4ff8bd5db0eea92674fe354f01617e4f2f52e8f49f657ac88e6dac9"),
    _s("S5", "cdi_s5_policy_count_county", "2017", CDI_HO + "C-2017-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf", "0c0d204f50b9e98120444ce6229bc5bf909705cbf0297629327fb403fe12417c"),
    _s("S5", "cdi_s5_policy_count_county", "2019", CDI_HO + "C-2019-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf", "12f0ced087b2e080ced666127cc40ff7dcf568e94235beed9579122b9ca3b4de"),
    _s("S5", "cdi_s5_policy_count_county", "2021", CDI_HO + "C-2021-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf", "8bafc2e73bca8f3e1b1136353b631240e9a174d1f0a31c863722f55c4e2223d7"),
    _s("S5", "cdi_s5_policy_count_county", "2023", CDI_HO + "C-2023-Number-of-Residential-Property-with-Fire-or-Earthquake-Insurance-by-County.pdf", "0a834e1ede1f866879b4197cb2f397bf8f9dabaeea86ef416cbe2e7456c04082"),
    _s("S6", "cdi_s6_fair_plan_share_zip", "2022", CDI_WRR + "Number-of-Residential-Dwelling-Units-Insured-in-2022-FAIR-Plan-vs-Voluntary.pdf", "dbc44c37b1e4024eacd06a120e89892a1ea6917b666aed7dcee7afc58dfe0cd8"),
    _s("S7", "cdi_s7_fact_sheets", "2023", CDI_WRR + "CDI-Fact-Sheet-Summary-on-Residential-Insurance-Policies-and-the-FAIR-Plan-v-011325-2.pdf", "1808dbaa3ea6a022e43befe9e97cd5a853632a40fb59f275675a2fccec864c7e"),
    _s("S7", "cdi_s7_fact_sheets", "2022", CDI_WRR + "CDI-Fact-Sheet-Summary-on-Residential-Insurance-Policies-and-the-FAIR-Plan.pdf", "2a41b112658ab49540a69ee7080638eb1cb4693ff2ed2039a3378fe67ac618ed"),
    _s("S7", "cdi_s7_fact_sheets", "2021", CDI_WRR + "CDI-Fact-Sheet-Residential-Insurance-Market-Policy-Count-Data-December-2022.pdf", "9c5e8900a2fa9b2bc37d0190ad43086732dfa28b81532dd9f67513856df71f6d"),
    _s("S7", "cdi_s7_fact_sheets", "2020", "https://www.insurance.ca.gov/0400-news/0100-press-releases/2021/upload/nr117DataNon-RenewalsandFAIRPlan12202021.pdf", "0db7aa40ae2bf797cce836d532290e533102c9f7d39425cef095d295f3902efd"),
    _s("S7", "cdi_s7_fact_sheets", "2019", "https://www.insurance.ca.gov/0400-news/0100-press-releases/2020/upload/nr104Charts-NewRenewedNon-RenewedData-2015-2019-101920.pdf", "a7114553ca6f6f25ddc02a1cbc7921307cb1ba1d7b098ba5d51bee3ef564e5f2"),
    _s("S7", "cdi_s7_fact_sheets", "2015-2018", "https://www.insurance.ca.gov/0400-news/0100-press-releases/2019/upload/nr063_factsheetwildfire.pdf", "f6ed0edeece860035c02e089a05e0357206adb9c7f2e3035f87c1acc03247d2d"),
    _s("S8", "cdi_s8_appendix_c_county", "2015", CDI_WRR + "Availability-and-Affordability-Report-Appendix-C.pdf", "2cc251d9c6b4a61cb5123bc5756f0d631b10f12f11aed521e97677fea16c43db"),
    _s("S9", "cfp_s9_pif_tiv", "FY2021-FY2025", CFP + "2025/11/CFP-5-yr-PIF-Zip-FY25-DWE-251114.pdf", "e3db28eb547415b169977a137749f06f7303bab862ff44ab6f3370f08ca5e624", True),
    _s("S9", "cfp_s9_pif_tiv", "FY2021-FY2025", CFP + "2025/11/CFP-5-yr-TIV-Zip-FY25-DWE-251114.pdf", "f6fd317921774a289c2951562331325881438cf7d3a186058c5eb73737ad38a7", True),
    _s("S9", "cfp_s9_pif_tiv", "FY2021-FY2025", CFP + "2025/11/CFP-5-yr-PIF-County-FY25-All-251114.pdf", "7c3b4286b2acfe21c525ce2fa9ec3348aac5c076b32a4efa48ecaf0ec9feb229", True),
    _s("S9", "cfp_s9_pif_tiv", "FY2021-FY2025", CFP + "2025/11/CFP-5-yr-TIV-County-FY25-All-251114.pdf", "4942539f88159b8e79d23e1f8a2a6d8b46625f8e77b85d0619b60f6aed5272a1", True),
    _s("S10", "cfp_s10_by_category", "2026-06-30", CFP + "2026/07/Policies-by-category-DWE-as-of-260630-DL-260717v001.pdf", "e3965c51ff4d84e8fdc8eb060319b108d69098b89ee651a0e6e0af82ba44e452", True),
    _s("S10", "cfp_s10_by_category", "2026-06-30", CFP + "2026/07/Premium-by-category-DWE-as-of-260630-DL-260717v001.pdf", "566e240aafe7d0e535cb6c9703c1f35640320f76bace09067752b25fa5490981", True),
    _s("S10", "cfp_s10_by_category", "2026-06-30", CFP + "2025/12/Reporting-Period-changes-2025-DL-251023.pdf", "a8335b18ce1a41164897a77d1dec79024df935903625c89c7d2584a237dae6c9", True),
)


class LayoutError(ValueError):
    pass


class UnitError(ValueError):
    pass


# ---------------------------------------------------------------- retrieval


def fetch(source: Source, refresh: bool = False) -> Path:
    """Download a public snapshot once and record it in the vintage's manifest.json; return its path.

    A hash that differs from the audited one goes to `<vintage>-<sha12>` so the audited bytes are never overwritten.
    """
    if source.restricted:
        raise PermissionError(f"{source.sid} is restricted; download it by hand (see restricted_instructions)")
    path = source.directory / source.filename
    if path.exists() and not refresh:
        bad = verify_manifest(path.parent) if (path.parent / "manifest.json").exists() else [path.name]
        if path.name not in bad:
            return path
    import requests  # only retrieval needs the network stack; parsers and tests stay offline

    resp = requests.get(source.url, timeout=300, headers={"User-Agent": "Mozilla/5.0 (nevada-county-housing-model)"})
    resp.raise_for_status()
    tmp = RAW / ".download"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(resp.content)
    digest = sha256(tmp)
    if digest != source.sha256:
        print(f"WARNING {source.sid} {source.filename}: sha256 {digest[:12]} differs from audited {source.sha256[:12]}; "
              "stored as a new vintage. Update SOURCES and docs/sources.md.", file=sys.stderr)
        path = source.directory.with_name(f"{source.vintage}-{digest[:12]}") / source.filename
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp.replace(path)
    write_manifest(path, source.url, CDI_TERMS)
    return path


def restricted_instructions() -> str:
    lines = [
        "Restricted files (no reuse grant). Download by hand in a browser, keep them out of git, and do not",
        "commit or publish values derived from them until the FAIR Plan grants written permission:",
    ]
    for s in SOURCES:
        if not s.restricted:
            continue
        dest = s.directory / s.filename
        if dest.exists():
            state = "present, hash matches" if sha256(dest) == s.sha256 else "present, HASH DIFFERS (new vintage)"
        else:
            state = "missing"
        lines.append(f"  {s.sid} {s.url}\n      -> {dest.relative_to(ROOT)} [{state}] expected sha256 {s.sha256}")
    lines.append(f"  Terms: {CFP_TERMS}")
    return "\n".join(lines)


# ---------------------------------------------------------------- readers (stdlib only: no openpyxl or pypdf)

_SML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref).group():
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_rows(path: Path, sheet: str = "xl/worksheets/sheet1.xml") -> list[list]:
    """Return the cell values of one worksheet as strings (None for blank cells)."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_SML + "si"):
                shared.append("".join(t.text or "" for t in si.iter(_SML + "t")))
        root = ET.fromstring(z.read(sheet))
    rows = []
    for r in root.iter(_SML + "row"):
        cells = {}
        for c in r.iter(_SML + "c"):
            v, t = c.find(_SML + "v"), c.get("t")
            if t == "inlineStr":
                cells[_col_index(c.get("r"))] = "".join(x.text or "" for x in c.iter(_SML + "t"))
            elif v is not None:
                cells[_col_index(c.get("r"))] = shared[int(v.text)] if t == "s" else v.text
        rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return rows


def pivot_fields(path: Path) -> list[tuple[str, list]]:
    """Return (field name, shared items) for each cache field of the first pivot cache."""
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("xl/pivotCache/pivotCacheDefinition1.xml"))
    fields = []
    for f in root.iter(_SML + "cacheField"):
        si = f.find(_SML + "sharedItems")
        items = [None if x.tag == _SML + "m" else x.get("v") for x in (si if si is not None else [])]
        fields.append((f.get("name").strip(), items))
    return fields


def pivot_records(path: Path, keep=None):
    """Yield pivot cache records as dicts; `keep(record)` filters before the dict is retained.

    The SB 824 values live only in the pivot cache (docs/sources.md S2), so the sheet cells are not read.
    """
    fields = pivot_fields(path)
    with zipfile.ZipFile(path) as z, z.open("xl/pivotCache/pivotCacheRecords1.xml") as fh:
        for _, elem in ET.iterparse(fh, events=("end",)):
            if elem.tag != _SML + "r":
                continue
            rec = {}
            for (name, items), x in zip(fields, elem):
                tag = x.tag[len(_SML):]
                if tag == "x":
                    rec[name] = items[int(x.get("v"))]
                elif tag == "m":
                    rec[name] = None
                else:
                    rec[name] = x.get("v")
            elem.clear()
            if keep is None or keep(rec):
                yield rec


_PDF_TOKEN = re.compile(rb"\((?:\\.|[^\\()]|\((?:\\.|[^\\()])*\))*\)|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[-+]?(?:\d+\.?\d*|\.\d+)|[A-Za-z'\"*]+")
_PDF_ESC = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}


def _pdf_string(tok: bytes) -> str:
    if tok.startswith(b"<"):
        return bytes.fromhex(re.sub(rb"\s", b"", tok[1:-1]).decode()).decode("cp1252", "replace")
    body, out, i = tok[1:-1], bytearray(), 0
    while i < len(body):
        ch = body[i:i + 1]
        if ch == b"\\":
            m = re.match(rb"[0-7]{1,3}", body[i + 1:i + 4])
            if m:
                out.append(int(m.group(), 8) & 0xFF)
                i += 1 + len(m.group())
                continue
            nxt = body[i + 1:i + 2]
            out += _PDF_ESC.get(nxt, nxt)
            i += 2
            continue
        out += ch
        i += 1
    return out.decode("cp1252", "replace")


def _pdf_objects(data: bytes) -> dict[int, tuple[bytes, bytes | None]]:
    """Map object number to (dictionary text, decoded stream or None), unpacking object streams."""
    objs: dict[int, tuple[bytes, bytes | None]] = {}
    for m in re.finditer(rb"(\d+)\s+\d+\s+obj\b", data):
        end = data.find(b"endobj", m.end())
        body = data[m.end():end]
        sm = re.search(rb"stream\r?\n", body)
        stream = None
        if sm:
            # Stream bytes can contain "endobj", so re-cut the object at "endstream".
            send = data.find(b"endstream", m.end() + sm.end())
            raw = data[m.end() + sm.end():send]
            body = body[:sm.start()]
            stream = raw
            if b"/FlateDecode" in body:
                try:
                    stream = zlib.decompressobj().decompress(raw)
                except zlib.error:
                    stream = None
        objs[int(m.group(1))] = (body, stream)
    for body, stream in list(objs.values()):
        if stream is None or not re.search(rb"/Type\s*/ObjStm", body):
            continue
        n = int(re.search(rb"/N\s+(\d+)", body).group(1))
        first = int(re.search(rb"/First\s+(\d+)", body).group(1))
        head = [int(x) for x in stream[:first].split()[: 2 * n]]
        offs = head[1::2] + [len(stream) - first]
        for i, num in enumerate(head[0::2]):
            objs.setdefault(num, (stream[first + offs[i]:first + offs[i + 1]], None))
    return objs


def pdf_lines(path: Path) -> list[str]:
    """Return text lines page by page, items on one baseline joined left to right.

    Handles simple-font PDFs with literal strings (the CDI S6 layout). Encrypted PDFs are rejected.
    """
    data = Path(path).read_bytes()
    if b"/Encrypt" in data:
        raise LayoutError(f"{path}: encrypted PDF is not supported")
    objs = _pdf_objects(data)
    lines = []
    for num in sorted(objs):
        body = objs[num][0]
        if not re.search(rb"/Type\s*/Page(?![a-zA-Z])", body):
            continue
        cm = re.search(rb"/Contents\s*(\[[^\]]*\]|\d+\s+\d+\s+R)", body)
        if not cm:
            continue
        refs = [int(r) for r in re.findall(rb"(\d+)\s+\d+\s+R", cm.group(1))]
        if len(refs) == 1 and objs.get(refs[0], (b"", None))[1] is None:  # indirect array of streams
            refs = [int(r) for r in re.findall(rb"(\d+)\s+\d+\s+R", objs[refs[0]][0])]
        content = b"\n".join(objs[r][1] or b"" for r in refs if r in objs)
        rows: dict[float, list] = {}
        for y, x, text in _pdf_text_items(content):
            key = next((k for k in rows if abs(k - y) <= 1.5), y)
            rows.setdefault(key, []).append((x, text))
        for y in sorted(rows, reverse=True):
            lines.append(re.sub(r"\s+", " ", " ".join(t for _, t in sorted(rows[y]))).strip())
    return lines


def _pdf_text_items(content: bytes) -> list[tuple[float, float, str]]:
    """Return (y, x, text) for each text-showing operator, positions from the text matrix."""
    items, stack = [], []
    tm = lm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    leading = 0.0
    for tok in _PDF_TOKEN.findall(content):
        if tok[:1] in b"(<[]/" or re.fullmatch(rb"[-+.\d]+", tok):
            stack.append(tok)
            continue
        op, nums = tok, [float(t) for t in stack if re.fullmatch(rb"[-+.\d]+", t)]
        if op == b"BT":
            tm = lm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        elif op == b"Tm" and len(nums) >= 6:
            tm = lm = nums[-6:]
        elif op in (b"Td", b"TD", b"T*", b"'", b'"'):
            tx, ty = nums[-2:] if op in (b"Td", b"TD") and len(nums) >= 2 else (0.0, -leading)
            if op == b"TD":
                leading = -ty
            a, b, c, d, e, f = lm
            tm = lm = [a, b, c, d, e + tx * a + ty * c, f + tx * b + ty * d]
        elif op == b"TL" and nums:
            leading = nums[-1]
        if op in (b"Tj", b"TJ", b"'", b'"'):
            text = "".join(_pdf_string(t) for t in stack if t[:1] in b"(<")
            if text.strip():
                items.append((round(tm[5], 1), tm[4], text))
        stack = []
    return items


# ---------------------------------------------------------------- layouts: one parser per layout

S3_SPLIT = ["county", "zip code", "year", "new", "renewed", "insured-initiated nonrenewed", "insurer-initiated nonrenewed"]
S3_TOTAL = ["county", "zip code", "year", "new", "renewed", "non-renewed"]
S2_FIELDS = {"Source", "Calendar Year", "Policy Type", "ZIP Code", "Avg Cov A", "Avg Cov C", "Earned Prem", "Earned Exp"}


def _header(row) -> list[str]:
    return [re.sub(r"\s+", " ", str(c)).strip().lower() for c in row if c is not None]


def detect_layout(path: Path) -> str:
    """Name the file layout from its structure, not its file name; raise LayoutError when unknown."""
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
        if "xl/pivotCache/pivotCacheDefinition1.xml" in names:
            fields = {name for name, _ in pivot_fields(path)}
            if S2_FIELDS <= fields:
                return "s2_sb824_pivot"
        else:
            rows = xlsx_rows(path)
            head = _header(rows[0]) if rows else []
            if head == S3_SPLIT:
                return "s3_nonrenewal_split"
            if head == S3_TOTAL:
                return "s3_nonrenewal_total"
    elif path.suffix.lower() == ".pdf":
        text = " ".join(pdf_lines(path)[:40])
        if re.search(r"Dwelling Units.{0,3} Insured in \d{4}", text) and "Voluntary Market" in text and "FAIR" in text:
            return "s6_fair_plan_share"
    raise LayoutError(f"unknown insurance file layout: {path.name}")


def _num(value) -> float:
    """Published number as float; blank or None is NaN, never 0."""
    if value is None:
        return np.nan
    s = str(value).replace(",", "").strip()
    if s == "":
        return np.nan
    if s == "-":
        return 0.0  # CDI prints a published zero as "-" (docs/sources.md S5, S9)
    return float(s)


def _reject_duplicates(df: pd.DataFrame, cols: list[str], what: str) -> None:
    dup = df.duplicated(cols, keep=False)
    if dup.any():
        raise DuplicateKeyError(f"{what}: {int(dup.sum())} rows share {cols}; first {df.loc[dup, cols].head(3).to_dict('records')}")


def parse_s2_sb824_pivot(path: Path, zips=TARGET_ZIPS) -> pd.DataFrame:
    """S2 `All Companies` slice: one row per (zip, year, S2 policy code) with earned premium and exposure in USD and house-years."""
    path = Path(path)
    zipset = None if zips is None else {normalize_zip(z) for z in zips}

    def keep(r):
        return r["Source"] == "All Companies" and r["Policy Type"] in S2_FORMS and (zipset is None or normalize_zip(r["ZIP Code"]) in zipset)

    recs = list(pivot_records(path, keep))
    df = pd.DataFrame(
        {
            "zip": [normalize_zip(r["ZIP Code"]) for r in recs],
            "year": [normalize_year(2000 + int(float(r["Calendar Year"]))) for r in recs],
            "s2_code": [r["Policy Type"] for r in recs],
            "earned_premium": [_num(r["Earned Prem"]) for r in recs],
            "earned_exposure": [_num(r["Earned Exp"]) for r in recs],
            "avg_coverage_a": [_num(r["Avg Cov A"]) for r in recs],
            "avg_coverage_c": [_num(r["Avg Cov C"]) for r in recs],
        }
    )
    _reject_duplicates(df, ["zip", "year", "s2_code"], path.name)
    return df


def _parse_s3(path: Path, columns: list[str], zips) -> pd.DataFrame:
    path = Path(path)
    rows = xlsx_rows(path)
    body = [r + [None] * (len(columns) - len(r)) for r in rows[1:] if r and any(c not in (None, "") for c in r)]
    raw = pd.DataFrame([r[: len(columns)] for r in body], columns=columns)
    raw["county"] = raw["county"].map(lambda v: (v or "").strip() or None)
    raw["zip"] = raw["zip"].map(normalize_zip)
    raw["year"] = raw["year"].map(lambda v: normalize_year(float(v)))
    for c in columns[3:]:
        raw[c] = raw[c].map(_num)
    if zips is not None:
        raw = raw[raw["zip"].isin({normalize_zip(z) for z in zips})]
    _reject_duplicates(raw, ["zip", "year"], path.name)
    return raw.reset_index(drop=True)


def parse_s3_nonrenewal_split(path: Path, zips=TARGET_ZIPS) -> pd.DataFrame:
    """S3 2015-2021 layout: nonrenewals split into insured- and insurer-initiated."""
    df = _parse_s3(path, ["county", "zip", "year", "policies_new", "policies_renewed", "nonrenewed_insured", "nonrenewed_insurer"], zips)
    df["nonrenewed_total"] = df["nonrenewed_insured"] + df["nonrenewed_insurer"]  # NaN if either part is missing
    df["s3_vintage"] = "2015-2021"
    return df


def parse_s3_nonrenewal_total(path: Path, zips=TARGET_ZIPS) -> pd.DataFrame:
    """S3 2020-2023 layout: one nonrenewal total; the split columns are not published (NaN)."""
    df = _parse_s3(path, ["county", "zip", "year", "policies_new", "policies_renewed", "nonrenewed_total"], zips)
    df["nonrenewed_insured"] = np.nan
    df["nonrenewed_insurer"] = np.nan
    df["s3_vintage"] = "2020-2023"
    return df


_S6_ROW = re.compile(r"^([A-Z][A-Z .'-]*?) (\d{5}) (.*?) ([\d, ]*\d|-) ([\d, ]*\d|-) (\d+(?:\.\d+)?) ?%$")


def parse_s6_fair_plan_share(path: Path, zips=TARGET_ZIPS) -> pd.DataFrame:
    """S6: voluntary and FAIR Plan dwelling units by ZIP for one year. ZIPs under 5 structures are omitted by CDI."""
    path = Path(path)
    lines = pdf_lines(path)
    year = int(re.search(r"Insured in (\d{4})", " ".join(lines[:40])).group(1))
    recs = []
    for line in lines:
        m = _S6_ROW.match(line)
        if m:
            county, z, _, vol, fp, pct = m.groups()
            recs.append((county.strip(), normalize_zip(z), _num(vol.replace(" ", "")), _num(fp.replace(" ", "")), float(pct) / 100))
    if not recs:
        raise LayoutError(f"{path.name}: no S6 rows parsed")
    df = pd.DataFrame(recs, columns=["county", "zip", "voluntary_dwelling_units", "fair_plan_dwelling_units", "published_share"])
    df["year"] = normalize_year(year)
    _reject_duplicates(df, ["zip", "year"], path.name)
    if zips is not None:
        df = df[df["zip"].isin({normalize_zip(z) for z in zips})]
    return df.reset_index(drop=True)


PARSERS = {
    "s2_sb824_pivot": parse_s2_sb824_pivot,
    "s3_nonrenewal_split": parse_s3_nonrenewal_split,
    "s3_nonrenewal_total": parse_s3_nonrenewal_total,
    "s6_fair_plan_share": parse_s6_fair_plan_share,
}


def parse(path: Path, zips=TARGET_ZIPS) -> tuple[str, pd.DataFrame]:
    layout = detect_layout(path)
    return layout, PARSERS[layout](path, zips=zips)


# ---------------------------------------------------------------- rates and checks


def safe_rate(numerator, denominator):
    """numerator / denominator, NaN when either is missing or the denominator is not positive."""
    num = pd.to_numeric(pd.Series(numerator), errors="coerce").astype(float)
    den = pd.to_numeric(pd.Series(denominator), errors="coerce").astype(float)
    out = num / den.where(den > 0)
    return out.to_numpy() if not np.isscalar(numerator) else float(out.iloc[0])


def total_policies(df: pd.DataFrame, forms, column: str = "exposures") -> pd.Series:
    """Sum `column` over policy forms per (zcta, year). FAIR Plan and supplemental cover the same dwellings, so summing both is refused."""
    forms = set(forms)
    if {"fair_plan", "supplemental"} <= forms:
        raise ValueError("fair_plan and supplemental policies insure the same dwellings; do not sum them")
    sub = df[df["policy_form"].isin(forms)]
    units = sorted(sub["exposure_unit"].dropna().unique())
    if len(units) > 1:
        raise UnitError(f"cannot sum exposures in different units: {units}")
    return sub.groupby(["zcta", "year"])[column].sum(min_count=1)


def check_dollar_units(df: pd.DataFrame) -> None:
    """Raise UnitError if average premiums look like thousands of dollars (or cents) instead of dollars."""
    rows = df[(df["exposures"] >= 5) & df["avg_premium_per_exposure"].notna()]
    if rows.empty:
        return
    med = rows["avg_premium_per_exposure"].median()
    if not 100 <= med <= 20_000:
        raise UnitError(f"median premium per house-year is {med:.4g}; expected dollars (100-20,000)")


# ---------------------------------------------------------------- panel


COLUMNS = [
    "zcta", "year", "policy_form", "zip_source", "zcta_method", "xw_weight", "sources",
    "exposure_unit", "exposures", "premium_basis", "written_premium", "earned_premium",
    "avg_premium_per_exposure", "avg_coverage_a", "avg_coverage_c", "coverage_basis",
    "premium_per_1000_coverage_a", "form_share_of_admitted", "owner_occupied_share",
    "policies_new", "policies_renewed", "nonrenewed_total", "nonrenewed_insurer", "nonrenewed_insured",
    "nonrenewal_rate", "nonrenewal_scope", "s3_vintage", "series_break",
    "voluntary_dwelling_units", "fair_plan_dwelling_units", "fair_plan_share",
    "denominator_source", "suppressed", "coverage_def_change", "mix_shift", "combined_premium_unavailable",
]


def _s2_rows(s2: pd.DataFrame) -> pd.DataFrame:
    if s2.empty:
        return pd.DataFrame(columns=["zip", "year", "policy_form"])
    _reject_duplicates(s2, ["zip", "year", "s2_code"], "S2 rows")
    d = s2[s2["s2_code"].isin(S2_FORMS)].copy()
    d["policy_form"] = d["s2_code"].map(S2_FORMS)
    # Averages are exposure-weighted when DO and DT are combined; the weight is in the same source and row.
    for c in ("avg_coverage_a", "avg_coverage_c"):
        d[c + "_x"] = d[c] * d["earned_exposure"]
    d["owner_exp"] = d["earned_exposure"].where(d["s2_code"] == "DO", 0.0)
    g = d.groupby(["zip", "year", "policy_form"], as_index=False).agg(
        earned_premium=("earned_premium", lambda s: s.sum(min_count=1)),
        exposures=("earned_exposure", lambda s: s.sum(min_count=1)),
        cov_a_x=("avg_coverage_a_x", lambda s: s.sum(min_count=1)),
        cov_c_x=("avg_coverage_c_x", lambda s: s.sum(min_count=1)),
        owner_exp=("owner_exp", "sum"),
    )
    g["avg_premium_per_exposure"] = safe_rate(g["earned_premium"], g["exposures"])
    g["avg_coverage_a"] = safe_rate(g["cov_a_x"], g["exposures"])
    g["avg_coverage_c"] = safe_rate(g["cov_c_x"], g["exposures"])
    g["premium_per_1000_coverage_a"] = safe_rate(g["avg_premium_per_exposure"] * 1000, g["avg_coverage_a"])
    g["owner_occupied_share"] = np.where(g["policy_form"] == "dwelling_fire", safe_rate(g["owner_exp"], g["exposures"]), np.nan)
    admitted = g.groupby(["zip", "year"])["exposures"].transform(lambda s: s.sum(min_count=1))
    g["form_share_of_admitted"] = safe_rate(g["exposures"], admitted)
    g["coverage_basis"] = g["year"].map(lambda y: S2_COVERAGE_BASIS.get(y, "start_of_year"))
    g["exposure_unit"] = "earned_house_years"
    g["premium_basis"] = "earned"
    g["s2"] = True
    return g.drop(columns=["cov_a_x", "cov_c_x", "owner_exp"])


def _s3_rows(s3_frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Use the 2020-2023 vintage for 2020 on and the 2015-2021 vintage before 2020 (docs/sources.md S3)."""
    parts = []
    for df in s3_frames:
        if df.empty:
            continue
        v = df["s3_vintage"].iloc[0]
        parts.append(df[df["year"] >= 2020] if v == "2020-2023" else df[df["year"] < 2020])
    if not parts:
        return pd.DataFrame(columns=["zip", "year", "policy_form"])
    d = pd.concat(parts, ignore_index=True)
    _reject_duplicates(d, ["zip", "year"], "S3 combined vintages")
    d["policy_form"] = "admitted_homeowners"
    d["nonrenewal_rate"] = safe_rate(d["nonrenewed_total"], d["policies_renewed"] + d["nonrenewed_total"])
    d["nonrenewal_scope"] = "voluntary_market_all_forms"
    d["series_break"] = d["year"] == 2020
    d["s3"] = True
    return d.drop(columns=["county"])


def _s6_rows(s6: pd.DataFrame, s3_zip_years: set) -> pd.DataFrame:
    d = s6.copy()
    d["policy_form"] = "fair_plan"
    d["fair_plan_share"] = safe_rate(d["fair_plan_dwelling_units"], d["voluntary_dwelling_units"] + d["fair_plan_dwelling_units"])
    off = (d["fair_plan_share"] - d["published_share"]).abs() > S6_PCT_TOLERANCE
    if off.any():
        raise UnitError(f"S6 computed FAIR Plan share disagrees with the published percent: {d.loc[off, 'zip'].tolist()}")
    d["exposures"] = d["fair_plan_dwelling_units"]
    d["exposure_unit"] = "dwelling_units"
    d["suppressed"] = False
    # CDI omits ZIPs with fewer than 5 insured structures; a ZIP active in S3 that year but absent here is suppressed.
    years = set(d["year"])
    have = set(zip(d["zip"], d["year"]))
    missing = [(z, y) for z, y in sorted(s3_zip_years) if y in years and (z, y) not in have]
    if missing:
        d = pd.concat([d, pd.DataFrame({"zip": [z for z, _ in missing], "year": [y for _, y in missing],
                                        "policy_form": "fair_plan", "exposure_unit": "dwelling_units", "suppressed": True})],
                      ignore_index=True)
    d["s6"] = True
    return d.drop(columns=["county", "published_share"])


def build_panel(s2: pd.DataFrame, s3_frames: list[pd.DataFrame], s6: pd.DataFrame | None, zctas=TARGET_ZCTAS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Combine parsed sources into (panel keyed on KEY, unmatched ZIP rows)."""
    a = _s2_rows(s2)
    b = _s3_rows(s3_frames)
    rows = a.merge(b, on=["zip", "year", "policy_form"], how="outer")
    if s6 is not None and not s6.empty:
        s3_zy = set(zip(b["zip"], b["year"])) if not b.empty else set()
        rows = pd.concat([rows, _s6_rows(s6, s3_zy)], ignore_index=True)
    for c in COLUMNS:
        if c not in rows.columns and c not in ("zcta", "zip_source", "zcta_method", "xw_weight"):
            rows[c] = np.nan
    for flag in ("s2", "s3", "s6"):
        rows[flag] = rows[flag].eq(True) if flag in rows else False

    rows["sources"] = [";".join(s for s, f in (("S2", a2), ("S3", a3), ("S6", a6)) if f)
                       for a2, a3, a6 in zip(rows["s2"], rows["s3"], rows["s6"])]
    rows["denominator_source"] = [_denominators(r) for r in rows.itertuples()]

    rows["suppressed"] = rows["suppressed"].eq(True)
    rows["series_break"] = rows["series_break"].eq(True)
    rows["coverage_def_change"] = rows["s2"] & rows["year"].eq(max(S2_COVERAGE_BASIS) + 1)
    rows["combined_premium_unavailable"] = rows["policy_form"].isin(["fair_plan", "supplemental"]) | rows["earned_premium"].isna()
    rows = rows.sort_values(["zip", "policy_form", "year"]).reset_index(drop=True)
    rows["mix_shift"] = _mix_shift(rows)

    matched, unmatched = map_zip_to_zcta(rows, zctas=zctas, zip_col="zip")
    matched = matched.rename(columns={"zip": "zip_source"})
    if not matched.empty and not set(matched["policy_form"]) <= set(POLICY_FORMS):
        raise ValueError(f"unexpected policy_form values: {set(matched['policy_form']) - set(POLICY_FORMS)}")
    assert_unique_keys(matched, KEY)
    check_dollar_units(matched)
    matched = matched[COLUMNS].sort_values(list(KEY)).reset_index(drop=True)
    unmatched = unmatched[["zip"] + [c for c in COLUMNS if c in unmatched.columns]]
    return matched, unmatched


def _denominators(r) -> str:
    out = []
    if pd.notna(r.avg_premium_per_exposure):
        out.append("avg_premium_per_exposure=S2 All Companies earned exposure")
    if pd.notna(r.premium_per_1000_coverage_a):
        out.append(f"premium_per_1000_coverage_a=S2 avg Coverage A ({r.coverage_basis})")
    if pd.notna(r.nonrenewal_rate):
        out.append(f"nonrenewal_rate=S3 {r.s3_vintage} renewed+nonrenewed")
    if pd.notna(r.fair_plan_share):
        out.append("fair_plan_share=S6 voluntary+FAIR Plan dwelling units")
    return "; ".join(out)


def _mix_shift(rows: pd.DataFrame) -> np.ndarray:
    """True when a form's share of admitted exposure, or the DF owner-occupied share, moves past the threshold year on year."""
    g = rows.groupby(["zip", "policy_form"])
    prev_year = g["year"].shift()
    consecutive = rows["year"] - prev_year == 1
    d_form = (rows["form_share_of_admitted"] - g["form_share_of_admitted"].shift()).abs()
    d_occ = (rows["owner_occupied_share"] - g["owner_occupied_share"].shift()).abs()
    return (consecutive & ((d_form > MIX_SHIFT_FORM_SHARE) | (d_occ > MIX_SHIFT_OCCUPANCY))).to_numpy()


def _report_unmatched(unmatched: pd.DataFrame) -> None:
    if unmatched.empty:
        print("unmatched ZIP rows: 0")
        return
    additive = ["exposures", "earned_premium", "policies_new", "policies_renewed", "nonrenewed_total", "fair_plan_dwelling_units"]
    sums = {c: float(unmatched[c].sum(min_count=1)) for c in additive if c in unmatched}
    print(f"unmatched ZIP rows: {len(unmatched)} (ZIPs {sorted(unmatched['zip'].unique())}); sums {sums}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-download public snapshots even if the manifest verifies")
    args = ap.parse_args(argv)

    paths = {}
    for s in SOURCES:
        if not s.restricted:
            paths.setdefault(s.sid, []).append(fetch(s, refresh=args.refresh))
    print(restricted_instructions())

    parsed: dict[str, list[pd.DataFrame]] = {}
    for sid in ("S2", "S3", "S6"):
        for p in paths[sid]:
            if p.suffix.lower() in (".xlsx", ".pdf"):
                layout, df = parse(p)
                print(f"{sid} {p.name}: layout {layout}, {len(df)} target rows")
                parsed.setdefault(sid, []).append(df)
    panel, unmatched = build_panel(parsed["S2"][0], parsed["S3"], parsed["S6"][0])
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUTPUT, index=False)
    UNMATCHED.parent.mkdir(parents=True, exist_ok=True)
    unmatched.to_csv(UNMATCHED, index=False)
    print(f"wrote {OUTPUT.relative_to(ROOT)}: {len(panel)} rows")
    _report_unmatched(unmatched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
