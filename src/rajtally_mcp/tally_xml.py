"""The one place that talks to Tally over its XML/HTTP interface.

Everything else in this package builds on four calls:

* ``collection(...)``  - read any list of objects (ledgers, vouchers, stock items ...)
* ``native_report(...)`` - export one of Tally's own reports (Trial Balance, GSTR-1 ...)
* ``import_data(...)`` - create / alter / delete masters and vouchers
* ``evaluate(...)``    - evaluate a TDL function such as ``$$LicenseInfo``

plus the small helpers for dates, amounts and quantities that Tally formats
in its own way.
"""

from __future__ import annotations

import datetime as dt
import re
import threading
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable
from typing import Any
from xml.sax.saxutils import escape

from mcp.server.mcpserver.exceptions import ResourceError, ToolError

from . import config


class TallyError(ToolError, ResourceError):
    """Anything that went wrong while talking to Tally, in plain words.

    Being a ToolError / ResourceError is what lets the MCP layer pass the message on to the
    assistant instead of hiding it as an unexpected crash.
    """


# Tally answers one request at a time; serialise ours so we never overlap.
_request_lock = threading.Lock()
# Tally is always on this machine or the local network: never send its data through a web proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --------------------------------------------------------------------------
# HTTP transport
# --------------------------------------------------------------------------

def post(xml: str, timeout: int | None = None) -> str:
    """Send one XML request to Tally and return the response text."""
    cfg = config.get()
    enc = "utf-16-le" if cfg.encoding.lower().replace("_", "-") in ("utf-16", "utf-16-le", "utf16") else "utf-8"
    charset = "utf-16" if enc == "utf-16-le" else "utf-8"
    body = xml.encode(enc)
    request = urllib.request.Request(
        cfg.url,
        data=body,
        method="POST",
        headers={"Content-Type": f"text/xml;charset={charset}", "Content-Length": str(len(body))},
    )
    try:
        with _request_lock, _opener.open(request, timeout=timeout or cfg.timeout) as response:
            raw = response.read()
    except urllib.error.URLError as exc:
        raise TallyError(
            f"Cannot reach Tally at {cfg.url} ({exc.reason}). Check that Tally is open, a company is loaded, "
            "and the HTTP server is enabled (F1 Help > Settings > Connectivity > Client/Server configuration: "
            f"'TallyPrime acts as' = Both, Port = {cfg.port})."
        ) from exc
    except TimeoutError as exc:
        raise TallyError(f"Tally did not answer within {timeout or cfg.timeout}s. Try a shorter date range.") from exc
    return decode(raw)


