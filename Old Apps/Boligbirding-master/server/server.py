import os
import io
import json
import time
import asyncio
import datetime
import secrets
import math
import hashlib
import shutil
import requests
import pandas as pd
from dotenv import load_dotenv
import re

from typing import Optional, Dict, Any, List, Tuple
from collections import defaultdict

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Body, Query, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from html import escape, unescape
from passlib.context import CryptContext

SAFE_OBSERKODE_RE = re.compile(r"^[A-Z0-9]{2,16}$")
CSRF_HEADER = "x-csrf-token"
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, String, Date, Integer, Text, select, func, text

from starlette.middleware.sessions import SessionMiddleware


# ---------------------------------------------------------
#  App & Database
# ---------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SUPERADMIN = os.environ.get("SUPERADMIN", "")
SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "")
DATABASE_URL = os.environ.get("DATABASE_URL")

engine = create_async_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
Base = declarative_base()

SERVER_DIR = os.path.dirname(os.path.abspath(__file__))    # .../Boligbirding/server
ROOT_DIR   = os.path.dirname(SERVER_DIR)                    # .../Boligbirding
WEB_DIR    = os.path.join(ROOT_DIR, "web")                 # .../Boligbirding/web

app = FastAPI()
SESSION_SECRET = os.environ.get("ADMIN_SECRET")
if not SESSION_SECRET:
    # Gør det eksplicit at produktion *kræver* en konstant nøgle
    raise RuntimeError("ADMIN_SECRET mangler. Sæt en fælles, stærk nøgle i miljøet for alle workers.")

app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie=os.environ.get("SESSION_COOKIE", "bb_session"),
    same_site="lax",            # eller "strict" hvis det passer din frontend
    https_only=bool(int(os.environ.get("SESSION_HTTPS_ONLY", "1"))),
    max_age=60*60*24*30,        # 30 dage - tilpas efter behov
)
# ---------------------------------------------------------
#  Models
# ---------------------------------------------------------
class Observation(Base):
    __tablename__ = "observations"
    id         = Column(Integer, primary_key=True, index=True)
    obserkode  = Column(String, index=True)
    artnavn    = Column(String, index=True)
    antal      = Column(Integer, nullable=True)
    dato       = Column(Date)
    turid      = Column(String, index=True)
    obsid      = Column(String, index=True, nullable=True)
    turtidfra  = Column(String, nullable=True)
    turtidtil  = Column(String, nullable=True)
    turnoter   = Column(String, nullable=True)
    afdeling   = Column(String, nullable=True)
    loknavn    = Column(String, nullable=True)
    loknr      = Column(Integer, nullable=True, index=True)

class Lokation(Base):
    __tablename__ = "lokationer"
    id = Column(Integer, primary_key=True)
    site_number = Column(Integer, index=True)
    site_name = Column(String)
    kommune_id = Column(Integer, index=True)

class Obserkode(Base):
    __tablename__ = "obserkoder"
    id   = Column(Integer, primary_key=True, index=True)
    kode = Column(String, unique=True, index=True)

class GlobalFilter(Base):
    __tablename__ = "globalfilter"
    id    = Column(Integer, primary_key=True, index=True)
    value = Column(String, index=True)

class GlobalYear(Base):
    __tablename__ = "globalyear"
    id    = Column(Integer, primary_key=True, index=True)
    value = Column(Integer, index=True)

class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True, index=True)
    obserkode     = Column(String, unique=True, index=True)
    navn          = Column(String)
    lokalafdeling = Column(String, nullable=True)
    kommune       = Column(String, nullable=True)
    lokalafdelinger_json = Column(Text, nullable=True)
    kommuner_json = Column(Text, nullable=True)
    matrikel1_perioder = Column(Text, nullable=True)
    matrikel2_perioder = Column(Text, nullable=True)
    matrikel_perioder_json = Column(Text, nullable=True)

class AdminPassword(Base):
    __tablename__ = "adminpassword"
    id           = Column(Integer, primary_key=True, index=True)
    password_hash = Column(String, nullable=False)


def _parse_ddmmyyyy(value: Optional[str]) -> datetime.datetime:
    try:
        return datetime.datetime.strptime(value or "", "%d-%m-%Y")
    except Exception:
        return datetime.datetime.min

# ---------------------------------------------------------
#  Helpers & Constants
# ---------------------------------------------------------
AFDELINGER = [
    "DOF København",
    "DOF Nordsjælland",
    "DOF Vestsjælland",
    "DOF Storstrøm",
    "DOF Bornholm",
    "DOF Fyn",
    "DOF Sønderjylland",
    "DOF Sydvestjylland",
    "DOF Sydøstjylland",
    "DOF Vestjylland",
    "DOF Østjylland",
    "DOF Nordvestjylland",
    "DOF Nordjylland",
]

def safe_makedirs(path: str):
    os.makedirs(path, exist_ok=True)

def get_data_dirs(aar: int):
    base = os.path.join(SERVER_DIR, "data", str(aar))
    scoreboards = os.path.join(base, "scoreboards")
    obser = os.path.join(base, "obser")  # individuelle lister pr. bruger
    return base, scoreboards, obser

def hash_password(p: str) -> str:
    return pwd_context.hash(p)

def verify_password(password: str, hashed: str) -> bool:
    if not hashed:
        return False
    if hashed.startswith("$2"):
        return pwd_context.verify(password, hashed)
    return hashlib.sha256(password.encode("utf-8")).hexdigest() == hashed

def generate_csrf_token(session: dict) -> str:
    token = secrets.token_hex(32)
    session["csrf_token"] = token
    return token

def ensure_csrf(request: Request):
    expected = request.session.get("csrf_token")
    provided = request.headers.get(CSRF_HEADER)
    if not expected or not provided or not secrets.compare_digest(expected, provided):
        raise HTTPException(status_code=403, detail="CSRF-beskyttelse fejlede")
    
def normalize_obserkode(kode: str) -> str:
    value = (kode or "").strip().upper()
    if not SAFE_OBSERKODE_RE.fullmatch(value):
        raise ValueError("Ugyldig obserkode")
    return value

def ensure_obserkode_access(request: Request, obserkode: str):
    session = request.session
    if session.get("is_admin"):
        return
    if session.get("obserkode") != obserkode:
        raise HTTPException(status_code=403, detail="Ingen adgang til denne obserkode")

_sync_rate_limit_last_call: Dict[str, float] = {}
_admin_login_last_call: Dict[str, float] = {}

def _client_host(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"

def enforce_sync_rate_limit(request: Request, min_interval_seconds: int = 30):
    session = request.session
    if session.get("is_admin"):
        actor = f"admin:{session.get('obserkode') or _client_host(request)}"
    else:
        actor = f"user:{session.get('obserkode') or _client_host(request)}"

    now = time.time()
    last = _sync_rate_limit_last_call.get(actor, 0)
    elapsed = now - last
    if elapsed < min_interval_seconds:
        wait = int(min_interval_seconds - elapsed) + 1
        raise HTTPException(status_code=429, detail=f"Vent {wait} sekunder før næste sync")
    _sync_rate_limit_last_call[actor] = now

def enforce_admin_login_rate_limit(request: Request, min_interval_seconds: int = 5):
    actor = _client_host(request)
    now = time.time()
    last = _admin_login_last_call.get(actor, 0)
    elapsed = now - last
    if elapsed < min_interval_seconds:
        wait = int(min_interval_seconds - elapsed) + 1
        raise HTTPException(status_code=429, detail=f"For mange loginforsøg. Vent {wait} sekunder.")
    _admin_login_last_call[actor] = now

def get_user_dir(aar: int, obserkode: str) -> str:
    _, _, OBSER_DIR = get_data_dirs(aar)
    return os.path.join(OBSER_DIR, normalize_obserkode(obserkode))

def get_global_user_dir(obserkode: str) -> str:
    base = os.path.join(SERVER_DIR, "data", "global", "obser")
    return os.path.join(base, normalize_obserkode(obserkode))

async def get_available_years_for_user(obserkode: str) -> List[int]:
    """Return sorted list of years where user has observations in database."""
    safe_kode = normalize_obserkode(obserkode)
    async with SessionLocal() as session:
        result = await session.execute(
            select(func.extract('year', Observation.dato).label('year'))
            .where(Observation.obserkode == safe_kode)
            .distinct()
        )
        years = [int(row[0]) for row in result if row[0] is not None]
    return sorted(years, reverse=True)

def remove_all_user_data_dirs(obserkode: str):
    safe_kode = normalize_obserkode(obserkode)
    data_root = os.path.join(SERVER_DIR, "data")
    if not os.path.isdir(data_root):
        return
    for year_dir in os.listdir(data_root):
        user_dir = os.path.join(data_root, year_dir, "obser", safe_kode)
        if os.path.isdir(user_dir):
            shutil.rmtree(user_dir, ignore_errors=True)

def safe_output(value: str) -> str:
    return escape(str(value or ""), quote=True)

def _extract_observer_name_from_html(html: str) -> str:
    content = str(html or "")
    if not content:
        return ""

    marker = 'Navn</acronym>:</td><td valign="top">'
    idx = content.find(marker)
    if idx != -1:
        start = idx + len(marker)
        end = content.find("</td>", start)
        if end != -1:
            return unescape(content[start:end].strip())

    match = re.search(r"Navn(?:</acronym>)?\s*:\s*</td>\s*<td[^>]*>(.*?)</td>", content, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""

    value = re.sub(r"<[^>]+>", "", match.group(1) or "").strip()
    return unescape(value)

def fetch_observer_name_from_dof(obserkode: str, timeout_seconds: int = 10) -> str:
    try:
        safe_kode = normalize_obserkode(obserkode)
    except Exception:
        return ""

    try:
        navn_res = requests.get(f"https://dofbasen.dk/popobser.php?obserkode={safe_kode}", timeout=timeout_seconds)
    except Exception:
        return ""

    if navn_res.status_code != 200:
        return ""

    return _extract_observer_name_from_html(navn_res.text)

def sanitize_text(s):
    """Tillad kun bogstaver, tal og mellemrum."""
    return re.sub(r'[^a-zA-ZæøåÆØÅ0-9 ]', '', str(s or ''))

def resolve_filter_tag(filt: str, aar: int) -> str:
    """
    Returnerer filter-tagget, hvor #BB automatisk får tilføjet de to sidste cifre af årstallet.
    """
    if filt == "#BB":
        return f"#BB{str(aar)[-2:]}"
    return filt

def resolve_matrikel_tags(filt: str, aar: int) -> Dict[str, str]:
    base_tag = resolve_filter_tag(filt, aar)
    if not base_tag:
        return {"matrikel1": "", "matrikel2": ""}
    return {
        "matrikel1": base_tag,
        "matrikel2": f"{base_tag}-2"
    }

def _extract_hashtag_tokens(text_value: Optional[str]) -> set:
    value = str(text_value or "").upper()
    # Matcher fx #BB26, #BB26-1, #HOME-2 (ingen delstrengs-match)
    return set(re.findall(r"#[A-Z0-9]+(?:-[A-Z0-9]+)*", value))

def _note_has_any_tag(text_value: Optional[str], tags: List[str]) -> bool:
    clean_tags = [str(tag or "").upper().strip() for tag in (tags or []) if str(tag or "").strip()]
    if not clean_tags:
        return False
    tokens = _extract_hashtag_tokens(text_value)
    return any(tag in tokens for tag in clean_tags)

def _matrikel_tags_for_year(raw_filter: str, year_value: int, matrikel_number: int) -> List[str]:
    base_tag = resolve_filter_tag(raw_filter, year_value)
    if not base_tag:
        return []
    if matrikel_number == 1:
        return [base_tag, f"{base_tag}-1"]
    return [f"{base_tag}-{int(matrikel_number)}"]

def _observation_has_matrikel_tag(row: Observation, raw_filter: str, matrikel_number: int) -> bool:
    year_value = row.dato.year if getattr(row, "dato", None) else datetime.datetime.now().year
    tags = _matrikel_tags_for_year(raw_filter, year_value, matrikel_number)
    return _note_has_any_tag(row.turnoter, tags)

def _parse_iso_date(value: Any) -> Optional[datetime.date]:
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    try:
        return datetime.datetime.strptime(text_value, "%Y-%m-%d").date()
    except Exception:
        return None

def _normalize_matrikel_periods(periods: Any) -> List[Dict[str, Optional[str]]]:
    if not isinstance(periods, list):
        return []

    normalized: List[Dict[str, Optional[str]]] = []
    for row in periods:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("navn") or "").strip()
        start_raw = row.get("start_date") or row.get("start") or row.get("fra")
        end_raw = row.get("end_date") or row.get("end") or row.get("til")

        start_date = _parse_iso_date(start_raw)
        end_date = _parse_iso_date(end_raw)
        if not name or not start_date:
            continue
        if end_date and end_date < start_date:
            continue

        normalized.append({
            "name": name,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat() if end_date else None,
        })

    normalized.sort(key=lambda item: item["start_date"])
    return normalized

def _matrikel_key(index: int) -> str:
    return f"matrikel{int(index)}"

def _matrikel_index_from_key(key: Any) -> Optional[int]:
    value = str(key or "").strip().lower()
    if not value:
        return None
    if value.isdigit():
        parsed = int(value)
        return parsed if parsed >= 1 else None
    match = re.fullmatch(r"matrikel\s*(\d+)", value)
    if not match:
        return None
    parsed = int(match.group(1))
    return parsed if parsed >= 1 else None

def _normalize_matrikel_period_map(payload: Any) -> Dict[str, List[Dict[str, Optional[str]]]]:
    if not isinstance(payload, dict):
        return {}
    result: Dict[str, List[Dict[str, Optional[str]]]] = {}
    for raw_key, raw_periods in payload.items():
        index = _matrikel_index_from_key(raw_key)
        if index is None:
            continue
        result[_matrikel_key(index)] = _normalize_matrikel_periods(raw_periods)
    return result

def _merge_period_lists(base_list: List[Dict[str, Optional[str]]], extra_list: List[Dict[str, Optional[str]]]) -> List[Dict[str, Optional[str]]]:
    merged = [dict(item) for item in (base_list or [])]
    for item in (extra_list or []):
        if not any(
            existing.get("name") == item.get("name")
            and existing.get("start_date") == item.get("start_date")
            and existing.get("end_date") == item.get("end_date")
            for existing in merged
        ):
            merged.append(dict(item))
    return _normalize_matrikel_periods(merged)

def _load_user_matrikel_periods(user: Optional[User]) -> Dict[str, List[Dict[str, Optional[str]]]]:
    if not user:
        return {}

    def _decode(raw_value: Optional[str]) -> List[Dict[str, Optional[str]]]:
        if not raw_value:
            return []
        try:
            parsed = json.loads(raw_value)
        except Exception:
            return []
        return _normalize_matrikel_periods(parsed)

    loaded: Dict[str, List[Dict[str, Optional[str]]]] = {}

    dynamic_raw = getattr(user, "matrikel_perioder_json", None)
    if dynamic_raw:
        try:
            parsed_dynamic = json.loads(dynamic_raw)
            loaded.update(_normalize_matrikel_period_map(parsed_dynamic))
        except Exception:
            pass

    legacy_m1 = _decode(getattr(user, "matrikel1_perioder", None))
    legacy_m2 = _decode(getattr(user, "matrikel2_perioder", None))
    loaded[_matrikel_key(1)] = _merge_period_lists(loaded.get(_matrikel_key(1), []), legacy_m1)
    loaded[_matrikel_key(2)] = _merge_period_lists(loaded.get(_matrikel_key(2), []), legacy_m2)

    # Fjern tomme nøgler
    return {k: v for k, v in loaded.items() if isinstance(v, list)}

def _collect_matrikel_indexes_from_observations(obs_rows: List[Observation], raw_filter: str) -> List[int]:
    indexes = set()
    for row in (obs_rows or []):
        if not getattr(row, "dato", None):
            continue
        tokens = _extract_hashtag_tokens(row.turnoter)
        base_tag = resolve_filter_tag(raw_filter, row.dato.year) if raw_filter else ""
        if not base_tag:
            for token in tokens:
                match = re.fullmatch(r"#BB\d{2}(?:-(\d+))?", token)
                if not match:
                    continue
                suffix = match.group(1)
                if not suffix or suffix == "1":
                    indexes.add(1)
                elif suffix.isdigit() and int(suffix) >= 2:
                    indexes.add(int(suffix))
            continue
        if base_tag in tokens or f"{base_tag}-1" in tokens:
            indexes.add(1)
        for token in tokens:
            if not token.startswith(f"{base_tag}-"):
                continue
            suffix = token[len(base_tag) + 1:]
            if suffix.isdigit():
                parsed = int(suffix)
                if parsed >= 2:
                    indexes.add(parsed)
    return sorted(indexes)

def _period_matches_date(period: Dict[str, Optional[str]], obs_date: Optional[datetime.date]) -> bool:
    if not obs_date:
        return False
    start_date = _parse_iso_date(period.get("start_date"))
    end_date = _parse_iso_date(period.get("end_date"))
    if not start_date:
        return False
    if obs_date < start_date:
        return False
    if end_date and obs_date > end_date:
        return False
    return True

def _period_overlaps_range(period: Dict[str, Optional[str]], start_date: datetime.date, end_date: datetime.date) -> bool:
    period_start = _parse_iso_date(period.get("start_date"))
    period_end = _parse_iso_date(period.get("end_date"))
    if not period_start:
        return False
    if period_end is None:
        period_end = datetime.date.max
    return period_start <= end_date and period_end >= start_date

def _reference_date_for_range(start_date: datetime.date, end_date: datetime.date) -> datetime.date:
    today = datetime.date.today()
    if today < start_date:
        return start_date
    if today > end_date:
        return end_date
    return today

def _select_active_period(
    periods: List[Dict[str, Optional[str]]],
    start_date: datetime.date,
    end_date: datetime.date,
    reference_date: Optional[datetime.date] = None,
) -> Optional[Dict[str, Optional[str]]]:
    ref_date = reference_date or _reference_date_for_range(start_date, end_date)
    overlapping = [p for p in periods if _period_overlaps_range(p, start_date, end_date)]
    if not overlapping:
        return None

    active_for_ref = [p for p in overlapping if _period_matches_date(p, ref_date)]
    if active_for_ref:
        return max(active_for_ref, key=lambda p: p.get("start_date") or "")

    fallback = [p for p in overlapping if (_parse_iso_date(p.get("start_date")) or datetime.date.min) <= ref_date]
    if fallback:
        return max(fallback, key=lambda p: p.get("start_date") or "")

    return min(overlapping, key=lambda p: p.get("start_date") or "")

def _matrikel_obs_for_range(
    obs_rows: List[Observation],
    tags: List[str],
    periods: List[Dict[str, Optional[str]]],
    start_date: datetime.date,
    end_date: datetime.date,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, List[Observation]]:
    tagged = [row for row in obs_rows if _note_has_any_tag(row.turnoter, tags)]
    return _apply_period_selection(tagged, periods, start_date, end_date, reference_date=reference_date)

def _apply_period_selection(
    tagged_rows: List[Observation],
    periods: List[Dict[str, Optional[str]]],
    start_date: datetime.date,
    end_date: datetime.date,
    reference_date: Optional[datetime.date] = None,
) -> Dict[str, List[Observation]]:
    tagged = list(tagged_rows or [])
    if not periods:
        return {"active": tagged, "historical": tagged}

    historical_rows = [
        row for row in tagged
        if any(_period_matches_date(period, row.dato) for period in periods)
    ]

    active_period = _select_active_period(periods, start_date, end_date, reference_date=reference_date)
    if not active_period:
        return {"active": [], "historical": historical_rows}

    active_rows = [row for row in tagged if _period_matches_date(active_period, row.dato)]
    return {"active": active_rows, "historical": historical_rows}

def safe_str(val):
    # Konverterer nan og None til tom string
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return ""
    return str(val)

def _normalize_base_art_name(name: Optional[str]) -> str:
    return (name or "").split("(")[0].split(",")[0].strip()

def _normalize_species_match_name(name: Optional[str]) -> str:
    # Streng normalisering til eksakt match mod admins eksklusionsliste.
    return " ".join(str(name or "").strip().split())

def _get_excluded_species_keys() -> set:
    try:
        names = load_excluded_species()
    except Exception:
        names = []
    return {
        _normalize_species_match_name(name).casefold()
        for name in names
        if _normalize_species_match_name(name)
    }

def _is_excluded_species(name: Optional[str], excluded_keys: Optional[set] = None) -> bool:
    keys = excluded_keys if excluded_keys is not None else _get_excluded_species_keys()
    match_name = _normalize_species_match_name(name)
    if not match_name:
        return False
    return match_name.casefold() in keys

def merged_note_text(turnoter: Any, fuglenoter: Any) -> str:
    turnoter_str = safe_str(turnoter).strip()
    fuglenoter_str = safe_str(fuglenoter).strip()
    if turnoter_str and fuglenoter_str:
        return f"{turnoter_str} {fuglenoter_str}".strip()
    return turnoter_str or fuglenoter_str

def _parse_int(val: Optional[str]) -> Optional[int]:
    try:
        if val is None:
            return None
        value = str(val).strip()
        if not value:
            return None
        return int(float(value))
    except Exception:
        return None

def _read_kommuner() -> List[Dict[str, str]]:
    kommuner_path = os.path.join(SERVER_DIR, "kommuner.csv")
    if not os.path.exists(kommuner_path):
        return []
    kommuner = []
    with open(kommuner_path, encoding="utf-8") as f:
        next(f, None)
        for line in f:
            if ";" not in line:
                continue
            parts = line.strip().split(";")
            if len(parts) < 2:
                continue
            kommune_id = parts[0].strip()
            navn = parts[1].strip()
            if kommune_id and navn:
                kommuner.append({"id": kommune_id, "navn": navn})
    return kommuner

def _kommune_slug(navn: str) -> str:
    value = (navn or "").strip().lower()
    value = value.replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "kommune"

def _kommune_sites_path(kommune_name: str) -> str:
    return os.path.join(SERVER_DIR, "data", "kommune", f"{_kommune_slug(kommune_name)}.json")

def _load_kommune_sites_from_file(kommune_name: str) -> List[int]:
    path = _kommune_sites_path(kommune_name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        sites = json.load(f)
    site_numbers = []
    for site in sites or []:
        value = site.get("siteNumber") if isinstance(site, dict) else None
        parsed = _parse_int(value)
        if parsed is not None:
            site_numbers.append(parsed)
    return site_numbers

def _kommune_name_by_id(kommune_id: str) -> Optional[str]:
    kommune_id = str(kommune_id or "").strip()
    for row in _read_kommuner():
        if row.get("id") == kommune_id:
            return row.get("navn")
    return None

def _load_json_string_list(raw_value: Optional[str]) -> List[str]:
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except Exception:
        return []
    return [str(v).strip() for v in parsed if str(v).strip()] if isinstance(parsed, list) else []

def _normalize_lokalafdelinger(values: Any) -> List[str]:
    allowed = set(AFDELINGER)
    normalized: List[str] = []
    for value in (values or []):
        item = str(value or "").strip()
        if not item or item not in allowed:
            continue
        if item not in normalized:
            normalized.append(item)
        if len(normalized) >= 3:
            break
    return normalized

def _normalize_kommuner(values: Any) -> List[str]:
    kommuner = _read_kommuner()
    valid_ids = {str(row.get("id")) for row in kommuner if row.get("id")}
    navn_to_id = {
        str(row.get("navn") or "").strip().lower(): str(row.get("id"))
        for row in kommuner
        if row.get("id") and row.get("navn")
    }

    normalized: List[str] = []
    for value in (values or []):
        item = str(value or "").strip()
        if not item:
            continue
        if item.isdigit() and item in valid_ids:
            resolved = item
        else:
            resolved = navn_to_id.get(item.lower())
            if not resolved:
                continue
        if resolved not in normalized:
            normalized.append(resolved)
        if len(normalized) >= 5:
            break
    return normalized

def _normalize_single_kommune(value: Any) -> Optional[str]:
    normalized = _normalize_kommuner([value])
    return normalized[0] if normalized else None

def _user_opted_lokalafdelinger(user: Optional[User]) -> List[str]:
    if not user:
        return []
    raw_json = getattr(user, "lokalafdelinger_json", None)
    if raw_json is not None and str(raw_json).strip() != "":
        return _normalize_lokalafdelinger(_load_json_string_list(raw_json))
    fallback = str(getattr(user, "lokalafdeling", "") or "").strip()
    return _normalize_lokalafdelinger([fallback]) if fallback else []

def _user_opted_kommuner(user: Optional[User]) -> List[str]:
    if not user:
        return []
    from_json = _normalize_kommuner(_load_json_string_list(getattr(user, "kommuner_json", None)))
    if from_json:
        return from_json
    fallback = str(getattr(user, "kommune", "") or "").strip()
    return _normalize_kommuner([fallback]) if fallback else []

# ---------------------------------------------------------
#  Global filter & year
# ---------------------------------------------------------
async def get_global_filter() -> str:
    async with SessionLocal() as session:
        row = (await session.execute(select(GlobalFilter).order_by(GlobalFilter.id.desc()))).scalars().first()
        return row.value if row else ""

async def set_global_filter(value: str):
    async with SessionLocal() as session:
        await session.execute(GlobalFilter.__table__.delete())
        session.add(GlobalFilter(value=value.strip()))
        await session.commit()

async def get_global_year() -> int:
    async with SessionLocal() as session:
        row = (await session.execute(select(GlobalYear).order_by(GlobalYear.id.desc()))).scalars().first()
        return row.value if row and row.value else datetime.datetime.now().year

async def set_global_year(value: int):
    async with SessionLocal() as session:
        await session.execute(GlobalYear.__table__.delete())
        session.add(GlobalYear(value=int(value)))
        await session.commit()

# ---------------------------------------------------------
#  First lists (individuelle)
# ---------------------------------------------------------
def _firsts_from_obs(obs_iter: List[Observation], excluded_keys: Optional[set] = None) -> List[Dict[str, Any]]:
    firsts: Dict[str, Dict[str, Any]] = {}
    blocked = excluded_keys if excluded_keys is not None else _get_excluded_species_keys()
    for o in obs_iter:
        navn = _normalize_base_art_name(o.artnavn)
        if "sp." in navn or "/" in navn or " x " in navn:
            continue
        if _is_excluded_species(o.artnavn, blocked):
            continue
        key = navn.casefold()
        has_obsid = bool(str(o.obsid or "").strip())
        existing = firsts.get(key)
        should_replace = (
            existing is None
            or o.dato < existing["dato"]
            or (
                o.dato == existing["dato"]
                and not str(existing.get("obsid") or "").strip()
                and has_obsid
            )
        )
        if should_replace:
            firsts[key] = {
                "artnavn": safe_output(navn),
                "lokalitet": safe_output(o.loknavn or ""),
                "dato": o.dato,
                "obsid": safe_output(o.obsid or "")
            }
    return sorted(
        (
            {
                "artnavn": v["artnavn"],
                "lokalitet": v["lokalitet"],
                "dato": v["dato"].strftime("%d-%m-%Y"),
                "obsid": v.get("obsid", "")
            }
            for v in firsts.values()
        ),
        key=lambda x: datetime.datetime.strptime(x["dato"], "%d-%m-%Y"),
    )

async def _user_matrikel_view_payload(
    obserkode: str,
    aar_value: Any,
    matrikel_index: int = 1,
    selected_period_name: Optional[str] = None,
) -> Dict[str, Any]:
    raw_filter = await get_global_filter()
    today = datetime.date.today()

    is_global = str(aar_value) == "global"
    if is_global:
        range_start = datetime.date.min
        range_end = datetime.date.max
        year_hint = today.year
    else:
        year_hint = int(aar_value)
        range_start = datetime.date(year_hint, 1, 1)
        range_end = datetime.date(year_hint, 12, 31)

    visible_end = min(range_end, today)
    excluded_keys = _get_excluded_species_keys()

    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        all_obs_query = select(Observation).where(
            Observation.obserkode == obserkode,
            Observation.dato <= today,
        )
        all_obs_rows = (await session.execute(all_obs_query)).scalars().all()
        obs_query = select(Observation).where(Observation.obserkode == obserkode)
        if not is_global:
            obs_query = obs_query.where(
                Observation.dato >= range_start,
                Observation.dato <= visible_end,
            )
        obs_rows = (await session.execute(obs_query)).scalars().all()

    available_matrikler = _collect_matrikel_indexes_from_observations(all_obs_rows, raw_filter)
    if not available_matrikler:
        available_matrikler = [1]

    tagged_rows = [
        row for row in obs_rows
        if _observation_has_matrikel_tag(row, raw_filter, matrikel_index)
    ]
    if is_global:
        tagged_rows = [row for row in tagged_rows if row.dato and row.dato <= today]

    period_map = _load_user_matrikel_periods(user)
    period_key = _matrikel_key(matrikel_index)
    periods = period_map.get(period_key) or []
    period_options = [
        period for period in periods
        if _period_overlaps_range(period, range_start, visible_end)
    ]
    period_options.sort(key=lambda period: period.get("start_date") or "")

    reference_date = _reference_date_for_range(range_start, visible_end)
    active_period = _select_active_period(period_options, range_start, visible_end, reference_date=reference_date) if period_options else None

    selected_period = None
    if selected_period_name:
        selected_period = next((period for period in period_options if (period.get("name") or "") == selected_period_name), None)
    if selected_period is None:
        selected_period = active_period

    if selected_period:
        selected_rows = [row for row in tagged_rows if _period_matches_date(selected_period, row.dato)]
    else:
        selected_rows = tagged_rows

    firsts = _firsts_from_obs(selected_rows, excluded_keys=excluded_keys)

    trend_points: List[Dict[str, Any]] = []
    for idx, row in enumerate(firsts, start=1):
        if row.get("dato"):
            trend_points.append({"dato": row.get("dato"), "count": idx})

    return {
        "firsts": firsts,
        "matrikel_index": matrikel_index,
        "available_matrikler": available_matrikler,
        "period_options": period_options,
        "selected_period_name": (selected_period or {}).get("name") if selected_period else None,
        "active_period_name": (active_period or {}).get("name") if active_period else None,
        "trend_points": trend_points,
        "year": "global" if is_global else year_hint,
    }


async def _user_kommune_view_payload(
    obserkode: str,
    aar_value: Any,
    kommune_id_value: Any,
    matrikel_only: bool = False,
) -> Dict[str, Any]:
    kommune_id = _normalize_single_kommune(kommune_id_value)
    if not kommune_id:
        return {"firsts": []}

    is_global = str(aar_value) == "global"
    today = datetime.date.today()
    raw_filter = await get_global_filter()
    excluded_keys = _get_excluded_species_keys()

    query = select(Observation).where(
        Observation.obserkode == obserkode,
        Observation.dato <= today,
    )
    if not is_global:
        year_value = int(aar_value)
        start_date = datetime.date(year_value, 1, 1)
        end_date = min(datetime.date(year_value, 12, 31), today)
        query = query.where(
            Observation.dato >= start_date,
            Observation.dato <= end_date,
        )

    async with SessionLocal() as session:
        obs_rows = (await session.execute(query)).scalars().all()
        site_numbers = (await session.execute(
            select(Lokation.site_number).where(Lokation.kommune_id == int(kommune_id))
        )).scalars().all()

    if not site_numbers:
        kommune_name = _kommune_name_by_id(str(kommune_id))
        if kommune_name:
            site_numbers = _load_kommune_sites_from_file(kommune_name)

    site_set = {_parse_int(value) for value in site_numbers if _parse_int(value) is not None}
    if not site_set:
        return {"firsts": []}

    selected_rows = [row for row in obs_rows if _parse_int(row.loknr) in site_set]
    if matrikel_only:
        selected_rows = [
            row for row in selected_rows
            if _observation_has_matrikel_tag(row, raw_filter, 1)
        ]

    return {
        "firsts": _firsts_from_obs(selected_rows, excluded_keys=excluded_keys)
    }

async def generate_user_lists(obserkode: str, aar: int):
    obserkode = normalize_obserkode(obserkode)
    _, _, OBSER_DIR = get_data_dirs(aar)
    safe_makedirs(OBSER_DIR)
    user_dir = get_user_dir(aar, obserkode)
    safe_makedirs(user_dir)

    async with SessionLocal() as session:
        q = select(Observation).where(
            Observation.obserkode == obserkode,
            Observation.dato >= datetime.date(aar, 1, 1),
            Observation.dato <= datetime.date(aar, 12, 31),
        )
        obs = (await session.execute(q)).scalars().all()
        filt = await get_global_filter()
        user = (await session.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
    tags = resolve_matrikel_tags(filt, aar)
    matrikel1_tags = [tags["matrikel1"], f"{tags['matrikel1']}-1"] if tags["matrikel1"] else []
    matrikel2_tags = [tags["matrikel2"]] if tags["matrikel2"] else []
    user_periods = _load_user_matrikel_periods(user)
    year_start = datetime.date(aar, 1, 1)
    year_end = datetime.date(aar, 12, 31)
    year_reference_date = _reference_date_for_range(year_start, year_end)
    excluded_keys = _get_excluded_species_keys()

    # Global (alle)
    global_list = _firsts_from_obs(obs, excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "global.json"), "w", encoding="utf-8") as f:
        json.dump(global_list, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/{aar}: global.json ({len(global_list)} arter)")

    # Matrikel 1 (aktiv periode)
    m1_obs = _matrikel_obs_for_range(
        obs_rows=obs,
        tags=matrikel1_tags,
        periods=user_periods.get("matrikel1") or [],
        start_date=year_start,
        end_date=year_end,
        reference_date=year_reference_date,
    )
    matrikel_list = _firsts_from_obs(m1_obs["active"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikelarter.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel_list, f, ensure_ascii=False, indent=2)
    matrikel_historik = _firsts_from_obs(m1_obs["historical"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikelarter_historik.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel_historik, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/{aar}: matrikelarter.json ({len(matrikel_list)} arter, filter='{','.join(matrikel1_tags)}')")

    # Matrikel 2 (aktiv periode, privat)
    m2_obs = _matrikel_obs_for_range(
        obs_rows=obs,
        tags=matrikel2_tags,
        periods=user_periods.get("matrikel2") or [],
        start_date=year_start,
        end_date=year_end,
        reference_date=year_reference_date,
    )
    matrikel2_list = _firsts_from_obs(m2_obs["active"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikel2arter.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel2_list, f, ensure_ascii=False, indent=2)
    matrikel2_historik = _firsts_from_obs(m2_obs["historical"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikel2arter_historik.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel2_historik, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/{aar}: matrikel2arter.json ({len(matrikel2_list)} arter, filter='{tags['matrikel2']}')")

    # Lokalafdeling – alle afdelinger
    la_dict: Dict[str, Dict[str, Any]] = {}
    for afd in AFDELINGER:
        la_obs = [o for o in obs if (o.afdeling or "").strip() == afd]
        la_dict[afd] = {
            "alle": _firsts_from_obs(la_obs, excluded_keys=excluded_keys),
            "matrikel": _firsts_from_obs([
                o for o in la_obs
                if o in m1_obs["active"]
            ], excluded_keys=excluded_keys),
        }
    with open(os.path.join(user_dir, "lokalafdeling.json"), "w", encoding="utf-8") as f:
        json.dump(la_dict, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/{aar}: lokalafdeling.json for {len(AFDELINGER)} afdelinger")

    # Kommune – kun brugerens hjemme-kommune
    kommune_id = None
    kommune_navn = None
    if user and getattr(user, "kommune", None):
        value = str(user.kommune).strip()
        if value.isdigit():
            kommune_id = int(value)
            kommune_navn = _kommune_name_by_id(value)
        else:
            kommune_navn = value
            for row in _read_kommuner():
                if row.get("navn") == value:
                    kommune_id = _parse_int(row.get("id"))
                    break

    kommune_alle = []
    kommune_matrikel = []
    if kommune_id:
        async with SessionLocal() as session:
            site_numbers = (await session.execute(
                select(Lokation.site_number).where(Lokation.kommune_id == kommune_id)
            )).scalars().all()
        if not site_numbers and kommune_navn:
            site_numbers = _load_kommune_sites_from_file(kommune_navn)
        site_set = set(_parse_int(x) for x in site_numbers if _parse_int(x) is not None)
        if site_set:
            k_obs = [o for o in obs if o.loknr in site_set]
            kommune_alle = _firsts_from_obs(k_obs, excluded_keys=excluded_keys)
            if tags["matrikel1"]:
                kommune_matrikel = _firsts_from_obs([o for o in k_obs if o in m1_obs["active"]], excluded_keys=excluded_keys)

    kommune_payload = {
        "kommune_id": str(kommune_id) if kommune_id else None,
        "kommune_navn": kommune_navn,
        "alle": kommune_alle,
        "matrikel": kommune_matrikel,
    }
    with open(os.path.join(user_dir, "kommune.json"), "w", encoding="utf-8") as f:
        json.dump(kommune_payload, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/{aar}: kommune.json")

async def generate_user_global_lists(obserkode: str):
    obserkode = normalize_obserkode(obserkode)
    user_dir = get_global_user_dir(obserkode)
    safe_makedirs(user_dir)

    async with SessionLocal() as session:
        q = select(Observation).where(Observation.obserkode == obserkode)
        obs = (await session.execute(q)).scalars().all()
        filt = await get_global_filter()
        user = (await session.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
    user_periods = _load_user_matrikel_periods(user)
    excluded_keys = _get_excluded_species_keys()

    # Global (alle)
    global_list = _firsts_from_obs(obs, excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "global.json"), "w", encoding="utf-8") as f:
        json.dump(global_list, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/global: global.json ({len(global_list)} arter)")

    # Matrikel 1 (aktiv periode, all-time)
    all_start = datetime.date.min
    all_end = datetime.date.max
    all_reference_date = datetime.date.today()
    tagged_m1 = [row for row in obs if _observation_has_matrikel_tag(row, filt, 1)]
    m1_obs = _apply_period_selection(
        tagged_rows=tagged_m1,
        periods=user_periods.get("matrikel1") or [],
        start_date=all_start,
        end_date=all_end,
        reference_date=all_reference_date,
    )
    matrikel_list = _firsts_from_obs(m1_obs["active"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikelarter.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel_list, f, ensure_ascii=False, indent=2)
    matrikel_historik = _firsts_from_obs(m1_obs["historical"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikelarter_historik.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel_historik, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/global: matrikelarter.json ({len(matrikel_list)} arter, filter='{filt}')")

    # Matrikel 2 (aktiv periode, privat all-time)
    tagged_m2 = [row for row in obs if _observation_has_matrikel_tag(row, filt, 2)]
    m2_obs = _apply_period_selection(
        tagged_rows=tagged_m2,
        periods=user_periods.get("matrikel2") or [],
        start_date=all_start,
        end_date=all_end,
        reference_date=all_reference_date,
    )
    matrikel2_list = _firsts_from_obs(m2_obs["active"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikel2arter.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel2_list, f, ensure_ascii=False, indent=2)
    matrikel2_historik = _firsts_from_obs(m2_obs["historical"], excluded_keys=excluded_keys)
    with open(os.path.join(user_dir, "matrikel2arter_historik.json"), "w", encoding="utf-8") as f:
        json.dump(matrikel2_historik, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/global: matrikel2arter.json ({len(matrikel2_list)} arter, filter='{filt}-2')")

    # Lokalafdeling – alle afdelinger (all-time)
    la_dict: Dict[str, Dict[str, Any]] = {}
    for afdeling in AFDELINGER:
        la_obs = [o for o in obs if (o.afdeling or "").strip() == afdeling]
        la_dict[afdeling] = {
            "alle": _firsts_from_obs(la_obs, excluded_keys=excluded_keys),
            "matrikel": _firsts_from_obs([
                o for o in la_obs
                if o in m1_obs["active"]
            ], excluded_keys=excluded_keys),
        }
    with open(os.path.join(user_dir, "lokalafdeling.json"), "w", encoding="utf-8") as f:
        json.dump(la_dict, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/global: lokalafdeling.json")

    # Kommune – kun brugerens hjemme-kommune
    kommune_id = None
    kommune_navn = None
    if user and getattr(user, "kommune", None):
        value = str(user.kommune).strip()
        if value.isdigit():
            kommune_id = int(value)
            kommune_navn = _kommune_name_by_id(value)
        else:
            kommune_navn = value
            for row in _read_kommuner():
                if row.get("navn") == value:
                    kommune_id = _parse_int(row.get("id"))
                    break

    kommune_alle = []
    kommune_matrikel = []
    if kommune_id:
        async with SessionLocal() as session:
            site_numbers = (await session.execute(
                select(Lokation.site_number).where(Lokation.kommune_id == kommune_id)
            )).scalars().all()
        if not site_numbers and kommune_navn:
            site_numbers = _load_kommune_sites_from_file(kommune_navn)
        site_set = set(_parse_int(x) for x in site_numbers if _parse_int(x) is not None)
        if site_set:
            k_obs = [o for o in obs if o.loknr in site_set]
            kommune_alle = _firsts_from_obs(k_obs, excluded_keys=excluded_keys)
            if filt:
                kommune_matrikel = _firsts_from_obs([o for o in k_obs if o in m1_obs["active"]], excluded_keys=excluded_keys)

    kommune_payload = {
        "kommune_id": str(kommune_id) if kommune_id else None,
        "kommune_navn": kommune_navn,
        "alle": kommune_alle,
        "matrikel": kommune_matrikel,
    }
    with open(os.path.join(user_dir, "kommune.json"), "w", encoding="utf-8") as f:
        json.dump(kommune_payload, f, ensure_ascii=False, indent=2)
    print(f"[LISTS] {obserkode}/global: kommune.json")


_prefs_scoreboard_rebuild_lock = asyncio.Lock()


async def _rebuild_scoreboards_after_optin_change(obserkode: str):
    try:
        safe_obserkode = normalize_obserkode(obserkode)
    except ValueError:
        return

    async with _prefs_scoreboard_rebuild_lock:
        try:
            years = await get_available_years_for_user(safe_obserkode)
            if not years:
                current_year = await get_global_year()
                years = [current_year]

            normalized_years: List[int] = []
            for year_value in years:
                try:
                    parsed_year = int(year_value)
                except Exception:
                    continue
                if parsed_year > 0:
                    normalized_years.append(parsed_year)
            years = sorted(set(normalized_years))

            for year in years:
                await generate_scoreboards_from_lists(year)

            await generate_user_global_lists(safe_obserkode)
            await generate_global_scoreboards_all_time()
            print(f"[PREFS] Scoreboards genopbygget for {safe_obserkode}: years={years} + global")
        except Exception as error:
            print(f"[PREFS] Kunne ikke genopbygge scoreboards for {safe_obserkode}: {error}")

# ---------------------------------------------------------
#  Artsdata
# ---------------------------------------------------------    

@app.get("/api/matrikel_arter")
async def matrikel_arter(
    aar: int = Query(None, description="År (valgfri, default: global year)")
):
    """
    Returnerer en sorteret liste med alle arter i matrikel-listerne.
    """
    if aar is None:
        aar = await get_global_year()
    _, _, OBSER_DIR = get_data_dirs(aar)
    arter = set()
    user_dirs = [os.path.join(OBSER_DIR, d) for d in os.listdir(OBSER_DIR) if os.path.isdir(os.path.join(OBSER_DIR, d))]
    for user_dir in user_dirs:
        path = os.path.join(user_dir, "matrikelarter.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        for row in rows:
            navn = (row.get("artnavn") or "").split("(")[0].split(",")[0].strip()
            if navn:
                arter.add(navn)
    return sorted(arter)


@app.get("/api/arter")
async def arter(
    scope: str = Query("global", description="'global' eller 'matrikel'"),
    aar: int = Query(None, description="År (valgfri, default: global year)")
):
    """
    Returnerer en sorteret liste med alle arter for valgt scope.
    """
    if aar is None:
        aar = await get_global_year()
    scope = (scope or "global").strip().lower()
    if scope == "alle":
        scope = "global"
    if scope not in ("global", "matrikel"):
        raise HTTPException(status_code=400, detail="Ukendt scope")

    _, _, OBSER_DIR = get_data_dirs(aar)
    arter = set()
    user_dirs = [os.path.join(OBSER_DIR, d) for d in os.listdir(OBSER_DIR) if os.path.isdir(os.path.join(OBSER_DIR, d))]
    filename = "matrikelarter.json" if scope == "matrikel" else "global.json"
    for user_dir in user_dirs:
        path = os.path.join(user_dir, filename)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        for row in rows:
            navn = (row.get("artnavn") or "").split("(")[0].split(",")[0].strip()
            if navn:
                arter.add(navn)
    return sorted(arter)

async def _artdata_payload(artnavn: str, scope: str, aar: Optional[int]):
    import unicodedata

    if aar is None:
        aar = await get_global_year()
    scope = (scope or "global").strip().lower()
    if scope == "alle":
        scope = "global"
    if scope not in ("global", "matrikel"):
        raise HTTPException(status_code=400, detail="Ukendt scope")

    _, _, OBSER_DIR = get_data_dirs(aar)
    user_dirs = [os.path.join(OBSER_DIR, d) for d in os.listdir(OBSER_DIR) if os.path.isdir(os.path.join(OBSER_DIR, d))]
    ankomstgraf = []
    sidste_fund = []

    artnavn_norm = unicodedata.normalize("NFC", artnavn.strip())

    # 1. Akkumuleret statistik (ankomstgraf) og sidste fund pr. bruger
    for user_dir in user_dirs:
        if scope == "matrikel":
            path = os.path.join(user_dir, "matrikelarter.json")
        else:
            path = os.path.join(user_dir, "global.json")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)

        # Ankomst pr. bruger (første fund)
        for row in rows:
            navn = (row.get("artnavn") or "").split("(")[0].split(",")[0].strip()
            navn_norm = unicodedata.normalize("NFC", navn)
            if navn_norm == artnavn_norm and row.get("dato"):
                ankomstgraf.append({
                    "matrikel": os.path.basename(user_dir),
                    "ankomst_dato": row["dato"]
                })
                break  # kun første gang brugeren får arten

        # Sidste fund pr. bruger
        datoer_fund = [
            row["dato"] for row in rows
            if unicodedata.normalize("NFC", (row.get("artnavn") or "").split("(")[0].split(",")[0].strip()) == artnavn_norm and row.get("dato")
        ]
        if datoer_fund:
            sidste = max(datoer_fund, key=lambda d: [int(x) for x in d.split('-')[::-1]])
            sidste_fund.append({
                "matrikel": os.path.basename(user_dir),
                "sidste_dato": sidste
            })

    # 2. Observationer pr. turid pr. dag
    obs_per_turid = []
    filter_tag = None
    if scope == "matrikel":
        filter_tag = resolve_filter_tag(await get_global_filter(), aar)

    if scope == "global" or filter_tag:
        async with SessionLocal() as session:
            base_query = (
                select(
                    Observation.dato,
                    Observation.turid,
                    func.sum(Observation.antal).label("antal")
                )
                .where(
                    func.replace(func.replace(func.replace(Observation.artnavn, "(", ""), ")", ""), ",", "").ilike(f"%{artnavn}%"),
                    Observation.dato >= datetime.date(aar, 1, 1),
                    Observation.dato <= datetime.date(aar, 12, 31)
                )
            )

            if scope == "matrikel" and filter_tag:
                base_query = base_query.where(Observation.turnoter.ilike(f"%{filter_tag}%"))

            rows = (await session.execute(base_query.group_by(Observation.dato, Observation.turid))).all()

            obs_by_date = {}
            for row in rows:
                d = row.dato.strftime("%d-%m-%Y") if row.dato else ""
                if d not in obs_by_date:
                    obs_by_date[d] = {"antal": 0, "turids": set()}
                obs_by_date[d]["antal"] += row.antal or 0
                obs_by_date[d]["turids"].add(row.turid)
            obs_per_turid = [
                {
                    "dato": d,
                    "ratio": (v["antal"] / len(v["turids"])) if v["turids"] else 0
                }
                for d, v in sorted(obs_by_date.items())
            ]

    return {
        "ankomstgraf": ankomstgraf,
        "obs_per_turid": obs_per_turid,
        "sidste_fund": sidste_fund
    }


@app.get("/api/artdata")
async def artdata(
    artnavn: str = Query(..., description="Navn på fugleart (præcis, som i listerne)"),
    scope: str = Query("global", description="'global' eller 'matrikel'"),
    aar: int = Query(None, description="År (valgfri, default: global year)")
):
    """
    Returnerer akkumuleret data + statistik for en art,
    samt observationer pr. turid pr. dag og sidste fund pr. bruger.
    """
    return await _artdata_payload(artnavn, scope, aar)


@app.post("/api/artdata")
async def artdata_post(payload: Dict[str, Any] = Body(...)):
    artnavn = payload.get("artnavn")
    if not artnavn:
        raise HTTPException(status_code=400, detail="artnavn mangler")
    scope = payload.get("scope", "global")
    aar = payload.get("aar")
    return await _artdata_payload(artnavn, scope, aar)

# ---------------------------------------------------------
#  Scoreboards (fra listerne)
# ---------------------------------------------------------
def _finalize(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [r for r in rows if r.get("antal_arter", 0) > 0]
    rows.sort(key=lambda x: x["antal_arter"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["placering"] = i
    return rows

def _ensure_scoreboard_fields(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for r in rows:
        r.setdefault("obserkode", "")
        r.setdefault("antal_arter", 0)
        r.setdefault("sidste_art", "")
        r.setdefault("sidste_dato", "")
    return rows

def _load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)



async def generate_scoreboards_from_lists(aar: int):
    """
    3-trins rebuild fra rigtige lister:
      1) Læs *kun matrikel*-lister -> skriv global_matrikel + lokalafdeling_matrikel
      2) Læs *kun lokal*-lister   -> skriv lokalafdeling_alle
      3) Læs *kun global*-lister  -> skriv global_alle
    Inkluderer alle brugere (også tomme lister) og beregner robust antal + sidste.
    """
    import datetime
    import shutil
    excluded_keys = _get_excluded_species_keys()

    # --- Hjælpere ---
    def _normalize_art(name: str) -> str:
        return (name or "").split("(")[0].split(",")[0].strip()

    def _is_valid_art(name: str) -> bool:
        n = _normalize_base_art_name(name)
        if _is_excluded_species(name, excluded_keys):
            return False
        return ("sp." not in n) and ("/" not in n) and (" x " not in n)

    def _parse_dato(d: str) -> datetime.datetime:
        try:
            return datetime.datetime.strptime(d or "", "%d-%m-%Y")
        except Exception:
            return datetime.datetime.min

    def _score_from_list(list_rows):
        """
        list_rows: [{ "artnavn": str, "lokalitet": str, "dato": "dd-mm-YYYY" }, ...]
        Returnerer (antal_arter, sidste_art, sidste_dato) robust.
        Inkluderer alle brugere: tom liste -> (0, "", "").
        """
        if not isinstance(list_rows, list) or not list_rows:
            return 0, "", ""

        cleaned = [r for r in list_rows if r.get("artnavn") and _is_valid_art(r["artnavn"])]
        if not cleaned:
            return 0, "", ""

        unique_arter = {_normalize_art(r["artnavn"]) for r in cleaned}
        antal_arter = len(unique_arter)

        latest = max(cleaned, key=lambda r: _parse_dato(r.get("dato")))
        return antal_arter, latest.get("artnavn", ""), latest.get("dato", "")

    def _safe_clear_dir(path: str):
        if os.path.isdir(path):
            # Ryd hele output-mappen for at starte helt forfra
            shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, exist_ok=True)

    # --- Stier ---
    _, SCOREBOARD_DIR, OBSER_DIR = get_data_dirs(aar)
    safe_makedirs(SCOREBOARD_DIR)

    # --- Brugere ---
    async with SessionLocal() as session:
        users = [
            u for u in (await session.execute(select(User))).scalars().all()
            if SAFE_OBSERKODE_RE.fullmatch(u.obserkode or "")
        ]

    # ======================================================================
    # 1) MATRIIKEL: global_matrikel + lokalafdeling_matrikel
    # ======================================================================
    outdir_global_matr = os.path.join(SCOREBOARD_DIR, "global_matrikel")
    outdir_lokal_matr  = os.path.join(SCOREBOARD_DIR, "lokalafdeling_matrikel")
    _safe_clear_dir(outdir_global_matr)
    _safe_clear_dir(outdir_lokal_matr)

    # Global matrikel (samlet én fil)
    gm_rows = []
    for u in users:
        L_m = _load_json(os.path.join(OBSER_DIR, u.obserkode, "matrikelarter.json")) or []
        a, art, dato = _score_from_list(L_m)
        gm_rows.append({
            "navn": safe_output(u.navn or u.obserkode),
            "obserkode": u.obserkode,
            "antal_arter": a,
            "sidste_art": safe_output(art),
            "sidste_dato": safe_output(dato),
        })
        print(f"[SB-IN] {u.obserkode} global_matrikel: list={len(L_m)} -> antal={a}, sidste={art} @ {dato}")

    with open(os.path.join(outdir_global_matr, "scoreboard.json"), "w", encoding="utf-8") as f:
        json.dump(_finalize(_ensure_scoreboard_fields(gm_rows)), f, ensure_ascii=False, indent=2)

    # Lokalafdeling matrikel (en fil pr. afdeling)
    for afd in AFDELINGER:
        rows_matr = []
        for u in users:
            opted_lokal = _user_opted_lokalafdelinger(u)
            if afd not in opted_lokal:
                continue
            la_map = _load_json(os.path.join(OBSER_DIR, u.obserkode, "lokalafdeling.json")) or {}
            L_matr = (la_map.get(afd) or {}).get("matrikel") or []
            a2, art2, dato2 = _score_from_list(L_matr)
            rows_matr.append({
                "navn": u.navn or u.obserkode,
                "obserkode": u.obserkode,
                "antal_arter": a2,
                "sidste_art": art2,
                "sidste_dato": dato2,
            })
            print(f"[SB-IN] {u.obserkode} lokal_matrikel[{afd}]: list={len(L_matr)} -> antal={a2}, sidste={art2} @ {dato2}")

        filename = f"{afd.replace(' ', '_')}.json"
        with open(os.path.join(outdir_lokal_matr, filename), "w", encoding="utf-8") as f:
            json.dump(_finalize(_ensure_scoreboard_fields(rows_matr)), f, ensure_ascii=False, indent=2)

    # ======================================================================
    # 2) LOKAL: lokalafdeling_alle
    # ======================================================================
    outdir_lokal_alle = os.path.join(SCOREBOARD_DIR, "lokalafdeling_alle")
    _safe_clear_dir(outdir_lokal_alle)

    for afd in AFDELINGER:
        rows_alle = []
        for u in users:
            opted_lokal = _user_opted_lokalafdelinger(u)
            if afd not in opted_lokal:
                continue
            la_map = _load_json(os.path.join(OBSER_DIR, u.obserkode, "lokalafdeling.json")) or {}
            L_alle = (la_map.get(afd) or {}).get("alle") or []
            a1, art1, dato1 = _score_from_list(L_alle)
            rows_alle.append({
                "navn": u.navn or u.obserkode,
                "obserkode": u.obserkode,
                "antal_arter": a1,
                "sidste_art": art1,
                "sidste_dato": dato1,
            })
            print(f"[SB-IN] {u.obserkode} lokal_alle[{afd}]: list={len(L_alle)} -> antal={a1}, sidste={art1} @ {dato1}")

        filename = f"{afd.replace(' ', '_')}.json"
        with open(os.path.join(outdir_lokal_alle, filename), "w", encoding="utf-8") as f:
            json.dump(_finalize(_ensure_scoreboard_fields(rows_alle)), f, ensure_ascii=False, indent=2)

    # ======================================================================
    # 3) GLOBAL: global_alle
    # ======================================================================
    outdir_global_alle = os.path.join(SCOREBOARD_DIR, "global_alle")
    _safe_clear_dir(outdir_global_alle)

    ga_rows = []
    for u in users:
        L_g = _load_json(os.path.join(OBSER_DIR, u.obserkode, "global.json")) or []
        a, art, dato = _score_from_list(L_g)
        ga_rows.append({
            "navn": u.navn or u.obserkode,
            "obserkode": u.obserkode,
            "antal_arter": a,
            "sidste_art": art,
            "sidste_dato": dato,
        })
        print(f"[SB-IN] {u.obserkode} global_alle: list={len(L_g)} -> antal={a}, sidste={art} @ {dato}")

    with open(os.path.join(outdir_global_alle, "scoreboard.json"), "w", encoding="utf-8") as f:
        json.dump(_finalize(_ensure_scoreboard_fields(ga_rows)), f, ensure_ascii=False, indent=2)

    await generate_kommune_scoreboards(aar, users)


async def generate_kommune_scoreboards(aar: int, users: List[User]):
    import shutil
    excluded_keys = _get_excluded_species_keys()
    raw_filter = await get_global_filter()
    year_start = datetime.date(aar, 1, 1)
    year_end = datetime.date(aar, 12, 31)

    def _user_in_kommune(user: User, kommune_id: int) -> bool:
        opted_ids = _user_opted_kommuner(user)
        return str(kommune_id) in opted_ids

    def _is_valid_art(name: str) -> bool:
        n = _normalize_base_art_name(name)
        if _is_excluded_species(name, excluded_keys):
            return False
        return ("sp." not in n) and ("/" not in n) and (" x " not in n)

    def _firsts_from_obs_rows(obs_rows, filter_tag: Optional[str] = None):
        firsts = {}
        for row in obs_rows:
            if filter_tag and filter_tag not in (row.turnoter or ""):
                continue
            navn = (row.artnavn or "").split("(")[0].split(",")[0].strip()
            if not _is_valid_art(navn) or not row.dato:
                continue
            if navn not in firsts or row.dato < firsts[navn]["dato"]:
                firsts[navn] = {"dato": row.dato, "artnavn": navn}
        return firsts

    def _score_from_firsts(firsts):
        if not firsts:
            return 0, "", ""
        latest = max(firsts.values(), key=lambda r: r["dato"])
        return len(firsts), latest.get("artnavn", ""), latest["dato"].strftime("%d-%m-%Y")

    def _safe_clear_dir(path: str):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, exist_ok=True)

    kommuner = _read_kommuner()
    if not kommuner:
        return

    _, scoreboard_dir, _ = get_data_dirs(aar)
    outdir_kommune_alle = os.path.join(scoreboard_dir, "kommune_alle")
    outdir_kommune_matr = os.path.join(scoreboard_dir, "kommune_matrikel")
    _safe_clear_dir(outdir_kommune_alle)
    _safe_clear_dir(outdir_kommune_matr)

    user_codes = {u.obserkode for u in users if u.obserkode}

    async with SessionLocal() as session:
        lok_rows = (await session.execute(
            select(Lokation.kommune_id, Lokation.site_number)
        )).all()
        obs_rows = (await session.execute(
            select(Observation).where(
                Observation.dato >= year_start,
                Observation.dato <= year_end,
            )
        )).scalars().all()

        obs_by_user = defaultdict(list)
        for obs in obs_rows:
            kode = obs.obserkode or ""
            if kode in user_codes:
                obs_by_user[kode].append(obs)

        kommune_sites = defaultdict(set)
        for kommune_id, site_number in lok_rows:
            if kommune_id is None or site_number is None:
                continue
            kommune_sites[int(kommune_id)].add(int(site_number))

        for kommune in kommuner:
            kommune_id = _parse_int(kommune.get("id"))
            if kommune_id is None:
                continue
            kommune_name = kommune.get("navn") or str(kommune_id)
            site_numbers = kommune_sites.get(kommune_id) or set()
            if not site_numbers:
                site_numbers = set(_load_kommune_sites_from_file(kommune_name))

            rows_alle = []
            rows_matr = []
            for u in users:
                user_obs = obs_by_user.get(u.obserkode, [])
                kommune_obs = [o for o in user_obs if _parse_int(o.loknr) in site_numbers]

                is_opted = _user_in_kommune(u, kommune_id)
                if is_opted:
                    firsts_all = _firsts_from_obs_rows(kommune_obs)
                    a_all, art_all, dato_all = _score_from_firsts(firsts_all)
                    rows_alle.append({
                        "navn": safe_output(u.navn or u.obserkode),
                        "obserkode": u.obserkode,
                        "antal_arter": a_all,
                        "sidste_art": safe_output(art_all),
                        "sidste_dato": safe_output(dato_all),
                    })

                if is_opted:
                    tagged_matrikel_obs = [
                        row for row in kommune_obs
                        if _observation_has_matrikel_tag(row, raw_filter, 1)
                    ]
                    firsts_m = _firsts_from_obs_rows(tagged_matrikel_obs)
                    a_m, art_m, dato_m = _score_from_firsts(firsts_m)
                    rows_matr.append({
                        "navn": safe_output(u.navn or u.obserkode),
                        "obserkode": u.obserkode,
                        "antal_arter": a_m,
                        "sidste_art": safe_output(art_m),
                        "sidste_dato": safe_output(dato_m),
                    })

            filename = f"{_kommune_slug(kommune_name)}.json"
            with open(os.path.join(outdir_kommune_alle, filename), "w", encoding="utf-8") as f:
                json.dump(_finalize(_ensure_scoreboard_fields(rows_alle)), f, ensure_ascii=False, indent=2)
            with open(os.path.join(outdir_kommune_matr, filename), "w", encoding="utf-8") as f:
                json.dump(_finalize(_ensure_scoreboard_fields(rows_matr)), f, ensure_ascii=False, indent=2)


async def generate_global_scoreboards_all_time():
    import shutil
    excluded_keys = _get_excluded_species_keys()

    def _normalize_art(name: str) -> str:
        return (name or "").split("(")[0].split(",")[0].strip()

    def _is_valid_art(name: str) -> bool:
        n = _normalize_base_art_name(name)
        if _is_excluded_species(name, excluded_keys):
            return False
        return ("sp." not in n) and ("/" not in n) and (" x " not in n)

    def _parse_dato(d: str) -> datetime.datetime:
        try:
            return datetime.datetime.strptime(d or "", "%d-%m-%Y")
        except Exception:
            return datetime.datetime.min

    def _score_from_list(list_rows):
        if not isinstance(list_rows, list) or not list_rows:
            return 0, "", ""

        cleaned = [r for r in list_rows if r.get("artnavn") and _is_valid_art(r["artnavn"])]
        if not cleaned:
            return 0, "", ""

        unique_arter = {_normalize_art(r["artnavn"]) for r in cleaned}
        antal_arter = len(unique_arter)

        latest = max(cleaned, key=lambda r: _parse_dato(r.get("dato")))
        return antal_arter, latest.get("artnavn", ""), latest.get("dato", "")

    def _safe_clear_dir(path: str):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        os.makedirs(path, exist_ok=True)

    base_dir = os.path.join(SERVER_DIR, "data", "global")
    scoreboards_dir = os.path.join(base_dir, "scoreboards")
    obser_dir = os.path.join(base_dir, "obser")

    outdir_global_alle = os.path.join(scoreboards_dir, "global_alle")
    outdir_global_matr = os.path.join(scoreboards_dir, "global_matrikel")
    outdir_lokal_alle = os.path.join(scoreboards_dir, "lokalafdeling_alle")
    outdir_lokal_matr = os.path.join(scoreboards_dir, "lokalafdeling_matrikel")
    outdir_kommune_alle = os.path.join(scoreboards_dir, "kommune_alle")
    outdir_kommune_matr = os.path.join(scoreboards_dir, "kommune_matrikel")

    _safe_clear_dir(outdir_global_alle)
    _safe_clear_dir(outdir_global_matr)
    _safe_clear_dir(outdir_lokal_alle)
    _safe_clear_dir(outdir_lokal_matr)
    _safe_clear_dir(outdir_kommune_alle)
    _safe_clear_dir(outdir_kommune_matr)

    async with SessionLocal() as session:
        users = [
            u for u in (await session.execute(select(User))).scalars().all()
            if SAFE_OBSERKODE_RE.fullmatch(u.obserkode or "")
        ]

    # Global matrikel
    gm_rows = []
    for u in users:
        L_m = _load_json(os.path.join(obser_dir, u.obserkode, "matrikelarter.json")) or []
        a, art, dato = _score_from_list(L_m)
        gm_rows.append({
            "navn": safe_output(u.navn or u.obserkode),
            "obserkode": u.obserkode,
            "antal_arter": a,
            "sidste_art": safe_output(art),
            "sidste_dato": safe_output(dato),
        })
    with open(os.path.join(outdir_global_matr, "scoreboard.json"), "w", encoding="utf-8") as f:
        json.dump(_finalize(_ensure_scoreboard_fields(gm_rows)), f, ensure_ascii=False, indent=2)

    # Lokalafdeling matrikel
    for afd in AFDELINGER:
        rows_matr = []
        for u in users:
            opted_lokal = _user_opted_lokalafdelinger(u)
            if afd not in opted_lokal:
                continue
            la_map = _load_json(os.path.join(obser_dir, u.obserkode, "lokalafdeling.json")) or {}
            L_matr = (la_map.get(afd) or {}).get("matrikel") or []
            a2, art2, dato2 = _score_from_list(L_matr)
            rows_matr.append({
                "navn": u.navn or u.obserkode,
                "obserkode": u.obserkode,
                "antal_arter": a2,
                "sidste_art": art2,
                "sidste_dato": dato2,
            })
        filename = f"{afd.replace(' ', '_')}.json"
        with open(os.path.join(outdir_lokal_matr, filename), "w", encoding="utf-8") as f:
            json.dump(_finalize(_ensure_scoreboard_fields(rows_matr)), f, ensure_ascii=False, indent=2)

    # Lokalafdeling alle
    for afd in AFDELINGER:
        rows_alle = []
        for u in users:
            opted_lokal = _user_opted_lokalafdelinger(u)
            if afd not in opted_lokal:
                continue
            la_map = _load_json(os.path.join(obser_dir, u.obserkode, "lokalafdeling.json")) or {}
            L_alle = (la_map.get(afd) or {}).get("alle") or []
            a1, art1, dato1 = _score_from_list(L_alle)
            rows_alle.append({
                "navn": u.navn or u.obserkode,
                "obserkode": u.obserkode,
                "antal_arter": a1,
                "sidste_art": art1,
                "sidste_dato": dato1,
            })
        filename = f"{afd.replace(' ', '_')}.json"
        with open(os.path.join(outdir_lokal_alle, filename), "w", encoding="utf-8") as f:
            json.dump(_finalize(_ensure_scoreboard_fields(rows_alle)), f, ensure_ascii=False, indent=2)

    # Global alle
    ga_rows = []
    for u in users:
        L_g = _load_json(os.path.join(obser_dir, u.obserkode, "global.json")) or []
        a, art, dato = _score_from_list(L_g)
        ga_rows.append({
            "navn": u.navn or u.obserkode,
            "obserkode": u.obserkode,
            "antal_arter": a,
            "sidste_art": art,
            "sidste_dato": dato,
        })
    with open(os.path.join(outdir_global_alle, "scoreboard.json"), "w", encoding="utf-8") as f:
        json.dump(_finalize(_ensure_scoreboard_fields(ga_rows)), f, ensure_ascii=False, indent=2)

    # Kommune (all-time)
    raw_filter = await get_global_filter()
    obs_by_user = defaultdict(list)
    async with SessionLocal() as session:
        all_obs_rows = (await session.execute(select(Observation))).scalars().all()
    user_codes = {u.obserkode for u in users if u.obserkode}
    for row in all_obs_rows:
        kode = row.obserkode or ""
        if kode in user_codes:
            obs_by_user[kode].append(row)

    kommune_site_map = {}
    async with SessionLocal() as session:
        lok_rows = (await session.execute(select(Lokation.kommune_id, Lokation.site_number))).all()
    for kommune_id_value, site_number in lok_rows:
        if kommune_id_value is None or site_number is None:
            continue
        kommune_site_map.setdefault(int(kommune_id_value), set()).add(int(site_number))

    def _firsts_from_obs_rows_global(obs_rows):
        firsts = {}
        for row in obs_rows:
            navn = (row.artnavn or "").split("(")[0].split(",")[0].strip()
            if not _is_valid_art(navn) or not row.dato:
                continue
            if navn not in firsts or row.dato < firsts[navn]["dato"]:
                firsts[navn] = {"dato": row.dato, "artnavn": navn}
        return firsts

    def _score_from_firsts_global(firsts):
        if not firsts:
            return 0, "", ""
        latest = max(firsts.values(), key=lambda r: r["dato"])
        return len(firsts), latest.get("artnavn", ""), latest["dato"].strftime("%d-%m-%Y")

    kommuner = _read_kommuner()
    for kommune in kommuner:
        kommune_id = _parse_int(kommune.get("id"))
        if kommune_id is None:
            continue
        kommune_name = kommune.get("navn") or str(kommune_id)
        site_set = kommune_site_map.get(kommune_id) or set(_load_kommune_sites_from_file(kommune_name))
        rows_alle = []
        rows_matr = []
        for u in users:
            user_obs = obs_by_user.get(u.obserkode, [])
            kommune_obs = [row for row in user_obs if _parse_int(row.loknr) in site_set]

            opted_kommuner = _user_opted_kommuner(u)
            is_opted = str(kommune_id) in opted_kommuner
            if is_opted:
                firsts_all = _firsts_from_obs_rows_global(kommune_obs)
                a1, art1, dato1 = _score_from_firsts_global(firsts_all)
                rows_alle.append({
                    "navn": u.navn or u.obserkode,
                    "obserkode": u.obserkode,
                    "antal_arter": a1,
                    "sidste_art": art1,
                    "sidste_dato": dato1,
                })

            if is_opted:
                tagged_matrikel_obs = [
                    row for row in kommune_obs
                    if _observation_has_matrikel_tag(row, raw_filter, 1)
                ]
                firsts_m = _firsts_from_obs_rows_global(tagged_matrikel_obs)
                a2, art2, dato2 = _score_from_firsts_global(firsts_m)
                rows_matr.append({
                    "navn": u.navn or u.obserkode,
                    "obserkode": u.obserkode,
                    "antal_arter": a2,
                    "sidste_art": art2,
                    "sidste_dato": dato2,
                })

        filename = f"{_kommune_slug(kommune_name)}.json"
        with open(os.path.join(outdir_kommune_alle, filename), "w", encoding="utf-8") as f:
            json.dump(_finalize(_ensure_scoreboard_fields(rows_alle)), f, ensure_ascii=False, indent=2)
        with open(os.path.join(outdir_kommune_matr, filename), "w", encoding="utf-8") as f:
            json.dump(_finalize(_ensure_scoreboard_fields(rows_matr)), f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------
#  DOFbasen sync (CSV -> DB -> lister -> scoreboards)
# ---------------------------------------------------------

async def fetch_and_store(
    obserkode: str,
    aar: Optional[int] = None,
    include_global_rebuild: bool = True,
):
    """
    Henter observationer fra DOFbasen (CSV), indsætter ALLE rækker i DB for det angivne år,
    og bygger derefter per-bruger lister + scoreboards for det år.
    """
    import io
    import datetime
    import pandas as pd
    import requests

    obserkode = normalize_obserkode(obserkode)

    if aar is None:
        aar = await get_global_year()
    filter_ = await get_global_filter()
    filter_tag = resolve_filter_tag(filter_, aar)

    url = (
        "https://dofbasen.dk/excel/search_result1.php"
        "?design=excel&soeg=soeg&periode=maanedaar"
        f"&aar_first={aar}&aar_second={aar}"
        "&obstype=observationer&species=alle"
        f"&obserdata={obserkode}&sortering=dato"
    )
    df = None
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.content.decode("latin1")), sep=";", dtype=str)
        print(f"[INFO] Hentet {len(df)} rækker fra DOFbasen for {obserkode} ({aar})")
    except Exception as e:
        print(f"[WARN] HTTP-fejl ({e}) – prøver lokal CSV fallback...")

    # 3) Lokal fallback (valgfri)
    if df is None or df.empty:
        candidates = [
            os.path.join(ROOT_DIR, "search_result (3).csv"),
            os.path.join(SERVER_DIR, "search_result (3).csv"),
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    df = pd.read_csv(p, sep=";", dtype=str, encoding="latin1")
                    print(f"[INFO] Lokal CSV: {len(df)} rækker fra {p}")
                    break
                except Exception as e:
                    print(f"[ERROR] Kunne ikke parse {p}: {e}")

    # 4) Ingen data -> skriv tomme lister og scoreboards for det valgte år
    if df is None or df.empty:
        print(f"[ERROR] Ingen data til {obserkode}/{aar}. Skriver tomme lister.")
        await generate_user_lists(obserkode, aar)
        await generate_scoreboards_from_lists(aar)
        if include_global_rebuild:
            await generate_user_global_lists(obserkode)
            await generate_global_scoreboards_all_time()
        return

    # 5) (INFO) Vis hvad admin-filter ville give—men ANVEND DET IKKE på CSV -> DB
    if filter_tag:
        try:
            before = len(df)
            after = len(df[df["Turnoter"].fillna("").str.contains(filter_tag)])
            print(f"[INFO] (info) Filter '{filter_tag}': {before} -> {after} rækker (for {obserkode}) [ikke anvendt til DB]")
        except Exception:
            pass

    # 6) Indsæt ALLE rækker i DB for det valgte år (rydder ALT for brugeren for det år) - batch-inserts
    BATCH_SIZE = 25000
    start_date = datetime.date(aar, 1, 1)
    end_date = datetime.date(aar, 12, 31)
    async with SessionLocal() as session:
        await session.execute(
            Observation.__table__.delete().where(
                Observation.obserkode == obserkode,
                Observation.dato >= start_date,
                Observation.dato <= end_date
            )
        )
        await session.commit()

        inserted = 0
        batch = []
        for _, row in df.iterrows():
            raw_dato = (row.get("Dato", "") or "").strip()
            if not raw_dato:
                continue
            if len(raw_dato) > 10:
                raw_dato = raw_dato[:10]
            dato = None
            for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                try:
                    dato = datetime.datetime.strptime(raw_dato, fmt).date()
                    break
                except Exception:
                    pass
            if not dato or not (start_date <= dato <= end_date):
                continue

            antal = int(row.get("Antal", "") or 0)
            if antal == 0:
                continue

            row_obserkode = row.get("Obserkode", obserkode) or obserkode
            try:
                row_obserkode = normalize_obserkode(row_obserkode)
            except ValueError:
                row_obserkode = obserkode
            obs = Observation(
                obserkode=row_obserkode,
                artnavn=safe_str(row.get("Artnavn", "") or ""),
                dato=dato,
                turid=safe_str(row.get("Turid")),
                obsid=safe_str(row.get("Obsid")) if "Obsid" in row else None,
                turtidfra=safe_str(row.get("Turtidfra")),
                turtidtil=safe_str(row.get("Turtidtil")),
                    turnoter=merged_note_text(row.get("Turnoter", ""), row.get("Fuglnoter", "")),
                afdeling=safe_str(row.get("DOF_afdeling", "") or ""),
                loknavn=safe_str(row.get("Loknavn", "") or ""),
                loknr=_parse_int(row.get("Loknr")),
                antal=antal
            )
            batch.append(obs)
            inserted += 1
            if len(batch) >= BATCH_SIZE:
                session.add_all(batch)
                await session.commit()
                batch = []
        if batch:
            session.add_all(batch)
            await session.commit()
        print(f"[INFO] Indsat {inserted} observationer for {obserkode} ({aar})")

    # 7) Generér lister og scoreboards kun for det valgte år
    await generate_user_lists(obserkode, aar)
    await generate_scoreboards_from_lists(aar)
    if include_global_rebuild:
        await generate_user_global_lists(obserkode)
        await generate_global_scoreboards_all_time()


async def _background_global_rebuild(obserkode: str):
    """Kør global rebuild i baggrunden (kald via asyncio.create_task)."""
    try:
        await generate_user_global_lists(obserkode)
        await generate_global_scoreboards_all_time()
        print(f"[BG] Global rebuild færdig for {obserkode}")
    except Exception as e:
        print(f"[BG] Global rebuild fejlede for {obserkode}: {e}")

async def fetch_and_store_sites_for_kommune(kommune_id: int, kommune_name: Optional[str] = None):
    url = f"https://statistik.dofbasen.dk/sites/group_{kommune_id}.json"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        sites = resp.json()
    except Exception as e:
        print(f"[ERROR] Kunne ikke hente sites for kommune {kommune_id}: {e}")
        return

    async with SessionLocal() as session:
        # Slet eksisterende sites for denne kommune
        await session.execute(
            Lokation.__table__.delete().where(Lokation.kommune_id == kommune_id)
        )
        # Indsæt nye
        for site in sites:
            session.add(Lokation(
                site_number=site["siteNumber"],
                site_name=site["siteName"],
                kommune_id=kommune_id
            ))
        await session.commit()
    print(f"[INFO] Opdateret {len(sites)} lokationer for kommune {kommune_id}")

    if kommune_name:
        out_dir = os.path.join(SERVER_DIR, "data", "kommune")
        safe_makedirs(out_dir)
        with open(_kommune_sites_path(kommune_name), "w", encoding="utf-8") as f:
            json.dump(sites, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Gemte kommune-sites for {kommune_name}")

async def update_all_kommuner_sites():
    kommuner = _read_kommuner()
    for row in kommuner:
        kommune_id = _parse_int(row.get("id"))
        if kommune_id is None:
            continue
        await fetch_and_store_sites_for_kommune(kommune_id, row.get("navn"))

async def schedule_daily_kommune_sync():
    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=5, minute=0, second=0, microsecond=0)
        if target <= now:
            target = target + datetime.timedelta(days=1)
        sleep_seconds = max(0, int((target - now).total_seconds()))
        print(f"[SCHEDULE] Next kommune sync at {target.isoformat()} (in {sleep_seconds}s)")
        await asyncio.sleep(sleep_seconds)
        try:
            print("[SCHEDULE] Running daily kommune sync...")
            await update_all_kommuner_sites()
            print("[SCHEDULE] Daily kommune sync done.")
        except Exception as exc:
            print(f"[SCHEDULE] Kommune sync failed: {exc}")

@app.post("/api/update_lokationer")
async def update_lokationer(request: Request):
    # Kun superadmin må opdatere lokationer
    admin_koder = [k.strip() for k in os.environ.get("SUPERADMIN", "").split(",") if k.strip()]
    session = request.session
    obserkode = session.get("obserkode")
    if not (obserkode and obserkode in admin_koder):
        raise HTTPException(status_code=403, detail="Kun superadmin kan opdatere lokationer")
    try:
        await update_all_kommuner_sites()
    except Exception as exc:
        return JSONResponse({"msg": f"Opdatering fejlede: {exc}"}, status_code=500)
    return {"msg": "Alle kommune-lokationer opdateret"}

async def daily_update_all_jsons():
    """
    Daglig fuld synkronisering:
    1. Hent og indsæt ALLE observationer for ALLE brugere (1900-NU) direkte.
    2. Generér lister og scoreboards for ALLE år med data (cacher alle år).
    """
    import io
    import datetime
    import pandas as pd
    import requests

    BATCH_SIZE = 25000

    # 1. Hent alle brugerkoder
    async with SessionLocal() as session:
        koder = [
            normalize_obserkode(k.kode)
            for k in (await session.execute(select(Obserkode))).scalars().all()
            if SAFE_OBSERKODE_RE.fullmatch((k.kode or "").strip().upper())
        ]

    # 2. Hent og indsæt observationer for alle brugere (rydder ALT for brugeren først)
    for kode in koder:
        print(f"[DAILY SYNC] Henter og indsætter observationer for {kode}")
        url = (
            "https://dofbasen.dk/excel/search_result1.php"
            "?design=excel&soeg=soeg&periode=maanedaar"
            f"&aar_first=1900&aar_second={datetime.datetime.now().year}"
            "&obstype=observationer&species=alle"
            f"&obserdata={kode}&sortering=dato"
        )
        df = None
        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.content.decode("latin1")), sep=";", dtype=str)
            print(f"[INFO] Hentet {len(df)} rækker fra DOFbasen for {kode} (1900-NU)")
        except Exception as e:
            print(f"[WARN] HTTP-fejl ({e}) – prøver lokal CSV fallback...")
            df = None

        # Lokal fallback (valgfri)
        if df is None or df.empty:
            candidates = [
                os.path.join(ROOT_DIR, "search_result (3).csv"),
                os.path.join(SERVER_DIR, "search_result (3).csv"),
            ]
            for p in candidates:
                if os.path.exists(p):
                    try:
                        df = pd.read_csv(p, sep=";", dtype=str, encoding="latin1")
                        print(f"[INFO] Lokal CSV: {len(df)} rækker fra {p}")
                        break
                    except Exception as e:
                        print(f"[ERROR] Kunne ikke parse {p}: {e}")

        # Indsæt i DB (ryd ALT for brugeren først) - nu med batch-inserts
        async with SessionLocal() as session:
            await session.execute(
                Observation.__table__.delete().where(
                    Observation.obserkode == kode
                )
            )
            await session.commit()

            inserted = 0
            batch = []
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    raw_dato = (row.get("Dato", "") or "").strip()
                    if not raw_dato:
                        continue
                    if len(raw_dato) > 10:
                        raw_dato = raw_dato[:10]
                    dato = None
                    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                        try:
                            dato = datetime.datetime.strptime(raw_dato, fmt).date()
                            break
                        except Exception:
                            pass
                    if not dato:
                        continue

                    antal = int(row.get("Antal", "") or 0)
                    if antal == 0:
                        continue

                    obs = Observation(
                        obserkode=safe_str(row.get("Obserkode", "")),
                        artnavn=safe_str(row.get("Artnavn", "") or ""),
                        dato=dato,
                        turid=safe_str(row.get("Turid")),
                        obsid=safe_str(row.get("Obsid")) if "Obsid" in row else None,
                        turtidfra=safe_str(row.get("Turtidfra")),
                        turtidtil=safe_str(row.get("Turtidtil")),
                        turnoter=merged_note_text(row.get("Turnoter", ""), row.get("Fuglnoter", "")),
                        afdeling=safe_str(row.get("DOF_afdeling", "") or ""),
                        loknavn=safe_str(row.get("Loknavn", "") or ""),
                        loknr=_parse_int(row.get("Loknr")),
                        antal=antal
                    )
                    batch.append(obs)
                    inserted += 1
                    if len(batch) >= BATCH_SIZE:
                        session.add_all(batch)
                        await session.commit()
                        batch = []
                if batch:
                    session.add_all(batch)
                    await session.commit()
            print(f"[INFO] Indsat {inserted} observationer for {kode} (1900-NU)")

    print("[DAILY SYNC] Alle observationer hentet og indsat.")

    # 3. Find alle årstal med data på tværs af brugere
    async with SessionLocal() as session:
        years = (await session.execute(
            select(func.extract("year", Observation.dato)).distinct()
        )).scalars().all()
        years = [int(y) for y in years if y is not None]

        # Find alle brugere igen (til generate_user_lists)
        brugere = [u.obserkode for u in (await session.execute(select(User))).scalars().all()]

    # 4. Generér lister og scoreboards for alle år og alle brugere
    for aar in sorted(years):
        for kode in brugere:
            await generate_user_lists(kode, aar)
        await generate_scoreboards_from_lists(aar)
        print(f"[DAILY SYNC] Scoreboards og lister genereret for {aar}")

    print("[DAILY SYNC] Færdig med scoreboards og lister for alle år.")

    # 5. Generér samlede lister (alle år) pr. bruger
    for kode in brugere:
        await generate_user_global_lists(kode)
    print("[DAILY SYNC] Samlede lister (alle år) genereret for alle brugere.")

    await generate_global_scoreboards_all_time()
    print("[DAILY SYNC] Samlede scoreboards (alle år) genereret.")


async def sync_user_all_time(obserkode: str):
    """
    Fuld sync for en enkelt bruger (1900-NU) + rebuild af relevante år og all-time.
    """
    import io
    import datetime
    import pandas as pd
    import requests

    obserkode = normalize_obserkode(obserkode)
    current_year = datetime.datetime.now().year

    url = (
        "https://dofbasen.dk/excel/search_result1.php"
        "?design=excel&soeg=soeg&periode=maanedaar"
        f"&aar_first=1900&aar_second={current_year}"
        "&obstype=observationer&species=alle"
        f"&obserdata={obserkode}&sortering=dato"
    )

    df = None
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.content.decode("latin1")), sep=";", dtype=str)
        print(f"[SYNC-ALL] Hentet {len(df)} rækker fra DOFbasen for {obserkode} (1900-NU)")
    except Exception as e:
        print(f"[SYNC-ALL] HTTP-fejl ({e}) – prøver lokal CSV fallback...")

    if df is None or df.empty:
        candidates = [
            os.path.join(ROOT_DIR, "search_result (3).csv"),
            os.path.join(SERVER_DIR, "search_result (3).csv"),
        ]
        for p in candidates:
            if os.path.exists(p):
                try:
                    df = pd.read_csv(p, sep=";", dtype=str, encoding="latin1")
                    print(f"[SYNC-ALL] Lokal CSV: {len(df)} rækker fra {p}")
                    break
                except Exception as e:
                    print(f"[SYNC-ALL] Kunne ikke parse {p}: {e}")

    async with SessionLocal() as session:
        await session.execute(
            Observation.__table__.delete().where(
                Observation.obserkode == obserkode
            )
        )
        await session.commit()

        inserted = 0
        batch = []
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                raw_dato = (row.get("Dato", "") or "").strip()
                if not raw_dato:
                    continue
                if len(raw_dato) > 10:
                    raw_dato = raw_dato[:10]
                dato = None
                for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        dato = datetime.datetime.strptime(raw_dato, fmt).date()
                        break
                    except Exception:
                        pass
                if not dato:
                    continue

                antal = int(row.get("Antal", "") or 0)
                if antal == 0:
                    continue

                obs = Observation(
                    obserkode=safe_str(row.get("Obserkode", "")) or obserkode,
                    artnavn=safe_str(row.get("Artnavn", "") or ""),
                    dato=dato,
                    turid=safe_str(row.get("Turid")),
                    obsid=safe_str(row.get("Obsid")) if "Obsid" in row else None,
                    turtidfra=safe_str(row.get("Turtidfra")),
                    turtidtil=safe_str(row.get("Turtidtil")),
                    turnoter=merged_note_text(row.get("Turnoter", ""), row.get("Fuglnoter", "")),
                    afdeling=safe_str(row.get("DOF_afdeling", "") or ""),
                    loknavn=safe_str(row.get("Loknavn", "") or ""),
                    loknr=_parse_int(row.get("Loknr")),
                    antal=antal
                )
                batch.append(obs)
                inserted += 1
                if len(batch) >= 25000:
                    session.add_all(batch)
                    await session.commit()
                    batch = []
            if batch:
                session.add_all(batch)
                await session.commit()
        print(f"[SYNC-ALL] Indsat {inserted} observationer for {obserkode} (1900-NU)")

    async with SessionLocal() as session:
        years = (await session.execute(
            select(func.extract("year", Observation.dato))
            .where(Observation.obserkode == obserkode)
            .distinct()
        )).scalars().all()
        years = [int(y) for y in years if y is not None]

    for aar in sorted(years):
        await generate_user_lists(obserkode, aar)
        await generate_scoreboards_from_lists(aar)
        print(f"[SYNC-ALL] Lister/scoreboards for {obserkode} opdateret ({aar})")

    await generate_user_global_lists(obserkode)
    await generate_global_scoreboards_all_time()
    print(f"[SYNC-ALL] All-time lister/scoreboards opdateret for {obserkode}")


async def schedule_daily_year_sync():
    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=18, minute=0, second=0, microsecond=0)
        if target <= now:
            target = target + datetime.timedelta(days=1)
        sleep_seconds = max(0, int((target - now).total_seconds()))
        print(f"[SCHEDULE] Next daily year sync at {target.isoformat()} (in {sleep_seconds}s)")
        await asyncio.sleep(sleep_seconds)
        try:
            aar = await get_global_year()
            print(f"[SCHEDULE] Running daily year sync for {aar}...")
            async with SessionLocal() as session:
                koder = [
                    normalize_obserkode(k.kode)
                    for k in (await session.execute(select(Obserkode))).scalars().all()
                    if SAFE_OBSERKODE_RE.fullmatch((k.kode or "").strip().upper())
                ]
            for kode in koder:
                await fetch_and_store(kode, aar)
                await asyncio.sleep(30)
            print(f"[SCHEDULE] Daily year sync done for {aar}.")
        except Exception as exc:
            print(f"[SCHEDULE] Daily year sync failed: {exc}")

async def schedule_daily_species_sync():
    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=3, minute=0, second=0, microsecond=0)
        if target <= now:
            target = target + datetime.timedelta(days=1)
        sleep_seconds = max(0, int((target - now).total_seconds()))
        print(f"[SCHEDULE] Next species sync at {target.isoformat()} (in {sleep_seconds}s)")
        await asyncio.sleep(sleep_seconds)
        try:
            print("[SCHEDULE] Running daily species sync...")
            result = await asyncio.to_thread(_sync_species_styles_and_excluded_from_dof)
            print(
                "[SCHEDULE] Daily species sync done: "
                f"excluded={result.get('excluded_total_count', 0)}, "
                f"styles={result.get('styles_total_count', 0)}"
            )
        except Exception as exc:
            print(f"[SCHEDULE] Daily species sync failed: {exc}")


# ---------------------------------------------------------
#  API: Admin
# ---------------------------------------------------------
@app.post("/api/admin_login")
async def admin_login(request: Request, data: Dict[str, Any]):
    enforce_admin_login_rate_limit(request, 5)
    password = data.get("password", "")
    session = request.session
    async with SessionLocal() as dbsession:
        row = (await dbsession.execute(select(AdminPassword).order_by(AdminPassword.id.desc()))).scalars().first()
        if not row:
            dbsession.add(AdminPassword(password_hash=hash_password(password)))
            await dbsession.commit()
            session["is_admin"] = True
            token = generate_csrf_token(session)
            return {"ok": True, "first": True, "csrf_token": token}
        if verify_password(password, row.password_hash):
            if not row.password_hash.startswith("$2"):
                row.password_hash = hash_password(password)
                await dbsession.commit()
            session["is_admin"] = True
            token = generate_csrf_token(session)
            return {"ok": True, "csrf_token": token}
        return JSONResponse({"ok": False}, status_code=401)

@app.post("/api/adminlogin")
async def adminlogin(request: Request, data: dict):
    enforce_admin_login_rate_limit(request, 5)
    kode = data.get("obserkode", "").strip()
    password = data.get("password", "")
    session = request.session
    if kode == SUPERADMIN and password == SUPERADMIN_PASSWORD:
        session["is_admin"] = True
        token = generate_csrf_token(session)
        return {"ok": True, "csrf_token": token}
    return JSONResponse({"ok": False, "msg": "Forkert admin-login"}, status_code=401)

@app.get("/api/is_admin")
async def is_admin(request: Request):
    session = request.session
    return {"is_admin": bool(session.get("is_admin", False))}

@app.post("/api/admin_logout")
async def admin_logout(request: Request):
    session = request.session
    session.clear()
    return {"ok": True}

@app.post("/api/logout")
async def logout(request: Request):
    session = request.session
    session.clear()
    return {"ok": True}

@app.get("/api/obser_is_admin")
async def obser_is_admin(request: Request):
    """
    Returnér om brugeren er admin (tjekker både session, .env og database).
    """
    session = request.session
    obserkode = session.get("obserkode")
    admin_koder = [k.strip() for k in os.environ.get("SUPERADMIN", "").split(",") if k.strip()]
    # Først: tjek om obserkode matcher admin_koder
    if obserkode and obserkode in admin_koder:
        return {"is_admin": True}
    # Ellers: tjek om bruger er markeret som admin i databasen
    if obserkode:
        async with SessionLocal() as dbsession:
            user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
            if user and getattr(user, "is_admin", False):
                return {"is_admin": True}
    return {"is_admin": False}

# ---------------------------------------------------------
#  API: Global filter & year
# ---------------------------------------------------------
@app.post("/api/set_filter")
async def set_filter_api(filter: str, request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan ændre filter")
    await set_global_filter(filter)
    return {"msg": "Globalt filter opdateret"}

@app.get("/api/get_filter")
async def get_filter_api():
    return {"filter": await get_global_filter()}

@app.post("/api/set_year")
async def set_year_api(year: int, request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan ændre år")
    await set_global_year(year)
    return {"msg": "Globalt år opdateret"}

@app.get("/api/get_year")
async def get_year_api():
    return {"year": await get_global_year()}

# ---------------------------------------------------------
#  API: Obserkoder
# ---------------------------------------------------------
@app.get("/api/obserkoder")
async def get_obserkoder():
    async with SessionLocal() as session:
        return [
            {"kode": u.obserkode, "navn": u.navn}
            for u in (await session.execute(select(User))).scalars().all()
        ]

@app.post("/api/add_obserkode")
async def add_obserkode(kode: str, request: Request):
    session = request.session
    if not session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan tilføje obserkoder")
    async with SessionLocal() as dbsession:
        exists = (await dbsession.execute(select(Obserkode).where(Obserkode.kode == kode))).scalar()
        if exists:
            raise HTTPException(status_code=400, detail="Obserkode findes allerede")
        dbsession.add(Obserkode(kode=kode))
        # Opret også User hvis den ikke findes
        user_exists = (await dbsession.execute(select(User).where(User.obserkode == kode))).scalar()
        if not user_exists:
            dbsession.add(User(obserkode=kode, navn=kode, lokalafdeling=None))
        await dbsession.commit()
    return {"msg": "Obserkode og bruger tilføjet"}

@app.delete("/api/delete_obserkode")
async def delete_obserkode(kode: str, request: Request):
    session = request.session
    if not session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan slette obserkoder")
    async with SessionLocal() as session:
        # Tjek om brugeren findes i mindst én af tabellerne
        ok = (await session.execute(select(Obserkode).where(Obserkode.kode == kode))).scalar()
        user = (await session.execute(select(User).where(User.obserkode == kode))).scalar()
        if not ok and not user:
            raise HTTPException(status_code=404, detail="Obserkode ikke fundet")
        # Slet alle observationer
        await session.execute(Observation.__table__.delete().where(Observation.obserkode == kode))
        # Slet fra Obserkode
        await session.execute(Obserkode.__table__.delete().where(Obserkode.kode == kode))
        # Slet fra User
        await session.execute(User.__table__.delete().where(User.obserkode == kode))
        await session.commit()
    # Fjern brugeren fra alle grupper (i grupper.json)
    grupper = load_grupper()
    for g in grupper:
        if kode in g.get("obserkoder", []):
            g["obserkoder"] = [k for k in g["obserkoder"] if k != kode]
    save_grupper(grupper)
    return {"msg": "Obserkode, bruger og alle data slettet"}

@app.get("/api/afdelinger")
async def afdelinger():
    def read_csv_names(filepath):
        if not os.path.exists(filepath):
            return []
        with open(filepath, encoding="utf-8") as f:
            lines = f.readlines()
        # Skip header, return only 'navn' column
        return [line.strip().split(";")[1] for line in lines[1:] if ";" in line]

    kommuner_path = os.path.join(SERVER_DIR, "kommuner.csv")
    afdelinger_path = os.path.join(SERVER_DIR, "lokalafdelinger.csv")

    kommuner = _read_kommuner()
    afdelinger = read_csv_names(afdelinger_path)

    return {
        "kommuner": kommuner,
        "lokalafdelinger": afdelinger
    }

@app.get("/api/get_userprefs")
async def get_userprefs(request: Request):
    web_session = request.session
    obserkode = web_session.get("obserkode")
    if not obserkode:
        return {
            "lokalafdeling": None,
            "kommune": None,
            "lokalafdelinger": [],
            "kommuner": [],
            "obserkode": None,
            "navn": None,
            "matrikel_perioder": {},
            "available_matrikler": [],
            "matrikel1_perioder": [],
            "matrikel2_perioder": []
        }

    raw_filter = await get_global_filter()
    async with SessionLocal() as dbsession:
        user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        obs_rows = (await dbsession.execute(
            select(Observation.dato, Observation.turnoter).where(Observation.obserkode == obserkode)
        )).all()
        if user:
            kommune_value = _normalize_single_kommune(getattr(user, "kommune", None))
            periods = _load_user_matrikel_periods(user)
            available_indexes = _collect_matrikel_indexes_from_observations(obs_rows, raw_filter)
            available_keys = {_matrikel_key(index) for index in available_indexes}
            filtered_periods = {
                key: value
                for key, value in periods.items()
                if key in available_keys
            }
            opted_kommuner = _user_opted_kommuner(user)
            if kommune_value and kommune_value not in opted_kommuner:
                opted_kommuner = [kommune_value, *[value for value in opted_kommuner if value != kommune_value]]
            opted_kommuner = opted_kommuner[:5]
            return {
                "lokalafdeling": user.lokalafdeling,
                "kommune": kommune_value,
                "lokalafdelinger": _user_opted_lokalafdelinger(user),
                "kommuner": opted_kommuner,
                "obserkode": user.obserkode,
                "navn": user.navn,
                "matrikel_perioder": filtered_periods,
                "available_matrikler": available_indexes,
                "matrikel1_perioder": filtered_periods.get("matrikel1") or [],
                "matrikel2_perioder": filtered_periods.get("matrikel2") or [],
            }
    return {
        "lokalafdeling": None,
        "kommune": None,
        "lokalafdelinger": [],
        "kommuner": [],
        "obserkode": None,
        "navn": None,
        "matrikel_perioder": {},
        "available_matrikler": [],
        "matrikel1_perioder": [],
        "matrikel2_perioder": []
    }


def _normalize_artname(name: str) -> str:
    return (name or "").split("(")[0].split(",")[0].strip()


def _sort_list_by_date(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda x: _parse_ddmmyyyy(x.get("dato")))


@app.get("/api/profile_data")
async def profile_data(request: Request):
    session = request.session
    obserkode = session.get("obserkode")
    if not obserkode:
        raise HTTPException(status_code=401, detail="Ikke logget ind")

    async with SessionLocal() as dbsession:
        user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        kommune_navn = getattr(user, "kommune", None) if user else None
        if kommune_navn and str(kommune_navn).isdigit():
            for row in _read_kommuner():
                if str(row.get("id")) == str(kommune_navn):
                    kommune_navn = row.get("navn")
                    break
        user_info = {
            "navn": user.navn if user else None,
            "obserkode": obserkode,
            "lokalafdeling": getattr(user, "lokalafdeling", None) if user else None,
            "kommune": getattr(user, "kommune", None) if user else None,
            "kommune_navn": kommune_navn
        }

    global_dir = get_global_user_dir(obserkode)
    global_list_path = os.path.join(global_dir, "global.json")
    matrikel_list_path = os.path.join(global_dir, "matrikelarter.json")

    if not os.path.exists(global_list_path):
        await generate_user_global_lists(obserkode)

    global_list = _load_json(global_list_path) or []
    matrikel_list = _load_json(matrikel_list_path) or []

    global_list = _sort_list_by_date(global_list)
    matrikel_list = _sort_list_by_date(matrikel_list)

    total_rank_global = None
    total_rank_matrikel = None
    global_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_alle", "scoreboard.json")
    matrikel_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_matrikel", "scoreboard.json")

    try:
        global_sb_rows = _load_json(global_sb_path) or []
    except Exception:
        global_sb_rows = []
    try:
        matrikel_sb_rows = _load_json(matrikel_sb_path) or []
    except Exception:
        matrikel_sb_rows = []

    for row in global_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_global = row.get("placering")
            break

    for row in matrikel_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_matrikel = row.get("placering")
            break

    total_rank_global = None
    total_rank_matrikel = None
    global_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_alle", "scoreboard.json")
    matrikel_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_matrikel", "scoreboard.json")

    try:
        global_sb_rows = _load_json(global_sb_path) or []
    except Exception:
        global_sb_rows = []
    try:
        matrikel_sb_rows = _load_json(matrikel_sb_path) or []
    except Exception:
        matrikel_sb_rows = []

    for row in global_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_global = row.get("placering")
            break

    for row in matrikel_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_matrikel = row.get("placering")
            break

    total_rank_global = None
    total_rank_matrikel = None
    global_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_alle", "scoreboard.json")
    matrikel_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_matrikel", "scoreboard.json")

    try:
        global_sb_rows = _load_json(global_sb_path) or []
    except Exception:
        global_sb_rows = []
    try:
        matrikel_sb_rows = _load_json(matrikel_sb_path) or []
    except Exception:
        matrikel_sb_rows = []

    for row in global_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_global = row.get("placering")
            break

    for row in matrikel_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_matrikel = row.get("placering")
            break

    # Blockers: arter kun set af denne bruger (global all-time)
    art_counts: Dict[str, int] = {}
    async with SessionLocal() as dbsession:
        all_users = (await dbsession.execute(select(User))).scalars().all()

    for u in all_users:
        if not u.obserkode:
            continue
        u_dir = get_global_user_dir(u.obserkode)
        u_list = _load_json(os.path.join(u_dir, "global.json")) or []
        unique_arts = {
            _normalize_artname(x.get("artnavn"))
            for x in u_list
            if x.get("artnavn")
        }
        for art in {a for a in unique_arts if a}:
            art_counts[art] = art_counts.get(art, 0) + 1

    obs_counts: Dict[str, int] = {}
    async with SessionLocal() as dbsession:
        obs_rows = (await dbsession.execute(
            select(Observation.artnavn).where(Observation.obserkode == obserkode)
        )).scalars().all()
        for art in obs_rows:
            norm = _normalize_artname(art)
            if not norm:
                continue
            obs_counts[norm] = obs_counts.get(norm, 0) + 1

    current_arts = {
        _normalize_artname(x.get("artnavn"))
        for x in global_list
        if x.get("artnavn")
    }
    blockers = [
        {"art": art, "count": obs_counts.get(art, 0)}
        for art in sorted(current_arts)
        if art_counts.get(art, 0) == 1
    ]

    # Aar-data og placeringer
    data_root = os.path.join(SERVER_DIR, "data")
    year_dirs = [int(n) for n in os.listdir(data_root) if n.isdigit()]
    year_dirs.sort()

    # Prefill counts from existing cached per-user files (may be missing for some years)
    global_by_year: Dict[int, int] = {}
    matrikel_by_year: Dict[int, int] = {}
    for year in year_dirs:
        user_dir = os.path.join(data_root, str(year), "obser", obserkode)
        glist = _load_json(os.path.join(user_dir, "global.json"))
        if glist is not None:
            global_by_year[year] = len(glist)
        mlist = _load_json(os.path.join(user_dir, "matrikelarter.json"))
        if mlist is not None:
            matrikel_by_year[year] = len(mlist)

    # Observationer pr. aar (fra DB)
    obs_by_year: Dict[int, int] = {}
    async with SessionLocal() as dbsession:
        obs_dates = (await dbsession.execute(
            select(Observation.dato).where(Observation.obserkode == obserkode)
        )).scalars().all()
        for d in obs_dates:
            if not d:
                continue
            obs_by_year[d.year] = obs_by_year.get(d.year, 0) + 1

    all_years = sorted(set(year_dirs) | set(obs_by_year.keys()) | set(matrikel_by_year.keys()))

    years: List[Dict[str, Any]] = []
    matrikel_years: List[Dict[str, Any]] = []
    for year in all_years:
        gcount = global_by_year.get(year, 0)
        mcount = matrikel_by_year.get(year, 0)

        rank = None
        sb_path = os.path.join(data_root, str(year), "scoreboards", "global_alle", "scoreboard.json")
        sb_rows = _load_json(sb_path) or []
        for row in sb_rows:
            if row.get("obserkode") == obserkode:
                rank = row.get("placering")
                break

        # Include year when there are either cached list entries or DB observations
        if gcount > 0 or obs_by_year.get(year, 0) > 0:
            years.append({"year": year, "count": gcount, "rank": rank})

        if mcount > 0:
            rank_matrikel = None
            sb_m_path = os.path.join(data_root, str(year), "scoreboards", "global_matrikel", "scoreboard.json")
            sb_m_rows = _load_json(sb_m_path) or []
            for row in sb_m_rows:
                if row.get("obserkode") == obserkode:
                    rank_matrikel = row.get("placering")
                    break
            matrikel_years.append({"year": year, "count": mcount, "rank": rank_matrikel})

    chart_global = [
        {"year": y, "count": global_by_year.get(y, 0)}
        for y in all_years
        if global_by_year.get(y, 0) > 0
    ]
    chart_matrikel = [
        {"year": y, "count": matrikel_by_year.get(y, 0)}
        for y in all_years
        if matrikel_by_year.get(y, 0) > 0
    ]
    chart_obs = [
        {"year": y, "count": obs_by_year.get(y, 0)}
        for y in all_years
        if obs_by_year.get(y, 0) > 0
    ]

    return {
        "user": user_info,
        "lists": {
            "danmark": {
                "count": len(global_list),
                "items": global_list,
                "blockers": blockers
            },
            "vp": {
                "count": len(matrikel_list),
                "items": matrikel_list
            }
        },
        "years": years,
        "matrikel_years": matrikel_years,
        "charts": {
            "global_by_year": chart_global,
            "matrikel_by_year": chart_matrikel,
            "obs_by_year": chart_obs
        }
    }


@app.get("/api/statistik_data")
async def statistik_data(obserkode: str):
    """Get statistics for any obserkode (public endpoint)"""
    if not obserkode or obserkode.strip() == "":
        raise HTTPException(status_code=400, detail="obserkode required")

    try:
        obserkode = normalize_obserkode(obserkode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ugyldig obserkode")

    async with SessionLocal() as dbsession:
        user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Observatør ikke fundet")
        
        kommune_navn = getattr(user, "kommune", None)
        if kommune_navn and str(kommune_navn).isdigit():
            for row in _read_kommuner():
                if str(row.get("id")) == str(kommune_navn):
                    kommune_navn = row.get("navn")
                    break
        user_info = {
            "navn": user.navn,
            "obserkode": user.obserkode,
            "lokalafdeling": getattr(user, "lokalafdeling", None),
            "kommune": getattr(user, "kommune", None),
            "kommune_navn": kommune_navn
        }

    global_dir = get_global_user_dir(obserkode)
    global_list_path = os.path.join(global_dir, "global.json")
    matrikel_list_path = os.path.join(global_dir, "matrikelarter.json")

    if not os.path.exists(global_list_path):
        await generate_user_global_lists(obserkode)

    global_list = _load_json(global_list_path) or []
    matrikel_list = _load_json(matrikel_list_path) or []

    global_list = _sort_list_by_date(global_list)
    matrikel_list = _sort_list_by_date(matrikel_list)

    total_rank_global = None
    total_rank_matrikel = None
    global_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_alle", "scoreboard.json")
    matrikel_sb_path = os.path.join(SERVER_DIR, "data", "global", "scoreboards", "global_matrikel", "scoreboard.json")

    try:
        global_sb_rows = _load_json(global_sb_path) or []
    except Exception:
        global_sb_rows = []
    try:
        matrikel_sb_rows = _load_json(matrikel_sb_path) or []
    except Exception:
        matrikel_sb_rows = []

    for row in global_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_global = row.get("placering")
            break

    for row in matrikel_sb_rows:
        if row.get("obserkode") == obserkode:
            total_rank_matrikel = row.get("placering")
            break

    # Aar-data og placeringer
    data_root = os.path.join(SERVER_DIR, "data")
    year_dirs = [int(n) for n in os.listdir(data_root) if n.isdigit()]
    year_dirs.sort()

    # Prefill counts from existing cached per-user files (may be missing for some years)
    global_by_year: Dict[int, int] = {}
    matrikel_by_year: Dict[int, int] = {}
    for year in year_dirs:
        user_dir = os.path.join(data_root, str(year), "obser", obserkode)
        glist = _load_json(os.path.join(user_dir, "global.json"))
        if glist is not None:
            global_by_year[year] = len(glist)
        mlist = _load_json(os.path.join(user_dir, "matrikelarter.json"))
        if mlist is not None:
            matrikel_by_year[year] = len(mlist)

    # Observationer pr. aar (fra DB)
    obs_by_year: Dict[int, int] = {}
    obs_rows: List[Observation] = []
    async with SessionLocal() as dbsession:
        obs_rows = (await dbsession.execute(
            select(Observation).where(Observation.obserkode == obserkode)
        )).scalars().all()
        for row in obs_rows:
            d = getattr(row, "dato", None)
            if not d:
                continue
            obs_by_year[d.year] = obs_by_year.get(d.year, 0) + 1

    # Build consolidated years + matrikel_years including years with DB obs but missing cached lists
    all_years = sorted(set(year_dirs) | set(obs_by_year.keys()) | set(matrikel_by_year.keys()))

    years = []
    matrikel_years = []
    for year in all_years:
        gcount = global_by_year.get(year, 0)
        mcount = matrikel_by_year.get(year, 0)

        rank = None
        sb_path = os.path.join(data_root, str(year), "scoreboards", "global_alle", "scoreboard.json")
        sb_rows = _load_json(sb_path) or []
        for row in sb_rows:
            if row.get("obserkode") == obserkode:
                rank = row.get("placering")
                break

        if gcount > 0 or obs_by_year.get(year, 0) > 0:
            years.append({"year": year, "count": gcount, "rank": rank})

        if mcount > 0:
            rank_matrikel = None
            sb_m_path = os.path.join(data_root, str(year), "scoreboards", "global_matrikel", "scoreboard.json")
            sb_m_rows = _load_json(sb_m_path) or []
            for row in sb_m_rows:
                if row.get("obserkode") == obserkode:
                    rank_matrikel = row.get("placering")
                    break
            matrikel_years.append({"year": year, "count": mcount, "rank": rank_matrikel})

    raw_filter = await get_global_filter()
    excluded_keys = _get_excluded_species_keys()

    # Fallback: hvis matrikel-listen ikke er bygget (fx manglende perioder),
    # udled den direkte fra observationer med matrikel-1 tag.
    if not matrikel_list:
        tagged_global_m1 = [
            row for row in (obs_rows or [])
            if _observation_has_matrikel_tag(row, raw_filter, 1)
        ]
        fallback_matrikel_list = _firsts_from_obs(tagged_global_m1, excluded_keys=excluded_keys)
        if fallback_matrikel_list:
            matrikel_list = _sort_list_by_date(fallback_matrikel_list)

    matrikel_indexes = _collect_matrikel_indexes_from_observations(obs_rows, raw_filter)
    if not matrikel_indexes:
        matrikel_indexes = [1]

    obs_by_year_rows: Dict[int, List[Observation]] = {}
    for row in obs_rows:
        d = getattr(row, "dato", None)
        if not d:
            continue
        obs_by_year_rows.setdefault(d.year, []).append(row)

    def _count_matrikel_species(rows: List[Observation], matrikel_index: int) -> int:
        tagged = [
            row for row in (rows or [])
            if _observation_has_matrikel_tag(row, raw_filter, matrikel_index)
        ]
        return len(_firsts_from_obs(tagged, excluded_keys=excluded_keys))

    matrikel_totals_by_index: Dict[int, Dict[str, Optional[int]]] = {}
    for idx in matrikel_indexes:
        count_total = _count_matrikel_species(obs_rows, idx)
        rank_total = total_rank_matrikel if idx == 1 else None
        matrikel_totals_by_index[idx] = {
            "count": count_total,
            "rank": rank_total,
        }

    matrikel_year_rows: List[Dict[str, Any]] = []
    for year in year_dirs:
        rows_for_year = obs_by_year_rows.get(year, [])
        per_index: Dict[str, Dict[str, Optional[int]]] = {}
        year_has_data = False

        rank_matrikel_1 = None
        sb_m_path = os.path.join(data_root, str(year), "scoreboards", "global_matrikel", "scoreboard.json")
        sb_m_rows = _load_json(sb_m_path) or []
        for sb_row in sb_m_rows:
            if sb_row.get("obserkode") == obserkode:
                rank_matrikel_1 = sb_row.get("placering")
                break

        for idx in matrikel_indexes:
            count_year = _count_matrikel_species(rows_for_year, idx)
            if count_year > 0:
                year_has_data = True
            per_index[str(idx)] = {
                "count": count_year,
                "rank": rank_matrikel_1 if idx == 1 and count_year > 0 else None,
            }

        if year_has_data:
            matrikel_year_rows.append({
                "year": year,
                "matrikler": per_index,
            })

    # Fallback for grafer: brug beregnede årstal fra observationer,
    # hvis filbaseret matrikel-udtræk ikke gav nogen værdier.
    if not any(Number > 0 for Number in matrikel_by_year.values()):
        derived_matrikel_by_year: Dict[int, int] = {}
        for row in matrikel_year_rows:
            year_value = int(row.get("year"))
            count_value = int((row.get("matrikler") or {}).get("1", {}).get("count") or 0)
            if count_value > 0:
                derived_matrikel_by_year[year_value] = count_value

        if derived_matrikel_by_year:
            matrikel_by_year = derived_matrikel_by_year
            matrikel_years = []
            for row in matrikel_year_rows:
                year_value = int(row.get("year"))
                cell = (row.get("matrikler") or {}).get("1", {})
                count_value = int(cell.get("count") or 0)
                if count_value <= 0:
                    continue
                matrikel_years.append({
                    "year": year_value,
                    "count": count_value,
                    "rank": cell.get("rank")
                })

    all_years = sorted(set(year_dirs) | set(obs_by_year.keys()) | set(matrikel_by_year.keys()))

    chart_global = [
        {"year": y, "count": global_by_year.get(y, 0)}
        for y in all_years
        if global_by_year.get(y, 0) > 0
    ]
    chart_matrikel = [
        {"year": y, "count": matrikel_by_year.get(y, 0)}
        for y in all_years
        if matrikel_by_year.get(y, 0) > 0
    ]
    chart_obs = [
        {"year": y, "count": obs_by_year.get(y, 0)}
        for y in all_years
        if obs_by_year.get(y, 0) > 0
    ]

    return {
        "user": user_info,
        "lists": {
            "danmark": {
                "count": len(global_list),
                "rank": total_rank_global,
                "items": global_list
            },
            "vp": {
                "count": len(matrikel_list),
                "rank": total_rank_matrikel,
                "items": matrikel_list
            }
        },
        "years": years,
        "matrikel_years": matrikel_years,
        "matrikel_available_indexes": matrikel_indexes,
        "matrikel_totals": {str(k): v for k, v in matrikel_totals_by_index.items()},
        "matrikel_year_rows": matrikel_year_rows,
        "charts": {
            "global_by_year": chart_global,
            "matrikel_by_year": chart_matrikel,
            "obs_by_year": chart_obs
        }
    }


@app.get("/api/observationer_table")
async def observationer_table(request: Request):
    session = request.session
    obserkode = session.get("obserkode")
    if not obserkode:
        raise HTTPException(status_code=401, detail="Ikke logget ind")

    url = (
        "https://statistik.dofbasen.dk/arter"
        f"?aar=&slutAar=&startAar=&afdeling=&kommune=&lokalitet=&obser={obserkode}"
        "&visArter=ja&_visArter=on&_visHybrider=on&_visUbestemte=on&_visAndre=on"
    )

    try:
        resp = requests.get(url, timeout=20)
    except Exception:
        return JSONResponse({"rows": [], "error": "Kunne ikke hente data"}, status_code=502)

    if resp.status_code != 200:
        return JSONResponse({"rows": [], "error": "Ugyldigt svar"}, status_code=502)

    try:
        tables = pd.read_html(resp.text)
    except Exception:
        tables = []

    if not tables:
        return {"rows": []}

    df = tables[0]

    def _find_col(candidates: List[str]):
        for cand in candidates:
            for col in df.columns:
                if str(col).strip().lower() == cand:
                    return col
        return None

    col_artnr = _find_col(["artnr.", "artnr", "artnr "])
    col_navn = _find_col(["navn"])
    col_latin = _find_col(["latin"])
    col_obs = _find_col(["observationer"])
    col_ind = _find_col(["individer"])

    def _format_artnr(value: Any) -> str:
        s = str(value).strip()
        if s.isdigit():
            return s.zfill(5)
        return s

    rows = []
    current_year = datetime.datetime.now().year
    for _, row in df.iterrows():
        artnr = _format_artnr(row[col_artnr]) if col_artnr else ""
        navn = str(row[col_navn]) if col_navn else ""
        latin = str(row[col_latin]) if col_latin else ""
        obs_val = row[col_obs] if col_obs else ""
        ind_val = row[col_ind] if col_ind else ""
        link = ""
        if artnr:
            link = (
                "https://dofbasen.dk/search/result.php?design=table&soeg=soeg"
                "&obstype=observationer&species=alle&subspecies=yes&sortering=art&artdata=art"
                f"&hiddenart={artnr}&periode=mellemdato&dato_first=01-01-1800"
                f"&dato_second=31-12-{current_year}&obserdata={obserkode}"
            )

        rows.append({
            "artnr": artnr,
            "navn": navn,
            "latin": latin,
            "observationer": obs_val,
            "individer": ind_val,
            "link": link
        })

    return {"rows": rows}

# ---------------------------------------------------------
#  API: Sync
# ---------------------------------------------------------
@app.post("/api/sync_obserkode")
async def sync_obserkode_api(request: Request, kode: Optional[str] = None, aar: Optional[int] = None, background_tasks: BackgroundTasks = None):
    web_session = request.session
    if not web_session.get("obserkode") and not web_session.get("is_admin"):
        raise HTTPException(status_code=401, detail="Ikke logget ind")

    if web_session.get("is_admin"):
        if not kode:
            raise HTTPException(status_code=400, detail="Admin skal angive obserkode")
        try:
            resolved_kode = normalize_obserkode(kode)
        except ValueError:
            raise HTTPException(status_code=400, detail="Ugyldig obserkode")
    else:
        resolved_kode = web_session.get("obserkode")
        if not resolved_kode:
            raise HTTPException(status_code=401, detail="Ikke logget ind")
        if kode:
            try:
                provided_kode = normalize_obserkode(kode)
            except ValueError:
                raise HTTPException(status_code=400, detail="Ugyldig obserkode")
            if provided_kode != resolved_kode:
                raise HTTPException(status_code=403, detail="Ingen adgang til denne obserkode")

    enforce_sync_rate_limit(request, 30)

    # sikr at bruger findes (navn bruges i scoreboard)
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.obserkode == resolved_kode))).scalar()
        if not user:
            session.add(User(obserkode=resolved_kode, navn=resolved_kode, lokalafdeling=None))
            await session.commit()
        ok = (await session.execute(select(Obserkode).where(Obserkode.kode == resolved_kode))).scalar()
        if not ok:
            session.add(Obserkode(kode=resolved_kode))
            await session.commit()
    # kør sync nu
    await fetch_and_store(resolved_kode, aar, include_global_rebuild=False)
    asyncio.create_task(_background_global_rebuild(resolved_kode))
    return {"msg": f"Sync kørt for {resolved_kode}", "aar": aar or (await get_global_year())}

@app.post("/api/sync_all")
async def sync_all_api(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan køre sync_all")
    enforce_sync_rate_limit(request, 30)
    await daily_update_all_jsons()
    return {"ok": True, "msg": "Synkronisering for alle brugere er gennemført"}

@app.post("/api/admin/sync_all_current_year")
async def admin_sync_all_current_year(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan køre sync for alle")
    enforce_sync_rate_limit(request, 30)
    aar = await get_global_year()
    async with SessionLocal() as session:
        koder = [
            normalize_obserkode(k.kode)
            for k in (await session.execute(select(Obserkode))).scalars().all()
            if SAFE_OBSERKODE_RE.fullmatch((k.kode or "").strip().upper())
        ]

    async def _run_sync_all_year():
        try:
            for kode in koder:
                await fetch_and_store(kode, aar, include_global_rebuild=False)
            # Én samlet global rebuild til sidst
            for kode in koder:
                await generate_user_global_lists(kode)
            await generate_global_scoreboards_all_time()
            print(f"[SYNC-ALL-YEAR] Færdig for {len(koder)} brugere ({aar})")
        except Exception as e:
            print(f"[SYNC-ALL-YEAR] Fejl: {e}")

    asyncio.create_task(_run_sync_all_year())
    return {
        "ok": True,
        "year": aar,
        "users": len(koder),
        "msg": f"Synkronisering startet for {len(koder)} brugere ({aar})"
    }

@app.post("/api/admin/sync_all_previous_years")
async def admin_sync_all_previous_years(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan køre sync for alle")
    enforce_sync_rate_limit(request, 30)
    asyncio.create_task(daily_update_all_jsons())
    return {
        "ok": True,
        "msg": "Fuld synkronisering for alle brugere er startet i baggrunden."
    }

@app.post("/api/admin/rebuild_scoreboards_from_db")
async def admin_rebuild_scoreboards_from_db(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan genopbygge scoreboards")
    enforce_sync_rate_limit(request, 15)

    async with SessionLocal() as session:
        users = [
            u for u in (await session.execute(select(User))).scalars().all()
            if SAFE_OBSERKODE_RE.fullmatch((u.obserkode or "").strip().upper())
        ]

        year_rows = (await session.execute(
            select(func.extract("year", Observation.dato)).where(Observation.dato.is_not(None)).distinct()
        )).all()

    years: List[int] = []
    for (year_value,) in year_rows:
        try:
            parsed = int(year_value)
        except Exception:
            continue
        if parsed > 0:
            years.append(parsed)
    years = sorted(set(years))

    rebuilt_user_count = 0
    for user in users:
        for year in years:
            await generate_user_lists(user.obserkode, year)
        await generate_user_global_lists(user.obserkode)
        rebuilt_user_count += 1

    for year in years:
        await generate_scoreboards_from_lists(year)
    await generate_global_scoreboards_all_time()

    return {
        "ok": True,
        "years": years,
        "users": rebuilt_user_count,
        "msg": f"Scoreboards genopbygget fra DB for {len(years)} år og {rebuilt_user_count} brugere"
    }

@app.post("/api/sync_mine_observationer")
async def sync_mine_observationer(request: Request, aar: Optional[int] = None):
    """
    Synkroniserer observationer for den aktuelle bruger (kræver login).
    """
    session = request.session
    obserkode = session.get("obserkode")
    if not obserkode:
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    enforce_sync_rate_limit(request, 30)
    if aar is None:
        aar = await get_global_year()
    await fetch_and_store(obserkode, aar, include_global_rebuild=False)
    asyncio.create_task(_background_global_rebuild(obserkode))
    return {
        "ok": True,
        "state": "done",
        "aar": aar,
        "msg": f"Synkronisering og scoreboards gennemført for {obserkode} ({aar})"
    }

@app.post("/api/full_sync_me")
async def full_sync_me(request: Request):
    session = request.session
    obserkode = session.get("obserkode")
    if not obserkode:
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    enforce_sync_rate_limit(request, 30)

    asyncio.create_task(sync_user_all_time(obserkode))
    return {"ok": True, "msg": f"Fuld synkronisering startet for {obserkode}"}

# ---------------------------------------------------------
#  API: Firsts & Scoreboards (filer)
# ---------------------------------------------------------
@app.post("/api/firsts")
async def api_firsts(payload: Dict[str, Any] = Body(...), request: Request = None):
    scope      = payload.get("scope", "global")  # "global" | "matrikel" | "lokalafdeling"
    afdeling   = payload.get("afdeling")
    session = request.session if request else None
    obserkode  = payload.get("obserkode") or (session.get("obserkode") if session else None)
    aar        = payload.get("aar") or await get_global_year()
    if not obserkode:
        raise HTTPException(status_code=401, detail="Ingen obserkode angivet")

    _, _, OBSER_DIR = get_data_dirs(aar)
    user_dir = os.path.join(OBSER_DIR, obserkode)

    def filter_nonempty(lst):
        # Fjern brugere uden observationer (tom liste eller kun tomme felter)
        return [row for row in lst if row.get("artnavn") or row.get("dato")]

    if scope == "global":
        path = os.path.join(user_dir, "global.json")
        if not os.path.exists(path):
            await fetch_and_store(obserkode, aar)
        data = _load_json(path) or []
        return filter_nonempty(data)

    if scope == "matrikel":
        path = os.path.join(user_dir, "matrikelarter.json")
        if not os.path.exists(path):
            await fetch_and_store(obserkode, aar)
        data = _load_json(path) or []
        return filter_nonempty(data)

    if scope == "lokalafdeling":
        if not afdeling:
            raise HTTPException(status_code=400, detail="Ingen afdeling angivet")
        la = _load_json(os.path.join(user_dir, "lokalafdeling.json")) or {}
        afd_data = la.get(afdeling) or {"alle": [], "matrikel": []}
        afd_data["alle"] = filter_nonempty(afd_data.get("alle", []))
        afd_data["matrikel"] = filter_nonempty(afd_data.get("matrikel", []))
        return afd_data

    raise HTTPException(status_code=400, detail="Ukendt scope")

@app.post("/api/scoreboard")
async def api_scoreboard(request: Request):
    params = await request.json()
    scope = params.get("scope")
    aar = params.get("aar") or await get_global_year()
    if str(aar) == "global":
        SCOREBOARD_DIR = os.path.join(SERVER_DIR, "data", "global", "scoreboards")
    else:
        _, SCOREBOARD_DIR, _ = get_data_dirs(aar)

    def rows_for_scope(rows, current_scope: str):
        # Behold 0-arter for tilmeldingsstyrede lister (lokalafdeling/kommune),
        # men filtrér fortsat nationale lister for 0-arter.
        if current_scope in ("global_alle", "global_matrikel"):
            return [r for r in rows if r.get("antal_arter", 0) > 0]
        return rows

    # Lokalafdeling
    if scope in ("lokal_alle", "lokal_matrikel"):
        afdeling = params.get("afdeling")
        if not afdeling:
            return JSONResponse({"error": "Afdeling mangler"}, status_code=400)
        subdir = "lokalafdeling_alle" if scope == "lokal_alle" else "lokalafdeling_matrikel"
        filename = f"{afdeling.replace(' ', '_')}.json"
        path = os.path.join(SCOREBOARD_DIR, subdir, filename)
        if not os.path.exists(path):
            return JSONResponse({"rows": []})
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        return {"rows": rows_for_scope(rows, scope)}

    # Global
    if scope in ("global_alle", "global_matrikel"):
        subdir = "global_alle" if scope == "global_alle" else "global_matrikel"
        path = os.path.join(SCOREBOARD_DIR, subdir, "scoreboard.json")
        if not os.path.exists(path):
            return JSONResponse({"rows": []})
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        return {"rows": rows_for_scope(rows, scope)}

    # Kommune
    if scope in ("kommune_alle", "kommune_matrikel"):
        kommune_id = params.get("kommune")
        if not kommune_id:
            return JSONResponse({"error": "Kommune mangler"}, status_code=400)
        kommune_name = _kommune_name_by_id(kommune_id) or str(kommune_id)
        subdir = "kommune_alle" if scope == "kommune_alle" else "kommune_matrikel"
        filename = f"{_kommune_slug(kommune_name)}.json"
        path = os.path.join(SCOREBOARD_DIR, subdir, filename)
        if not os.path.exists(path):
            return JSONResponse({"rows": []})
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        return {"rows": rows_for_scope(rows, scope)}

    return JSONResponse({"error": "Ukendt scope"}, status_code=400)

@app.post("/api/obser")
async def api_obser(request: Request):
    params = await request.json()
    scope = params.get("scope")
    aar = params.get("aar") or await get_global_year()
    obserkode = params.get("obserkode")
    if not obserkode:
        return JSONResponse({"error": "Obserkode mangler"}, status_code=400)

    async def _ensure_firsts_obsid(rows: Any) -> List[Dict[str, Any]]:
        if not isinstance(rows, list) or not rows:
            return []

        normalized_rows = [row for row in rows if isinstance(row, dict)]
        missing = [row for row in normalized_rows if not str(row.get("obsid") or "").strip()]
        if not missing:
            return normalized_rows

        dates: set = set()
        for row in missing:
            parsed = _parse_ddmmyyyy(row.get("dato"))
            if parsed != datetime.datetime.min:
                dates.add(parsed.date())
        if not dates:
            return normalized_rows

        async with SessionLocal() as session:
            candidates = (await session.execute(
                select(Observation).where(
                    Observation.obserkode == obserkode,
                    Observation.dato.in_(sorted(dates)),
                )
            )).scalars().all()

        obsid_by_exact: Dict[Tuple[str, str, str], str] = {}
        obsid_by_art: Dict[Tuple[str, str], str] = {}
        for obs in candidates:
            if not obs.obsid or not obs.dato:
                continue
            dato_key = obs.dato.strftime("%d-%m-%Y")
            art_key = _normalize_base_art_name(obs.artnavn or "").casefold()
            lok_key = safe_output(obs.loknavn or "").strip().casefold()
            obsid = safe_output(obs.obsid)
            if not obsid:
                continue
            obsid_by_exact.setdefault((dato_key, art_key, lok_key), obsid)
            obsid_by_art.setdefault((dato_key, art_key), obsid)

        enriched: List[Dict[str, Any]] = []
        for row in normalized_rows:
            existing = str(row.get("obsid") or "").strip()
            if existing:
                enriched.append(row)
                continue

            dato_key = str(row.get("dato") or "")
            art_key = _normalize_base_art_name(row.get("artnavn") or "").casefold()
            lok_key = safe_output(row.get("lokalitet") or "").strip().casefold()
            obsid = obsid_by_exact.get((dato_key, art_key, lok_key)) or obsid_by_art.get((dato_key, art_key))
            if obsid:
                copy_row = dict(row)
                copy_row["obsid"] = obsid
                enriched.append(copy_row)
            else:
                enriched.append(row)

        return enriched

    if str(aar) == "global":
        userdir = os.path.join(SERVER_DIR, "data", "global", "obser", obserkode)
    else:
        userdir = os.path.join(SERVER_DIR, "data", str(aar), "obser", obserkode)

    if scope == "user_global":
        path = os.path.join(userdir, "global.json")
        key = "firsts"
    elif scope == "user_matrikel":
        matrikel_index = _matrikel_index_from_key(params.get("matrikel") or params.get("matrikel_index") or 1) or 1
        period_name = params.get("period")
        payload = await _user_matrikel_view_payload(obserkode, aar, matrikel_index=matrikel_index, selected_period_name=period_name)
        return payload
    elif scope == "user_matrikel2":
        period_name = params.get("period")
        payload = await _user_matrikel_view_payload(obserkode, aar, matrikel_index=2, selected_period_name=period_name)
        return payload
    elif scope == "user_lokalafdeling":
        afdeling = params.get("afdeling")
        path = os.path.join(userdir, "lokalafdeling.json")
        key = "afdeling"
    elif scope in ("user_kommune_alle", "user_kommune_matrikel"):
        kommune_id = params.get("kommune")
        if kommune_id:
            return await _user_kommune_view_payload(
                obserkode=obserkode,
                aar_value=aar,
                kommune_id_value=kommune_id,
                matrikel_only=(scope == "user_kommune_matrikel"),
            )
        path = os.path.join(userdir, "kommune.json")
        key = "kommune"
    else:
        return JSONResponse({"error": "Ukendt scope"}, status_code=400)

    if not os.path.exists(path):
        if str(aar) == "global":
            await generate_user_global_lists(obserkode)
        elif scope in ("user_kommune_alle", "user_kommune_matrikel"):
            await fetch_and_store(obserkode, aar)
        if not os.path.exists(path):
            return JSONResponse({key: []})
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if scope == "user_lokalafdeling":
        afdeling = params.get("afdeling")
        firsts = await _ensure_firsts_obsid(data.get(afdeling, {}).get("alle", []))
        return {"firsts": firsts}
    if scope in ("user_kommune_alle", "user_kommune_matrikel"):
        if not data:
            return {"firsts": []}
        if scope == "user_kommune_matrikel":
            firsts = await _ensure_firsts_obsid(data.get("matrikel", []))
            return {"firsts": firsts}
        firsts = await _ensure_firsts_obsid(data.get("alle", []))
        return {"firsts": firsts}
    if key == "firsts":
        enriched_firsts = await _ensure_firsts_obsid(data)
        available_years = await get_available_years_for_user(obserkode) if scope == "user_global" else []
        return {key: enriched_firsts, "available_years": available_years}
    return {key: data}

@app.get("/api/user_scoreboard")
async def user_scoreboard(request: Request, aar: int = Query(None)):
    """
    Returnerer brugerens placering, antal arter, seneste art og dato for:
    - Lokalafdeling (hvis sat): alle + matrikel
    - Nationalt: alle + matrikel
    - Grupper: alle + matrikel (samme format)
    Med debug-logging.
    """
    session = request.session
    obserkode = session.get("obserkode")
    print("[DEBUG] Session obserkode:", obserkode)
    if not obserkode:
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    if aar is None:
        aar = await get_global_year()
    print("[DEBUG] År:", aar)
    _, SCOREBOARD_DIR, OBSER_DIR = get_data_dirs(aar)
    GLOBAL_SCOREBOARD_DIR = os.path.join(SERVER_DIR, "data", "global", "scoreboards")
    print("[DEBUG] SCOREBOARD_DIR:", SCOREBOARD_DIR)

    def get_row(rows):
        for i, r in enumerate(rows, 1):
            print("[DEBUG] Tjekker row:", r)
            if r.get("obserkode") == obserkode:
                print("[DEBUG] Match fundet:", r)
                return {
                    "placering": r.get("placering", i),
                    "antal_arter": r.get("antal_arter", 0),
                    "sidste_art": r.get("sidste_art", ""),
                    "sidste_dato": r.get("sidste_dato", "")
                }
        print("[DEBUG] Ingen match for obserkode:", obserkode)
        return None

    def list_summary(rows: Any) -> Dict[str, Any]:
        if not isinstance(rows, list) or not rows:
            return {"antal_arter": 0, "sidste_art": "", "sidste_dato": ""}
        clean_rows = [row for row in rows if isinstance(row, dict) and row.get("artnavn")]
        if not clean_rows:
            return {"antal_arter": 0, "sidste_art": "", "sidste_dato": ""}
        latest = max(clean_rows, key=lambda row: _parse_ddmmyyyy(row.get("dato")))
        return {
            "antal_arter": len(clean_rows),
            "sidste_art": latest.get("artnavn", ""),
            "sidste_dato": latest.get("dato", ""),
        }

    result = {}
    result["self_obserkode"] = obserkode
    result["self_navn"] = request.session.get("navn") or obserkode

    # Nationalt - alle
    try:
        path = os.path.join(SCOREBOARD_DIR, "global_alle", "scoreboard.json")
        print("[DEBUG] Læser national_alle fra:", path)
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        result["national_alle"] = get_row(rows)
    except Exception as e:
        print("[DEBUG] national_alle fejl:", e)
        result["national_alle"] = None

    # Nationalt - matrikel
    try:
        path = os.path.join(SCOREBOARD_DIR, "global_matrikel", "scoreboard.json")
        print("[DEBUG] Læser national_matrikel fra:", path)
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        result["national_matrikel"] = get_row(rows)
    except Exception as e:
        print("[DEBUG] national_matrikel fejl:", e)
        result["national_matrikel"] = None

    # Lokalafdeling (hent fra session eller database)
    lokalafdeling = session.get("lokalafdeling")
    print("[DEBUG] Session lokalafdeling:", lokalafdeling)
    if not lokalafdeling:
        async with SessionLocal() as dbsession:
            user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
            if user and user.lokalafdeling:
                lokalafdeling = user.lokalafdeling
                session["lokalafdeling"] = lokalafdeling
                print("[DEBUG] Lokalafdeling hentet fra database:", lokalafdeling)
    if lokalafdeling:
        result["lokalafdeling_navn"] = lokalafdeling
        try:
            filename = f"{lokalafdeling.replace(' ', '_')}.json"
            path = os.path.join(SCOREBOARD_DIR, "lokalafdeling_alle", filename)
            print("[DEBUG] Læser lokalafdeling_alle fra:", path)
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            result["lokalafdeling_alle"] = get_row(rows)
        except Exception as e:
            print("[DEBUG] lokalafdeling_alle fejl:", e)
            result["lokalafdeling_alle"] = None
        try:
            filename = f"{lokalafdeling.replace(' ', '_')}.json"
            path = os.path.join(SCOREBOARD_DIR, "lokalafdeling_matrikel", filename)
            print("[DEBUG] Læser lokalafdeling_matrikel fra:", path)
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            result["lokalafdeling_matrikel"] = get_row(rows)
        except Exception as e:
            print("[DEBUG] lokalafdeling_matrikel fejl:", e)
            result["lokalafdeling_matrikel"] = None
    else:
        print("[DEBUG] Ingen lokalafdeling sat i session eller database.")
        result["lokalafdeling_navn"] = None
        result["lokalafdeling_alle"] = None
        result["lokalafdeling_matrikel"] = None

    # Lokalafdeling-overblik for alle valgte lokalafdelinger (primær først)
    try:
        async with SessionLocal() as dbsession:
            user_for_overview = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        opted_lokalafdelinger = _user_opted_lokalafdelinger(user_for_overview)
        if lokalafdeling:
            opted_lokalafdelinger = [lokalafdeling, *[value for value in opted_lokalafdelinger if value != lokalafdeling]]
        opted_lokalafdelinger = opted_lokalafdelinger[:5]

        lokalafdelinger_overblik = []
        for opted_name in opted_lokalafdelinger:
            filename = f"{opted_name.replace(' ', '_')}.json"

            alle_row = None
            alle_total_row = None
            matrikel_row = None
            try:
                with open(os.path.join(SCOREBOARD_DIR, "lokalafdeling_alle", filename), encoding="utf-8") as f:
                    alle_row = get_row(json.load(f))
            except Exception:
                alle_row = None
            try:
                with open(os.path.join(GLOBAL_SCOREBOARD_DIR, "lokalafdeling_alle", filename), encoding="utf-8") as f:
                    alle_total_row = get_row(json.load(f))
            except Exception:
                alle_total_row = None
            try:
                with open(os.path.join(SCOREBOARD_DIR, "lokalafdeling_matrikel", filename), encoding="utf-8") as f:
                    matrikel_row = get_row(json.load(f))
            except Exception:
                matrikel_row = None

            lokalafdelinger_overblik.append({
                "lokalafdeling_navn": str(opted_name),
                "alle": alle_row,
                "alle_total": alle_total_row,
                "matrikel": matrikel_row,
            })
        result["lokalafdelinger_overblik"] = lokalafdelinger_overblik
    except Exception as error:
        print("[DEBUG] lokalafdelinger_overblik fejl:", error)
        result["lokalafdelinger_overblik"] = []

    # Kommune (hent fra session eller database)
    kommune_id = _normalize_single_kommune(session.get("kommune"))
    print("[DEBUG] Session kommune:", kommune_id)
    if not kommune_id:
        async with SessionLocal() as dbsession:
            user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
            if user and getattr(user, "kommune", None):
                kommune_id = _normalize_single_kommune(user.kommune)
                session["kommune"] = kommune_id
                print("[DEBUG] Kommune hentet fra database:", kommune_id)

    if kommune_id:
        kommune_name = _kommune_name_by_id(str(kommune_id)) or "Kommune"
        result["kommune_id"] = str(kommune_id)
        result["kommune_navn"] = kommune_name
        try:
            filename = f"{_kommune_slug(kommune_name)}.json"
            path = os.path.join(SCOREBOARD_DIR, "kommune_alle", filename)
            print("[DEBUG] Læser kommune_alle fra:", path)
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            result["kommune_alle"] = get_row(rows)
        except Exception as e:
            print("[DEBUG] kommune_alle fejl:", e)
            result["kommune_alle"] = None
        try:
            filename = f"{_kommune_slug(kommune_name)}.json"
            path = os.path.join(SCOREBOARD_DIR, "kommune_matrikel", filename)
            print("[DEBUG] Læser kommune_matrikel fra:", path)
            with open(path, encoding="utf-8") as f:
                rows = json.load(f)
            result["kommune_matrikel"] = get_row(rows)
        except Exception as e:
            print("[DEBUG] kommune_matrikel fejl:", e)
            result["kommune_matrikel"] = None
    else:
        print("[DEBUG] Ingen kommune sat i session eller database.")
        result["kommune_alle"] = None
        result["kommune_matrikel"] = None

    # Kommune-overblik for alle tilmeldte kommuner (primær først)
    try:
        async with SessionLocal() as dbsession:
            user_for_overview = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        opted_kommuner = _user_opted_kommuner(user_for_overview)
        if kommune_id:
            opted_kommuner = [kommune_id, *[value for value in opted_kommuner if value != kommune_id]]
        opted_kommuner = opted_kommuner[:5]

        kommuner_overblik = []
        for opted_id in opted_kommuner:
            kommune_name = _kommune_name_by_id(str(opted_id)) or str(opted_id)
            filename = f"{_kommune_slug(kommune_name)}.json"

            alle_row = None
            alle_total_row = None
            matrikel_row = None
            try:
                with open(os.path.join(SCOREBOARD_DIR, "kommune_alle", filename), encoding="utf-8") as f:
                    alle_row = get_row(json.load(f))
            except Exception:
                alle_row = None
            try:
                with open(os.path.join(GLOBAL_SCOREBOARD_DIR, "kommune_alle", filename), encoding="utf-8") as f:
                    alle_total_row = get_row(json.load(f))
            except Exception:
                alle_total_row = None
            try:
                with open(os.path.join(SCOREBOARD_DIR, "kommune_matrikel", filename), encoding="utf-8") as f:
                    matrikel_row = get_row(json.load(f))
            except Exception:
                matrikel_row = None

            kommuner_overblik.append({
                "kommune_id": str(opted_id),
                "kommune_navn": kommune_name,
                "alle": alle_row,
                "alle_total": alle_total_row,
                "matrikel": matrikel_row,
            })
        result["kommuner_overblik"] = kommuner_overblik
    except Exception as error:
        print("[DEBUG] kommuner_overblik fejl:", error)
        result["kommuner_overblik"] = []

    # Grupper (beregn direkte)
    async def beregn_gruppe_scoreboard(gruppe, scope, aar):
        koder = gruppe.get("obserkoder", [])
        rows = []
        async with SessionLocal() as session:
            users = (await session.execute(select(User).where(User.obserkode.in_(koder)))).scalars().all()
        for u in users:
            if scope == "gruppe_alle":
                L = _load_json(os.path.join(OBSER_DIR, u.obserkode, "global.json")) or []
            elif scope == "gruppe_matrikel":
                L = _load_json(os.path.join(OBSER_DIR, u.obserkode, "matrikelarter.json")) or []
            else:
                continue
            a, art, dato = 0, "", ""
            if L:
                unique_arter = { (r.get("artnavn") or "").split("(")[0].split(",")[0].strip()
                                 for r in L if r.get("artnavn") and "sp." not in r.get("artnavn") and "/" not in r.get("artnavn") and " x " not in r.get("artnavn") }
                a = len(unique_arter)
                latest = max(L, key=lambda r: _parse_ddmmyyyy(r.get("dato")), default={})
                art = latest.get("artnavn", "")
                dato = latest.get("dato", "")
            rows.append({
                "navn": u.navn or u.obserkode,
                "obserkode": u.obserkode,
                "antal_arter": a,
                "sidste_art": art,
                "sidste_dato": dato,
            })
        rows.sort(key=lambda x: x["antal_arter"], reverse=True)
        for i, r in enumerate(rows, 1):
            r["placering"] = i
        return rows

    grupper = load_grupper()
    mine_grupper = [g for g in grupper if obserkode in g.get("obserkoder", [])]
    print("[DEBUG] Mine grupper:", [g["navn"] for g in mine_grupper])
    result["grupper"] = []
    for g in mine_grupper:
        gruppeinfo = {"navn": g["navn"]}
        # alle
        try:
            rows_alle = await beregn_gruppe_scoreboard(g, "gruppe_alle", aar)
            gruppeinfo["alle"] = get_row(rows_alle)
        except Exception as e:
            print("[DEBUG] gruppe_alle fejl:", e)
            gruppeinfo["alle"] = None
        # matrikel
        try:
            rows_matrikel = await beregn_gruppe_scoreboard(g, "gruppe_matrikel", aar)
            gruppeinfo["matrikel"] = get_row(rows_matrikel)
        except Exception as e:
            print("[DEBUG] gruppe_matrikel fejl:", e)
            gruppeinfo["matrikel"] = None
        result["grupper"].append(gruppeinfo)

    # Matrikel 2 (privat, ingen scoreboard)
    try:
        user_dir = get_user_dir(aar, obserkode)
        matrikel2_rows = _load_json(os.path.join(user_dir, "matrikel2arter.json")) or []
        periods_payload = []
        async with SessionLocal() as dbsession:
            user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
            periods_payload = (_load_user_matrikel_periods(user) or {}).get("matrikel2") or []
        latest_period_name = ""
        if periods_payload:
            latest_period_name = periods_payload[-1].get("name") or ""
        result["matrikel2"] = {
            **list_summary(matrikel2_rows),
            "navn": latest_period_name,
        }
    except Exception as error:
        print("[DEBUG] matrikel2 fejl:", error)
        result["matrikel2"] = {
            "antal_arter": 0,
            "sidste_art": "",
            "sidste_dato": "",
            "navn": "",
        }

    print("[DEBUG] Endeligt resultat:", result)
    return result

# ---------------------------------------------------------
#  API: Bruger-login (DOFbasen) + afdeling
# ---------------------------------------------------------
login_attempts = defaultdict(list)

@app.get("/api/is_logged_in")
async def is_logged_in(request: Request):
    session = request.session
    return {"ok": bool(session.get("obserkode")), "lokalafdeling": session.get("lokalafdeling")}

# filepath: [server.py](http://_vscodecontentref_/1)
@app.post("/api/validate-login")
async def validate_login(data: Dict[str, Any] = Body(...), request: Request = None):
    try:
        obserkode = normalize_obserkode(data.get("obserkode"))
    except ValueError:
        return {"ok": False, "error": "Ugyldig obserkode"}
    adgangskode = data.get("adgangskode")

    # rate limit: max 5 / 10 min pr kode
    now = time.time()
    attempts = [t for t in login_attempts[obserkode] if now - t < 600]
    if len(attempts) >= 5:
        return {"ok": False, "error": "For mange loginforsøg. Prøv igen om 10 minutter."}
    attempts.append(now)
    login_attempts[obserkode] = attempts

    # DOFbasen login
    url = "https://krydslister.dofbasen.dk/api/v1/login"
    try:
        r = requests.post(url, json={"username": obserkode, "password": adgangskode}, timeout=10)
        if r.status_code != 200:
            return {"ok": False, "error": "Login fejlede"}
        token = r.json().get("token")
        if not token:
            return {"ok": False, "error": "Token mangler"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Hent navn
    navn = fetch_observer_name_from_dof(obserkode)

    # Gem/Opdater bruger
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.obserkode == obserkode))).scalar()
        new_user_created = False
        if user:
            if navn and user.navn != navn:
                user.navn = navn
        else:
            session.add(User(obserkode=obserkode, navn=navn or obserkode))
            new_user_created = True
        # Sørg for at der også findes en Obserkode-række
        ok = (await session.execute(select(Obserkode).where(Obserkode.kode == obserkode))).scalar()
        if not ok:
            session.add(Obserkode(kode=obserkode))
        await session.commit()

    if new_user_created:
        asyncio.create_task(sync_user_all_time(obserkode))

    if request:
        session = request.session
        session["obserkode"]     = obserkode
        session["navn"]          = navn
        session["lokalafdeling"] = None
        csrf_token = generate_csrf_token(session)
    else:
        csrf_token = None

    return {"ok": True, "token": token, "navn": navn, "csrf_token": csrf_token}

@app.post("/api/set_afdeling")
async def set_afdeling_kommune(data: Dict[str, Any] = Body(...), request: Request = None):
    lokalafdeling = (data.get("lokalafdeling") or None)
    kommune_raw = data.get("kommune")
    kommune = _normalize_single_kommune(kommune_raw)
    has_lokalafdelinger = "lokalafdelinger" in data
    has_kommuner = "kommuner" in data
    lokalafdelinger = _normalize_lokalafdelinger(data.get("lokalafdelinger") or []) if has_lokalafdelinger else None
    kommuner = _normalize_kommuner(data.get("kommuner") or []) if has_kommuner else None
    has_dynamic = "matrikel_perioder" in data
    dynamic_periods = _normalize_matrikel_period_map(data.get("matrikel_perioder") or {}) if has_dynamic else None
    has_m1 = "matrikel1_perioder" in data
    has_m2 = "matrikel2_perioder" in data
    matrikel1_perioder = _normalize_matrikel_periods(data.get("matrikel1_perioder") or []) if has_m1 else None
    matrikel2_perioder = _normalize_matrikel_periods(data.get("matrikel2_perioder") or []) if has_m2 else None
    if not request:
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    web_session = request.session
    if not web_session.get("obserkode"):
        raise HTTPException(status_code=401, detail="Ikke logget ind")
    obserkode = web_session.get("obserkode")
    async with SessionLocal() as dbsession:
        user = (await dbsession.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Bruger ikke fundet")

        prev_lokalafdeling = str(getattr(user, "lokalafdeling", "") or "").strip() or None
        prev_kommune = _normalize_single_kommune(getattr(user, "kommune", None))
        prev_lokalafdelinger = _user_opted_lokalafdelinger(user)
        prev_kommuner = _user_opted_kommuner(user)

        user.lokalafdeling = (lokalafdeling or None)
        user.kommune = (kommune or None)

        if has_lokalafdelinger:
            user.lokalafdelinger_json = json.dumps(lokalafdelinger or [], ensure_ascii=False)
        elif not _user_opted_lokalafdelinger(user) and lokalafdeling:
            user.lokalafdelinger_json = json.dumps(_normalize_lokalafdelinger([lokalafdeling]), ensure_ascii=False)

        if has_kommuner:
            normalized_kommuner = list(kommuner or [])
            if kommune and kommune not in normalized_kommuner:
                normalized_kommuner = [kommune, *normalized_kommuner]
            normalized_kommuner = normalized_kommuner[:5]
            user.kommuner_json = json.dumps(normalized_kommuner, ensure_ascii=False)
        elif not _user_opted_kommuner(user) and kommune:
            user.kommuner_json = json.dumps(_normalize_kommuner([kommune]), ensure_ascii=False)

        current_map = _load_user_matrikel_periods(user)
        if has_dynamic:
            for key, periods in (dynamic_periods or {}).items():
                current_map[key] = periods
        if has_m1:
            user.matrikel1_perioder = json.dumps(matrikel1_perioder or [], ensure_ascii=False)
            current_map[_matrikel_key(1)] = matrikel1_perioder or []
        if has_m2:
            user.matrikel2_perioder = json.dumps(matrikel2_perioder or [], ensure_ascii=False)
            current_map[_matrikel_key(2)] = matrikel2_perioder or []

        if has_dynamic or has_m1 or has_m2:
            user.matrikel_perioder_json = json.dumps(current_map, ensure_ascii=False)

        new_lokalafdeling = str(getattr(user, "lokalafdeling", "") or "").strip() or None
        new_kommune = _normalize_single_kommune(getattr(user, "kommune", None))
        new_lokalafdelinger = _user_opted_lokalafdelinger(user)
        new_kommuner = _user_opted_kommuner(user)
        needs_scoreboard_refresh = (
            prev_lokalafdeling != new_lokalafdeling
            or prev_kommune != new_kommune
            or prev_lokalafdelinger != new_lokalafdelinger
            or prev_kommuner != new_kommuner
        )

        await dbsession.commit()
    web_session["lokalafdeling"] = lokalafdeling
    web_session["kommune"] = kommune

    if needs_scoreboard_refresh:
        asyncio.create_task(_rebuild_scoreboards_after_optin_change(obserkode))

    return {"ok": True}

@app.post("/api/delete_my_account")
async def delete_my_account(request: Request):
    web_session = request.session
    obserkode = web_session.get("obserkode")
    if not obserkode:
        raise HTTPException(status_code=401, detail="Ikke logget ind")

    safe_kode = normalize_obserkode(obserkode)

    async with SessionLocal() as dbsession:
        user = (await dbsession.execute(select(User).where(User.obserkode == safe_kode))).scalar_one_or_none()
        ok = (await dbsession.execute(select(Obserkode).where(Obserkode.kode == safe_kode))).scalar_one_or_none()
        if not user and not ok:
            raise HTTPException(status_code=404, detail="Bruger ikke fundet")

        await dbsession.execute(Observation.__table__.delete().where(Observation.obserkode == safe_kode))
        await dbsession.execute(User.__table__.delete().where(User.obserkode == safe_kode))
        await dbsession.execute(Obserkode.__table__.delete().where(Obserkode.kode == safe_kode))
        await dbsession.commit()

    grupper = load_grupper()
    changed = False
    for gruppe in grupper:
        medlemmer = gruppe.get("obserkoder", [])
        nye_medlemmer = []
        for kode in medlemmer:
            try:
                normalized_member = normalize_obserkode(kode)
            except Exception:
                normalized_member = str(kode or "").strip().upper()
            if normalized_member != safe_kode:
                nye_medlemmer.append(kode)
        if len(nye_medlemmer) != len(medlemmer):
            gruppe["obserkoder"] = nye_medlemmer
            changed = True
    if changed:
        save_grupper(grupper)

    remove_all_user_data_dirs(safe_kode)
    web_session.clear()
    return {"ok": True, "msg": "Konto og alle brugerdata er slettet"}

# ---------------------------------------------------------
#  API: Matrix (oversigt)
# ---------------------------------------------------------
@app.get("/api/matrix")
async def get_matrix():
    async with SessionLocal() as session:
        rows = (await session.execute(select(Observation))).scalars().all()

    all_arter = set()
    all_koder = set()
    hovedart_data: Dict[str, Dict[str, List[datetime.date]]] = {}
    kode_ture: Dict[str, Dict[str, Any]] = {}
    kode_turid_set: Dict[str, set] = {}

    def hovedart(artnavn: str) -> str:
        return (artnavn or "").split('(')[0].split(',')[0].strip()

    for obs in rows:
        ha = hovedart(obs.artnavn)
        if "sp." in ha or "/" in ha or " x " in ha:
            continue
        all_arter.add(ha)
        all_koder.add(obs.obserkode)
        hovedart_data.setdefault(ha, {}).setdefault(obs.obserkode, []).append(obs.dato)

        if obs.turid and obs.turtidfra and obs.turtidtil:
            kode_ture.setdefault(obs.obserkode, {})[obs.turid] = (obs.turtidfra, obs.turtidtil)
        if obs.turid:
            kode_turid_set.setdefault(obs.obserkode, set()).add(obs.turid)

    arter = sorted(all_arter)
    koder = sorted(all_koder)
    matrix = []
    for art in arter:
        row = []
        for kode in koder:
            datoer = hovedart_data.get(art, {}).get(kode, [])
            row.append(min(datoer).strftime("%d-%m-%Y") if datoer else "")
        matrix.append(row)

    totals = [sum(1 for art in arter if hovedart_data.get(art, {}).get(kode)) for kode in koder]

    def tid_i_min(t1: str, t2: str) -> int:
        try:
            a = datetime.datetime.strptime(t1, "%H:%M")
            b = datetime.datetime.strptime(t2, "%H:%M")
            return max(0, int((b - a).total_seconds() // 60))
        except Exception:
            return 0

    tid_brugt = []
    for kode in koder:
        ture = kode_ture.get(kode, {})
        total = sum(tid_i_min(fra, til) for fra, til in ture.values())
        tid_brugt.append(f"{total//60:02}:{total%60:02}")

    antal_observationer = [len(kode_turid_set.get(kode, set())) for kode in koder]

    return {
        "arter": arter,
        "koder": koder,
        "matrix": matrix,
        "totals": totals,
        "tid_brugt": tid_brugt,
        "antal_observationer": antal_observationer
    }



# ---------------------------------------------------------
#  Grupper
# ---------------------------------------------------------

# --- GRUPPEMODEL (i memory eller database, her som fil for demo) ---
GRUPPEFIL = os.path.join(SERVER_DIR, "grupper.json")
EXCLUDED_SPECIES_FILE = os.path.join(SERVER_DIR, "excluded_species.json")
SPECIES_STYLES_FILE = os.path.join(SERVER_DIR, "species_styles.json")

def load_grupper():
    if not os.path.exists(GRUPPEFIL):
        return []
    with open(GRUPPEFIL, "r", encoding="utf-8") as f:
        return json.load(f)

def save_grupper(grupper):
    with open(GRUPPEFIL, "w", encoding="utf-8") as f:
        json.dump(grupper, f, ensure_ascii=False, indent=2)

def _normalize_species_name(value: str) -> str:
    return " ".join(str(value or "").strip().split())

def load_excluded_species() -> List[str]:
    if not os.path.exists(EXCLUDED_SPECIES_FILE):
        return []
    try:
        with open(EXCLUDED_SPECIES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    unique: Dict[str, str] = {}
    for item in data:
        name = _normalize_species_name(item)
        if not name:
            continue
        key = name.casefold()
        if key not in unique:
            unique[key] = name
    return sorted(unique.values(), key=lambda x: x.casefold())

def save_excluded_species(species: List[str]):
    cleaned = []
    seen = set()
    for item in species:
        name = _normalize_species_name(item)
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(name)
    cleaned.sort(key=lambda x: x.casefold())
    with open(EXCLUDED_SPECIES_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

def load_species_styles() -> Dict[str, str]:
    if not os.path.exists(SPECIES_STYLES_FILE):
        return {}
    try:
        with open(SPECIES_STYLES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    allowed = {"normal", "su", "subart"}
    result: Dict[str, str] = {}
    seen: set = set()
    for raw_name, raw_kind in data.items():
        name = _normalize_species_name(raw_name)
        if not name:
            continue
        kind = str(raw_kind or "normal").strip().lower()
        if kind not in allowed:
            kind = "normal"
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result[name] = kind
    return dict(sorted(result.items(), key=lambda kv: kv[0].casefold()))

def save_species_styles(styles: Dict[str, str]):
    if not isinstance(styles, dict):
        styles = {}
    allowed = {"normal", "su", "subart"}
    cleaned: Dict[str, str] = {}
    seen: set = set()
    for raw_name, raw_kind in styles.items():
        name = _normalize_species_name(raw_name)
        if not name:
            continue
        kind = str(raw_kind or "normal").strip().lower()
        if kind not in allowed:
            kind = "normal"
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned[name] = kind

    payload = dict(sorted(cleaned.items(), key=lambda kv: kv[0].casefold()))
    with open(SPECIES_STYLES_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def _merge_species_style_kind(current: str, incoming: str) -> str:
    rank = {"normal": 0, "subart": 1, "su": 2}
    c = incoming if incoming in rank else "normal"
    p = current if current in rank else "normal"
    return c if rank[c] >= rank[p] else p

def _extract_species_entries_from_dof_html(html_text: str) -> Dict[str, Dict[str, Any]]:
    rows = re.findall(r"<tr[^>]*>.*?</tr>", html_text or "", flags=re.IGNORECASE | re.DOTALL)
    found: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.IGNORECASE | re.DOTALL)
        if len(tds) < 3:
            continue
        species_cell = tds[2]
        cell_lower = species_cell.lower()

        species_kind = "normal"
        if "class=\"su\"" in cell_lower or "class='su'" in cell_lower:
            species_kind = "su"
        elif "class=\"subart\"" in cell_lower or "class='subart'" in cell_lower:
            species_kind = "subart"

        text = re.sub(r"<[^>]+>", "", species_cell)
        text = unescape(text).replace("\xa0", " ")
        text = _normalize_species_name(text)

        is_bracket = text.startswith("[") and text.endswith("]")
        name = _normalize_species_name(text[1:-1] if is_bracket else text)
        if not name:
            continue

        key = name.casefold()
        if key not in found:
            found[key] = {
                "name": name,
                "kind": species_kind,
                "is_bracket": is_bracket,
            }
            continue

        previous_kind = found[key].get("kind", "normal")
        found[key]["kind"] = _merge_species_style_kind(previous_kind, species_kind)
        if is_bracket:
            found[key]["is_bracket"] = True

    return found

def _sync_species_styles_and_excluded_from_dof() -> Dict[str, Any]:
    url = "https://dofbasen.dk/opslag/artdata.php"
    response = requests.get(url, timeout=25)
    response.raise_for_status()

    entries = _extract_species_entries_from_dof_html(response.text)
    if not entries:
        raise RuntimeError("Fandt ingen arter i DOFbasens artsliste")

    fetched_styles = {
        entry["name"]: entry.get("kind", "normal")
        for entry in entries.values()
        if entry.get("name")
    }
    existing_styles = load_species_styles()
    merged_styles = dict(existing_styles)
    for name, kind in fetched_styles.items():
        existing_kind = merged_styles.get(name, "normal")
        merged_styles[name] = _merge_species_style_kind(existing_kind, kind)
    save_species_styles(merged_styles)

    fetched_brackets = sorted({
        entry["name"]
        for entry in entries.values()
        if entry.get("is_bracket") and entry.get("name")
    }, key=lambda value: value.casefold())
    if not fetched_brackets:
        raise RuntimeError("Fandt ingen arter i klammer i DOFbasens artsliste")

    existing_excluded = load_excluded_species()
    save_excluded_species(existing_excluded + fetched_brackets)
    saved_excluded = load_excluded_species()

    su_count = sum(1 for kind in merged_styles.values() if kind == "su")
    subart_count = sum(1 for kind in merged_styles.values() if kind == "subart")

    return {
        "fetched_bracket_count": len(fetched_brackets),
        "excluded_total_count": len(saved_excluded),
        "styles_total_count": len(merged_styles),
        "su_count": su_count,
        "subart_count": subart_count,
    }

def find_gruppe(grupper, navn):
    for g in grupper:
        if g["navn"] == navn:
            return g
    return None

# --- ENDPOINTS ---

@app.get("/api/get_grupper")
async def get_grupper(request: Request):
    """Returnér alle grupper brugeren er medlem af."""
    session = request.session
    bruger = session.get("obserkode")
    grupper = load_grupper()
    mine = [g for g in grupper if bruger and bruger in g["obserkoder"]]
    return mine

@app.post("/api/create_gruppe")
async def create_gruppe(request: Request, data: dict = Body(...)):
    session = request.session
    bruger = session.get("obserkode")
    navn = sanitize_text(data.get("navn", "").strip())
    if not bruger or not navn:
        return JSONResponse({"ok": False, "msg": "Navn og login kræves"}, status_code=400)
    grupper = load_grupper()
    if any(g["navn"] == navn for g in grupper):
        return JSONResponse({"ok": False, "msg": "Gruppenavn findes allerede"}, status_code=400)
    grupper.append({"navn": navn, "obserkoder": [bruger]})
    save_grupper(grupper)
    return {"ok": True}

@app.post("/api/rename_gruppe")
async def rename_gruppe(request: Request, data: dict = Body(...)):
    """Omdøb gruppe (kun hvis bruger er medlem)."""
    session = request.session
    bruger = session.get("obserkode")
    gammel = sanitize_text(data.get("gammel_navn", "").strip())
    ny = sanitize_text(data.get("nyt_navn", "").strip())
    if not bruger or not gammel or not ny:
        return JSONResponse({"ok": False, "msg": "Navne og login kræves"}, status_code=400)
    grupper = load_grupper()
    if any(g["navn"] == ny for g in grupper):
        return JSONResponse({"ok": False, "msg": "Gruppenavn findes allerede"}, status_code=400)
    g = find_gruppe(grupper, gammel)
    if not g or bruger not in g["obserkoder"]:
        return JSONResponse({"ok": False, "msg": "Ingen adgang"}, status_code=403)
    g["navn"] = ny
    save_grupper(grupper)
    return {"ok": True}

@app.post("/api/delete_gruppe")
async def delete_gruppe(request: Request, data: dict = Body(...)):
    """Slet gruppe (kun hvis bruger er medlem)."""
    session = request.session
    bruger = session.get("obserkode")
    navn = data.get("navn", "").strip()
    grupper = load_grupper()
    g = find_gruppe(grupper, navn)
    if not g or bruger not in g["obserkoder"]:
        return JSONResponse({"ok": False, "msg": "Ingen adgang"}, status_code=403)
    grupper = [x for x in grupper if x["navn"] != navn]
    save_grupper(grupper)
    return {"ok": True}

@app.post("/api/add_gruppemedlem")
async def add_gruppemedlem(request: Request, data: dict = Body(...)):
    session = request.session
    bruger = session.get("obserkode")
    navn = sanitize_text(data.get("navn", "").strip())
    kode = sanitize_text(data.get("obserkode", "").strip())
    grupper = load_grupper()
    g = find_gruppe(grupper, navn)
    if not g or bruger not in g["obserkoder"]:
        return JSONResponse({"ok": False, "msg": "Ingen adgang"}, status_code=403)
    if kode and kode not in g["obserkoder"]:
        g["obserkoder"].append(kode)
        save_grupper(grupper)
    return {"ok": True}

@app.post("/api/remove_gruppemedlem")
async def remove_gruppemedlem(request: Request, data: dict = Body(...)):
    """Fjern medlem fra gruppe (kun hvis bruger er medlem)."""
    session = request.session
    bruger = session.get("obserkode")
    navn = data.get("navn", "").strip()
    kode = data.get("obserkode", "").strip()
    grupper = load_grupper()
    g = find_gruppe(grupper, navn)
    if not g or bruger not in g["obserkoder"]:
        return JSONResponse({"ok": False, "msg": "Ingen adgang"}, status_code=403)
    if kode in g["obserkoder"]:
        g["obserkoder"].remove(kode)
        save_grupper(grupper)
    return {"ok": True}

@app.post("/api/gruppe_scoreboard")
async def gruppe_scoreboard(request: Request, data: dict = Body(...)):
    """
    Returnér scoreboard for en gruppe (global eller matrikel) + matrix-data.
    Body: { "navn": "Fuglehold", "scope": "gruppe_alle" | "gruppe_matrikel", "aar": 2026 }
    """
    session = request.session
    bruger = session.get("obserkode")
    navn = data.get("navn", "").strip()
    scope = data.get("scope")
    aar = data.get("aar") or await get_global_year()
    grupper = load_grupper()
    g = find_gruppe(grupper, navn)
    if not g or bruger not in g["obserkoder"]:
        return JSONResponse({"ok": False, "msg": "Ingen adgang"}, status_code=403)
    # Find brugere i gruppen
   
    koder = g["obserkoder"]
    if str(aar) == "global":
        OBSER_DIR = os.path.join(SERVER_DIR, "data", "global", "obser")
    else:
        _, _, OBSER_DIR = get_data_dirs(aar)
    rows = []
    trend_points: Dict[str, List[Dict[str, Any]]] = {}

    if str(aar) == "global":
        range_start = datetime.date.min
        range_end = datetime.date.max
    else:
        year_value = int(aar)
        range_start = datetime.date(year_value, 1, 1)
        range_end = datetime.date(year_value, 12, 31)
    today = datetime.date.today()
    visible_end = min(range_end, today)

    global_filter_value = await get_global_filter()

    async with SessionLocal() as session:
        users = (await session.execute(select(User).where(User.obserkode.in_(koder)))).scalars().all()
    # --- Matrix-data ---
    all_arter = set()
    hovedart_data = {}
    matrix = []
    for u in users:
        if scope == "gruppe_alle":
            L = _load_json(os.path.join(OBSER_DIR, u.obserkode, "global.json")) or []
            
            # Generate trend_points for gruppe_alle
            async with SessionLocal() as dbsession:
                obs_query = select(Observation).where(Observation.obserkode == u.obserkode)
                if str(aar) != "global":
                    obs_query = obs_query.where(
                        Observation.dato >= range_start,
                        Observation.dato <= visible_end,
                    )
                obs_rows = (await dbsession.execute(obs_query)).scalars().all()
            
            firsts_by_art: Dict[str, datetime.date] = {}
            for obs_row in obs_rows:
                if not obs_row.dato:
                    continue
                if obs_row.dato > visible_end:
                    continue
                art_name = _normalize_base_art_name(obs_row.artnavn)
                if not art_name or "sp." in art_name or "/" in art_name or " x " in art_name:
                    continue
                key = art_name.casefold()
                previous = firsts_by_art.get(key)
                if previous is None or obs_row.dato < previous:
                    firsts_by_art[key] = obs_row.dato
            
            points: List[Dict[str, Any]] = []
            running = 0
            for first_date in sorted(firsts_by_art.values()):
                running += 1
                points.append({"dato": first_date.strftime("%d-%m-%Y"), "count": running})
            
            if points:
                trend_points[u.obserkode] = points
        elif scope == "gruppe_matrikel":
            L = _load_json(os.path.join(OBSER_DIR, u.obserkode, "matrikelarter.json")) or []

            user_periods = (_load_user_matrikel_periods(u) or {}).get("matrikel1") or []
            relevant_periods = [
                period for period in user_periods
                if _period_overlaps_range(period, range_start, visible_end)
            ]
            relevant_periods.sort(key=lambda period: period.get("start_date") or "")

            if relevant_periods:
                async with SessionLocal() as dbsession:
                    obs_query = select(Observation).where(Observation.obserkode == u.obserkode)
                    if str(aar) != "global":
                        obs_query = obs_query.where(
                            Observation.dato >= range_start,
                            Observation.dato <= visible_end,
                        )
                    obs_rows = (await dbsession.execute(obs_query)).scalars().all()

                points: List[Dict[str, Any]] = []
                for period in relevant_periods:
                    period_start = _parse_iso_date(period.get("start_date"))
                    if not period_start:
                        continue
                    if period_start > visible_end:
                        continue
                    period_end = _parse_iso_date(period.get("end_date")) or datetime.date.max
                    period_end = min(period_end, visible_end)
                    points.append({"dato": period_start.strftime("%d-%m-%Y"), "count": 0})

                    firsts_by_art: Dict[str, datetime.date] = {}
                    for obs_row in obs_rows:
                        if not obs_row.dato:
                            continue
                        if obs_row.dato > visible_end:
                            continue
                        if obs_row.dato < period_start or obs_row.dato > period_end:
                            continue
                        if not _observation_has_matrikel_tag(obs_row, global_filter_value, 1):
                            continue
                        art_name = _normalize_base_art_name(obs_row.artnavn)
                        if not art_name or "sp." in art_name or "/" in art_name or " x " in art_name:
                            continue
                        key = art_name.casefold()
                        previous = firsts_by_art.get(key)
                        if previous is None or obs_row.dato < previous:
                            firsts_by_art[key] = obs_row.dato

                    running = 0
                    for first_date in sorted(firsts_by_art.values()):
                        running += 1
                        points.append({"dato": first_date.strftime("%d-%m-%Y"), "count": running})

                if points:
                    trend_points[u.obserkode] = points
        else:
            return JSONResponse({"ok": False, "msg": "Ukendt scope"}, status_code=400)
        # Scoreboard-row
        a, art, dato = 0, "", ""
        if L:
            unique_arter = { (r.get("artnavn") or "").split("(")[0].split(",")[0].strip()
                             for r in L if r.get("artnavn") and "sp." not in r.get("artnavn") and "/" not in r.get("artnavn") and " x " not in r.get("artnavn") }
            a = len(unique_arter)
            latest = max(L, key=lambda r: _parse_ddmmyyyy(r.get("dato")), default={})
            art = latest.get("artnavn", "")
            dato = latest.get("dato", "")
        rows.append({
            "navn": u.navn or u.obserkode,
            "obserkode": u.obserkode,
            "antal_arter": a,
            "sidste_art": art,
            "sidste_dato": dato,
        })
        # Matrix-data
        for r in L:
            ha = (r.get("artnavn") or "").split("(")[0].split(",")[0].strip()
            if "sp." in ha or "/" in ha or " x " in ha:
                continue
            all_arter.add(ha)
            hovedart_data.setdefault(ha, {}).setdefault(u.obserkode, []).append(r.get("dato"))

    # Filtrér brugere med 0 arter fra
    rows = [r for r in rows if r.get("antal_arter", 0) > 0]
    koder_sorted = sorted([r["obserkode"] for r in rows])

    arter = sorted(all_arter)
    # Build matrix
    matrix = []
    for art in arter:
        row = []
        for kode in koder_sorted:
            datoer = hovedart_data.get(art, {}).get(kode, [])
            # Find tidligste dato for arten for denne kode
            if datoer:
                try:
                    d = min(datetime.datetime.strptime(d, "%d-%m-%Y") if isinstance(d, str) else d for d in datoer)
                    row.append(d.strftime("%d-%m-%Y"))
                except Exception:
                    row.append("")
            else:
                row.append("")
        matrix.append(row)
    # Totals
    totals = [sum(1 for art in arter if hovedart_data.get(art, {}).get(kode)) for kode in koder_sorted]
    # Tid og observationer (dummy, tilpas evt. til din logik)
    tid_brugt = ["00:00" for _ in koder_sorted]
    # Antal observationer pr. bruger (summen af alle observationer i L for hver bruger)
    antal_observationer = []
    for kode in koder_sorted:
        obs_count = 0
        for art in arter:
            obs_count += len(hovedart_data.get(art, {}).get(kode, []))
        antal_observationer.append(obs_count)

    # Sortér som de andre scoreboards
    rows.sort(key=lambda x: x["antal_arter"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["placering"] = i

    return {
        "rows": rows,
        "arter": arter,
        "koder": koder_sorted,
        "matrix": matrix,
        "totals": totals,
        "tid_brugt": tid_brugt,
        "antal_observationer": antal_observationer,
        "trend_points": trend_points,
    }

# ---------------------------------------------------------
#  API: Admin
# ---------------------------------------------------------
from fastapi import Depends

async def require_admin(request: Request):
    session = request.session
    if not session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin adgang")
    return True

@app.get("/api/admin/grupper")
async def admin_get_grupper(request: Request, admin: bool = Depends(require_admin)):
    """
    Returnér alle grupper (kun navn, ikke medlemmer). Kun admin.
    """
    grupper = load_grupper()
    return [{"navn": g["navn"]} for g in grupper]

@app.get("/api/admin/excluded_species")
async def admin_get_excluded_species(admin: bool = Depends(require_admin)):
    return {"species": load_excluded_species()}

@app.get("/api/species_styles")
async def get_species_styles():
    return {"styles": load_species_styles()}

@app.post("/api/admin/excluded_species")
async def admin_add_excluded_species(payload: Dict[str, Any] = Body(...), admin: bool = Depends(require_admin)):
    artnavn = _normalize_species_name(payload.get("artnavn", ""))
    if not artnavn:
        raise HTTPException(status_code=400, detail="Artnavn mangler")

    species = load_excluded_species()
    existing_keys = {name.casefold() for name in species}
    if artnavn.casefold() not in existing_keys:
        species.append(artnavn)
        save_excluded_species(species)
        species = load_excluded_species()
    return {"ok": True, "species": species}

@app.delete("/api/admin/excluded_species")
async def admin_remove_excluded_species(artnavn: str = Query(""), admin: bool = Depends(require_admin)):
    target = _normalize_species_name(artnavn)
    if not target:
        raise HTTPException(status_code=400, detail="Artnavn mangler")

    species = load_excluded_species()
    target_key = target.casefold()
    filtered = [name for name in species if name.casefold() != target_key]
    save_excluded_species(filtered)
    return {"ok": True, "species": filtered}

@app.post("/api/admin/excluded_species/save")
async def admin_save_excluded_species(payload: Dict[str, Any] = Body(...), admin: bool = Depends(require_admin)):
    species = payload.get("species")
    if not isinstance(species, list):
        raise HTTPException(status_code=400, detail="species skal være en liste")
    save_excluded_species([str(x) for x in species])
    saved = load_excluded_species()
    return {"ok": True, "species": saved, "count": len(saved)}

@app.post("/api/admin/excluded_species/sync")
async def admin_sync_excluded_species_from_dof(admin: bool = Depends(require_admin)):
    try:
        result = await asyncio.to_thread(_sync_species_styles_and_excluded_from_dof)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Kunne ikke hente artsliste fra DOFbasen: {exc}")

    return {
        "ok": True,
        "msg": (
            "Sync fuldført: "
            f"{result['fetched_bracket_count']} klamme-arter, "
            f"{result['su_count']} su-arter, "
            f"{result['subart_count']} subarter."
        ),
        "species": load_excluded_species(),
        "fetched_count": result["fetched_bracket_count"],
        "total_count": result["excluded_total_count"],
        "styles_count": result["styles_total_count"],
    }

@app.post("/api/admin/slet_gruppe")
async def admin_slet_gruppe(request: Request, data: dict = Body(...), admin: bool = Depends(require_admin)):
    """
    Slet en gruppe (kun admin). Body: { "navn": "Gruppenavn" }
    """
    navn = data.get("navn", "").strip()
    grupper = load_grupper()
    grupper = [g for g in grupper if g["navn"] != navn]
    save_grupper(grupper)
    return {"ok": True, "msg": f"Gruppe '{navn}' slettet"}


@app.post("/api/admin/sync_all_user_names")
async def admin_sync_all_user_names(request: Request, admin: bool = Depends(require_admin)):
    enforce_sync_rate_limit(request, 30)

    async with SessionLocal() as session:
        users = (await session.execute(select(User))).scalars().all()
        user_by_code: Dict[str, User] = {}
        for user in users:
            code = (user.obserkode or "").strip().upper()
            if SAFE_OBSERKODE_RE.fullmatch(code):
                user_by_code[code] = user

        all_codes = set(user_by_code.keys())
        obserkoder = (await session.execute(select(Obserkode.kode))).scalars().all()
        for raw_code in obserkoder:
            code = (raw_code or "").strip().upper()
            if SAFE_OBSERKODE_RE.fullmatch(code):
                all_codes.add(code)

        updated = 0
        created = 0
        unchanged = 0
        missing = 0

        for code in sorted(all_codes):
            fetched_name = await asyncio.to_thread(fetch_observer_name_from_dof, code)
            if not fetched_name:
                missing += 1
                continue

            user = user_by_code.get(code)
            if not user:
                user = User(obserkode=code, navn=fetched_name, lokalafdeling=None)
                session.add(user)
                user_by_code[code] = user
                created += 1
                continue

            if (user.navn or "").strip() != fetched_name:
                user.navn = fetched_name
                updated += 1
            else:
                unchanged += 1

        await session.commit()

    return {
        "ok": True,
        "users": len(all_codes),
        "updated": updated,
        "created": created,
        "unchanged": unchanged,
        "missing": missing,
        "msg": (
            "Navne-sync fuldført: "
            f"{updated} opdateret, {created} oprettet, "
            f"{unchanged} uændret, {missing} uden navn."
        ),
    }


@app.post("/api/admin/full_sync_user")
async def admin_full_sync_user(request: Request, kode: str, admin: bool = Depends(require_admin)):
    enforce_sync_rate_limit(request, 30)
    try:
        obserkode = normalize_obserkode(kode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ugyldig obserkode")

    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Obserkode ikke fundet")

    asyncio.create_task(sync_user_all_time(obserkode))
    return {"ok": True, "msg": f"Full sync startet for {obserkode}"}


@app.get("/api/admin/user_profile")
async def admin_user_profile(kode: str, admin: bool = Depends(require_admin)):
    try:
        obserkode = normalize_obserkode(kode)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ugyldig obserkode")

    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Obserkode ikke fundet")

    kommune_value = getattr(user, "kommune", None)
    kommune_id = None
    kommune_navn = None
    if kommune_value and str(kommune_value).isdigit():
        kommune_id = str(kommune_value)
        kommune_navn = _kommune_name_by_id(kommune_id)
    elif kommune_value:
        kommune_navn = str(kommune_value)
        for row in _read_kommuner():
            if row.get("navn") == kommune_navn:
                kommune_id = row.get("id")
                break

    return {
        "ok": True,
        "user": {
            "obserkode": obserkode,
            "navn": getattr(user, "navn", None),
            "lokalafdeling": getattr(user, "lokalafdeling", None),
            "kommune": kommune_value,
            "kommune_id": kommune_id,
            "kommune_navn": kommune_navn
        }
    }


@app.post("/api/admin/update_user")
async def admin_update_user(payload: Dict[str, Any] = Body(...), admin: bool = Depends(require_admin)):
    obserkode_raw = payload.get("obserkode")
    try:
        obserkode = normalize_obserkode(obserkode_raw or "")
    except ValueError:
        raise HTTPException(status_code=400, detail="Ugyldig obserkode")

    navn = (payload.get("navn") or "").strip()
    lokalafdeling = (payload.get("lokalafdeling") or "").strip()
    kommune = payload.get("kommune")
    kommune_value = (kommune or "").strip() if isinstance(kommune, str) else ""

    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.obserkode == obserkode))).scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Obserkode ikke fundet")

        user.navn = navn or user.navn
        user.lokalafdeling = lokalafdeling if lokalafdeling else None
        user.kommune = kommune_value if kommune_value else None
        await session.commit()

    return {"ok": True, "msg": "Bruger opdateret"}

# ---------------------------------------------------------
#  Startup
# ---------------------------------------------------------
async def ensure_user_optional_columns():
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS lokalafdelinger_json TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS kommuner_json TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS matrikel1_perioder TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS matrikel2_perioder TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS matrikel_perioder_json TEXT",
    ]
    for sql in statements:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
        except Exception:
            # Fallback til DB'er uden IF NOT EXISTS support
            try:
                plain_sql = sql.replace(" IF NOT EXISTS", "")
                async with engine.begin() as conn:
                    await conn.execute(text(plain_sql))
            except Exception:
                pass

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_user_optional_columns()
    print("[START] DB klar. Static peger på:", WEB_DIR)
    asyncio.create_task(schedule_daily_kommune_sync())
    asyncio.create_task(schedule_daily_year_sync())
    asyncio.create_task(schedule_daily_species_sync())


@app.post("/api/full_sync_all")
async def full_sync_all(request: Request):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Kun admin kan køre fuld sync for alle")
    enforce_sync_rate_limit(request, 30)
    asyncio.create_task(daily_update_all_jsons())
    return {"msg": "Fuld synkronisering for alle brugere er startet"}

# ---------------------------------------------------------
#  Static (peg på .../web ved siden af server/)
# ---------------------------------------------------------
if not os.path.isdir(WEB_DIR):
    # fallback hvis nogen kører alt inde i /server
    WEB_DIR = os.path.join(SERVER_DIR, "web")

@app.get("/sw.js")
async def sw():
    sw_path = os.path.join(WEB_DIR, "sw.js")
    if os.path.exists(sw_path):
        return FileResponse(sw_path, media_type="application/javascript")
    return JSONResponse({"ok": False, "msg": "sw.js ikke fundet"}, status_code=404)

@app.get("/")
async def root():
    index_path = os.path.join(WEB_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse({"ok": True, "msg": "Læg index.html i mappen 'web' på roden."})

app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="static")