def ping() -> str:
    """GET the Tally port. Returns Tally's banner, e.g. 'TallyPrime Server is Running'."""
    cfg = config.get()
    try:
        with _request_lock, _opener.open(cfg.url, timeout=min(cfg.timeout, 10)) as response:
            text = decode(response.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TallyError(
            f"Cannot reach Tally at {cfg.url} ({getattr(exc, 'reason', exc)}). Open Tally, load a company and "
            "enable the HTTP server (F1 Help > Settings > Connectivity)."
        ) from exc
    return re.sub(r"<[^>]+>", "", text).strip()


def decode(raw: bytes) -> str:
    """Tally replies in UTF-16 or UTF-8 depending on the request; detect which."""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    if len(raw) >= 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le", errors="replace")
    return raw.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# XML parsing
# --------------------------------------------------------------------------

_CONTROL_REF = re.compile(r"&#(x[0-9a-fA-F]+|\d+);")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(text: str) -> str:
    """Remove the control-character references Tally emits (e.g. ``&#4; Primary``)."""

    def fix(match: re.Match[str]) -> str:
        ref = match.group(1)
        code = int(ref[1:], 16) if ref.lower().startswith("x") else int(ref)
        return "" if code < 32 and code not in (9, 10, 13) else match.group(0)

    return _CONTROL_CHARS.sub("", _CONTROL_REF.sub(fix, text))


def parse(text: str) -> ET.Element:
    """Parse a Tally response and raise a clear error if Tally reported one."""
    cleaned = _clean(text).strip()
    if not cleaned:
        raise TallyError("Tally returned an empty response. Is a company loaded?")
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError:
        body = re.sub(r"<\?xml[^>]*\?>", "", cleaned)
        try:  # some exports contain several top-level elements
            root = ET.fromstring(f"<ROOT>{body}</ROOT>")
        except ET.ParseError as exc:
            raise TallyError(f"Tally returned something that is not XML: {cleaned[:300]}") from exc
    if root.tag == "RESPONSE" and (root.text or "").strip() and len(root) == 0:
        raise TallyError(f"Tally said: {root.text.strip()}")
    status = root.findtext("HEADER/STATUS")
    errors = [e.text.strip() for e in root.iter("LINEERROR") if e.text and e.text.strip()]
    if status == "0" and root.find(".//IMPORTRESULT") is None:
        raise TallyError("Tally rejected the request: " + ("; ".join(errors) or "no reason given"))
    return root


def text_of(element: ET.Element | None, tag: str, default: str = "") -> str:
    if element is None:
        return default
    found = element.find(tag)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def to_dict(element: ET.Element) -> Any:
    """Turn any Tally XML element into plain JSON-friendly data.

    ``X.LIST`` children become lists, empty values are dropped, and the
    ``NAME`` attribute is kept as ``NAME``.
    """
    children = list(element)
    if not children:
        return (element.text or "").strip()
    out: dict[str, Any] = {}
    if element.get("NAME"):
        out["NAME"] = element.get("NAME")
    for child in children:
        value = to_dict(child)
        if value in ("", {}, []):
            continue
        key = child.tag
        if key == "NAME" and out.get("NAME") == value:
            continue  # the name is already there from the NAME attribute
        if key.endswith(".LIST"):
            out.setdefault(key[:-5], []).append(value)
        elif key in out:
            if not isinstance(out[key], list):
                out[key] = [out[key]]
            out[key].append(value)
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------
# Dates, amounts, quantities
# --------------------------------------------------------------------------

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_date(value: str | dt.date | None) -> dt.date | None:
    """Accept 2024-04-01, 01-04-2024, 01/04/2024, 20240401, 1-Apr-2024, 1-Apr-24."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        return dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if match:
        return dt.date(int(match[1]), int(match[2]), int(match[3]))
    match = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2,4})", text)
    if match:
        year = int(match[3])
        return dt.date(year + 2000 if year < 100 else year, int(match[2]), int(match[1]))
    match = re.fullmatch(r"(\d{1,2})[-/. ]([A-Za-z]{3,9})[-/. ](\d{2,4})", text)
    if match and match[2][:3].lower() in _MONTHS:
        year = int(match[3])
        return dt.date(year + 2000 if year < 100 else year, _MONTHS[match[2][:3].lower()], int(match[1]))
    raise TallyError(f"Could not understand the date '{value}'. Use YYYY-MM-DD, e.g. 2024-04-01.")


def tally_date(value: str | dt.date | None) -> str:
    """Tally's wire format: YYYYMMDD."""
    parsed = parse_date(value)
    return parsed.strftime("%Y%m%d") if parsed else ""


def iso_date(value: str | None) -> str:
    """Tally date text -> ISO date, or '' if blank/unreadable."""
    if not value:
        return ""
    try:
        parsed = parse_date(value)
    except TallyError:
        return value
    return parsed.isoformat() if parsed else ""


def financial_year(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The Indian financial year (1 April - 31 March) containing ``today``."""
    today = today or dt.date.today()
    start_year = today.year if today.month >= 4 else today.year - 1
    return dt.date(start_year, 4, 1), dt.date(start_year + 1, 3, 31)


def period(from_date: str | None, to_date: str | None) -> tuple[dt.date, dt.date]:
    """Resolve a period. Blank = current financial year. 'FY2024-25' / '2024-25' also work."""
    for value in (from_date, to_date):
        match = re.fullmatch(r"(?:FY)?\s*(\d{4})\s*-\s*(\d{2}|\d{4})", str(value or "").strip(), re.IGNORECASE)
        if match:
            start = int(match[1])
            return dt.date(start, 4, 1), dt.date(start + 1, 3, 31)
    first = parse_date(from_date) or financial_year()[0]
    last = parse_date(to_date) or financial_year(first)[1]
    if last < first:
        raise TallyError(f"to_date {last} is before from_date {first}.")
    return first, last


_NUMBER = re.compile(r"-?\(?\d[\d,]*\.?\d*\)?")


def amount(value: str | None) -> Decimal:
    """Tally amount text -> Decimal. Handles commas, brackets, Dr/Cr and forex ('$ 10 @ 83/$ = 830')."""
    if value is None:
        return Decimal(0)
    text = str(value).strip()
    if not text:
        return Decimal(0)
    whole = text
    if "=" in text:  # forex: the base-currency value follows the last '='
        text = text.rsplit("=", 1)[1].strip()
    negative = text.startswith(("-", "(-")) or whole.startswith(("-", "(-"))
    numbers = _NUMBER.findall(text)
    if not numbers:
        return Decimal(0)
    raw = numbers[0].replace(",", "").replace("(", "").replace(")", "")
    try:
        number = abs(Decimal(raw))
    except InvalidOperation:
        return Decimal(0)
    if re.search(r"\bDr\b", text):
        return -number
    if re.search(r"\bCr\b", text):
        return number
    return -number if negative else number


def quantity(value: str | None) -> tuple[Decimal, str]:
    """' 10 Nos' -> (10, 'Nos'). Compound quantities keep the first part."""
    text = (value or "").strip()
    if not text:
        return Decimal(0), ""
    first = text.split("=")[0].strip()
    match = re.match(r"(-?[\d,]*\.?\d+)\s*(.*)", first)
    if not match:
        return Decimal(0), first
    return Decimal(match[1].replace(",", "")), match[2].strip()


def rate(value: str | None) -> tuple[Decimal, str]:
    """'100.00/Nos' -> (100.00, 'Nos')."""
    text = (value or "").strip()
    if not text:
        return Decimal(0), ""
    if "=" in text:
        text = text.rsplit("=", 1)[1].strip()
    number, _, unit = text.partition("/")
    return amount(number), unit.strip()


def money(value: Decimal | float | int | str) -> float:
    """Round to paise and return a plain float for JSON output."""
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def dr_cr(value: Decimal) -> dict[str, float]:
    """Split a signed Tally amount (debit is negative) into dr / cr columns."""
    return {"dr": money(-value) if value < 0 else 0.0, "cr": money(value) if value > 0 else 0.0}


def yes(value: str | None) -> bool:
    return (value or "").strip().lower() in ("yes", "true", "1")


# --------------------------------------------------------------------------
# Request builders
# --------------------------------------------------------------------------

def _static_variables(company: str | None, from_date: Any = None, to_date: Any = None,
                      extra: dict[str, str] | None = None) -> str:
    cfg = config.get()
    parts = ["<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"]
    name = company if company is not None else cfg.company
    if name:
        parts.append(f"<SVCURRENTCOMPANY>{escape(name)}</SVCURRENTCOMPANY>")
    if from_date:
        parts.append(f"<SVFROMDATE TYPE=\"Date\">{tally_date(from_date)}</SVFROMDATE>")
    if to_date:
        parts.append(f"<SVTODATE TYPE=\"Date\">{tally_date(to_date)}</SVTODATE>")
    for key, value in (extra or {}).items():
        parts.append(f"<{key}>{escape(str(value))}</{key}>")
    return "<STATICVARIABLES>" + "".join(parts) + "</STATICVARIABLES>"


def tdl_string(value: str) -> str:
    """Quote a value for use inside a TDL formula."""
    return '"' + str(value).replace('"', "'") + '"'


def collection_xml(
    object_type: str,
    fields: Iterable[str] = (),
    *,
    filters: dict[str, str] | None = None,
    child_of: str | None = None,
    belongs_to: bool = True,
    compute: dict[str, str] | None = None,
    company: str | None = None,
    from_date: Any = None,
    to_date: Any = None,
    name: str = "RajTallyCollection",
) -> str:
    """Build a TDL collection request.

    ``filters`` maps a filter name to a TDL formula (all must be true).
    ``fields`` may contain ``*`` to fetch every stored field.
    """
    fields = [f for f in fields if f]
    parts = [f"<TYPE>{escape(object_type)}</TYPE>"]
    if child_of:
        parts.append(f"<CHILDOF>{escape(child_of)}</CHILDOF>")
        if belongs_to:
            parts.append("<BELONGSTO>Yes</BELONGSTO>")
    named = [f for f in fields if f != "*"]
    if "*" in fields:
        parts.append("<NATIVEMETHOD>*</NATIVEMETHOD>")
    if named:
        parts.append(f"<FETCH>{escape(', '.join(named))}</FETCH>")
    for key, formula in (compute or {}).items():
        parts.append(f"<COMPUTE>{escape(key)} : {escape(formula)}</COMPUTE>")
    formulae = []
    for key, formula in (filters or {}).items():
        parts.append(f"<FILTER>{escape(key)}</FILTER>")
        formulae.append(f'<SYSTEM TYPE="Formulae" NAME="{escape(key)}">{escape(formula)}</SYSTEM>')
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
        f"<TYPE>Collection</TYPE><ID>{escape(name)}</ID></HEADER><BODY><DESC>"
        + _static_variables(company, from_date, to_date)
        + f'<TDL><TDLMESSAGE><COLLECTION NAME="{escape(name)}" ISMODIFY="No">'
        + "".join(parts)
        + "</COLLECTION>"
        + "".join(formulae)
        + "</TDLMESSAGE></TDL></DESC></BODY></ENVELOPE>"
    )


def collection(object_type: str, fields: Iterable[str] = (), **options: Any) -> list[ET.Element]:
    """Run a collection request and return the object elements."""
    root = parse(post(collection_xml(object_type, fields, **options)))
    holder = root.find(".//COLLECTION")
    if holder is None:
        holder = root.find(".//DATA")
    if holder is None:
        holder = root
    skip = {"HEADER", "BODY", "DESC", "CMPINFO", "DATA"}
    return [child for child in holder if child.tag not in skip]


def report_xml(report: str, *, company: str | None = None, from_date: Any = None, to_date: Any = None,
               variables: dict[str, str] | None = None) -> str:
    extra = {key.upper(): value for key, value in (variables or {}).items()}
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
        f"<TYPE>Data</TYPE><ID>{escape(report)}</ID></HEADER><BODY><DESC>"
        + _static_variables(company, from_date, to_date, extra)
        + "</DESC></BODY></ENVELOPE>"
    )


def native_report(report: str, **options: Any) -> ET.Element:
    """Export one of Tally's own reports by its name."""
    return parse(post(report_xml(report, **options)))


def flat_rows(root: ET.Element) -> list[dict[str, str]]:
    """Turn Tally's 'display' report XML into rows.

    Native reports come back as a flat run of siblings (name, amount, name,
    amount ...). A new row starts each time the first tag repeats.
    """
    holder = root
    for path in ("BODY/DATA", "BODY", "DATA"):
        found = root.find(path)
        if found is not None and len(found):
            holder = found
            break
    children = [c for c in holder if c.tag not in ("HEADER", "DESC", "CMPINFO")]
    if len(children) == 1 and len(children[0]):
        children = list(children[0])
    rows: list[dict[str, str]] = []
    current: dict[str, str] = {}
    first_tag = children[0].tag if children else None
    for child in children:
        if child.tag == first_tag and current:
            rows.append(current)
            current = {}
        for leaf in child.iter():
            if len(leaf) == 0 and leaf.text and leaf.text.strip():
                key = leaf.tag
                suffix = 2
                while key in current:
                    key = f"{leaf.tag}_{suffix}"
                    suffix += 1
                current[key] = leaf.text.strip()
    if current:
        rows.append(current)
    return rows


def import_xml(payload: str, kind: str, company: str | None = None) -> str:
    """Wrap master / voucher XML in an import envelope. ``kind`` is 'All Masters' or 'Vouchers'."""
    cfg = config.get()
    name = company if company is not None else cfg.company
    statics = f"<SVCURRENTCOMPANY>{escape(name)}</SVCURRENTCOMPANY>" if name else ""
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Import</TALLYREQUEST>"
        f"<TYPE>Data</TYPE><ID>{kind}</ID></HEADER><BODY><DESC><STATICVARIABLES>{statics}"
        "</STATICVARIABLES></DESC><DATA><TALLYMESSAGE xmlns:UDF=\"TallyUDF\">"
        + payload
        + "</TALLYMESSAGE></DATA></BODY></ENVELOPE>"
    )


def import_result(text: str) -> dict[str, Any]:
    """Read Tally's import counters and line errors."""
    root = parse(text)
    result: dict[str, Any] = {}
    for tag in ("CREATED", "ALTERED", "DELETED", "CANCELLED", "COMBINED", "IGNORED", "ERRORS", "EXCEPTIONS"):
        found = next(root.iter(tag), None)
        if found is not None and (found.text or "").strip().lstrip("-").isdigit():
            result[tag.lower()] = int(found.text.strip())
    for tag, key in (("LASTVCHID", "last_voucher_id"), ("LASTMID", "last_master_id")):
        found = next(root.iter(tag), None)
        if found is not None and (found.text or "").strip() not in ("", "0"):
            result[key] = found.text.strip()
    errors = [e.text.strip() for e in root.iter("LINEERROR") if e.text and e.text.strip()]
    if errors:
        result["line_errors"] = errors
    changed = sum(result.get(k, 0) for k in ("created", "altered", "deleted", "cancelled", "combined"))
    result["ok"] = changed > 0 and not errors and result.get("errors", 0) == 0
    if not result["ok"] and not errors:
        result["note"] = (
            "Tally accepted the request but changed nothing. Usual causes: a ledger/item name that does not "
            "exist, a date outside the company's books period, or an object that could not be found."
        )
    return result


def import_data(payload: str, kind: str, company: str | None = None) -> dict[str, Any]:
    return import_result(post(import_xml(payload, kind, company)))


def evaluate(function: str, params: Iterable[str] = (), company: str | None = None) -> str:
    """Evaluate a TDL function, e.g. ``$$LicenseInfo`` with param ``IsEducationalMode``."""
    name = function if function.startswith("$$") else "$$" + function
    plist = "".join(f"<PARAM>{escape(str(p))}</PARAM>" for p in params)
    xml = (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><TALLYREQUEST>Export</TALLYREQUEST>"
        f"<TYPE>Function</TYPE><ID>{escape(name)}</ID></HEADER><BODY><DESC>"
        + _static_variables(company)
        + f"<FUNCPARAMLIST>{plist}</FUNCPARAMLIST></DESC></BODY></ENVELOPE>"
    )
    root = parse(post(xml))
    found = next(root.iter("RESULT"), None)
    return (found.text or "").strip() if found is not None else ""


# --------------------------------------------------------------------------
# dict -> Tally XML (used by every write tool)
# --------------------------------------------------------------------------

def to_xml(tag: str, value: Any, attrs: dict[str, str] | None = None) -> str:
    """Turn plain data into Tally XML.

    * dict  -> nested element (keys become UPPERCASE tags)
    * list  -> the tag repeated (use keys ending in ``.LIST`` for Tally lists)
    * bool  -> Yes / No
    * date  -> YYYYMMDD
    """
    tag = tag.upper()
    attributes = "".join(f' {k}="{escape(str(v), {chr(34): "&quot;"})}"' for k, v in (attrs or {}).items())
    if isinstance(value, list):
        return "".join(to_xml(tag, item) for item in value)
    if isinstance(value, dict):
        inner = "".join(to_xml(k, v) for k, v in value.items() if v is not None)
        return f"<{tag}{attributes}>{inner}</{tag}>"
    if isinstance(value, bool):
        text = "Yes" if value else "No"
    elif isinstance(value, (dt.date, dt.datetime)):
        text = tally_date(value)
    elif value is None:
        text = ""
    else:
        text = escape(str(value))
    return f"<{tag}{attributes}>{text}</{tag}>"
