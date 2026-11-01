"""Kalite360 - veritabanindan otomatik model kesfi ve ortak model listesi.

Bir ürün kaydindaki model kodu (or. "Midas Rex (MR8-AF02R)")
otomatik algilanir. Kullanici eklemeleri model_registry tablosuna saklanir.
Orijinal sikayet metinlerine veya mevcut kayitlara dokunulmaz.
"""
import os
import re
from pathlib import Path
from db import db

# Yalnizca urun adindan teknik model kodu tahmin edilir. Serbest sikayet
# metinlerinden bilinmeyen modeller turetilmez (yanlis eslesme riski).
CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9._/-]{1,39}$")
PAREN_RE = re.compile(r"\(([^()]*)\)")
EXPLICIT_RE = re.compile(
    r"\b(?:model\s*(?:number|no|numarasi)?|model\s*number)\s*[:=#-]\s*([A-Z0-9][A-Z0-9._/-]{1,39})",
    flags=re.IGNORECASE,
)
TOKEN_RE = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9._/-]{2,39})(?![A-Z0-9])", re.IGNORECASE)


def normalize_model(value):
    val = str(value or "").strip().upper().replace("\u0130", "I")
    if not CODE_RE.fullmatch(val):
        return ""
    return val


def _automatic_candidate(value):
    val = normalize_model(value)
    # Rakam + harf icermeyen genel urun adlari, tarihler ve marka adlari model sanilmasin.
    if not val or not re.search(r"[A-Z]", val) or not re.search(r"\d", val):
        return ""
    if val in {"FDA2026", "MAUDE2026"}:
        return ""
    return val


def _match_known(text, names):
    upper = str(text or "").upper()
    for name in sorted(set(names), key=lambda s: (-len(s), s)):
        if re.search(r"(?<![A-Z0-9._/-])" + re.escape(name) + r"(?![A-Z0-9._/-])", upper):
            return name
    return ""


def detect_model_from_product(product, known_models=()):
    text = str(product or "").strip()
    if not text:
        return ""
    # Once bilinen modelin tam eslesmesi; onek benzerligi hata yaratmasin.
    known = _match_known(text, known_models)
    if known:
        return known
    for match in EXPLICIT_RE.finditer(text):
        name = _automatic_candidate(match.group(1))
        if name:
            return name
    for match in PAREN_RE.finditer(text):
        name = _automatic_candidate(match.group(1))
        if name:
            return name
    # Sadece urun alanindaki olasi teknik model tokeni (EM800, MR8-AF02R gibi).
    for match in TOKEN_RE.finditer(text.upper()):
        name = _automatic_candidate(match.group(1))
        if name:
            return name
    return ""


def resolve_record_model(row, known_models=()):
    """Urun sutunu onceliklidir; diger metinlerde sadece bilinen modeller aranir."""
    product = row.get("product", "") if hasattr(row, "get") else ""
    name = detect_model_from_product(product, known_models)
    if name:
        return name
    for field in ("findings", "description"):
        val = row.get(field, "") if hasattr(row, "get") else ""
        name = _match_known(val, known_models)
        if name:
            return name
    return "Diğer"


def _legacy_env_models():
    """Eski kayitli modelleri bir defaya mahsus DB'ye tasima uyumlulugu.

    .env dosyasi artik zorunlu degil ve kaydetme amaciyla degistirilmez.
    """
    raw = os.environ.get("SAVED_FDA_MODEL_NUMBERS", "")
    if not raw:
        root = Path(__file__).resolve().parent
        for p in (root / ".env", root / "services" / ".env"):
            if not p.is_file():
                continue
            try:
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.lstrip().startswith("SAVED_FDA_MODEL_NUMBERS="):
                        raw = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
            except (OSError, UnicodeError):
                pass
            if raw:
                break
    return [x for x in (normalize_model(t) for t in raw.split(",")) if x]


def sync_model_registry():
    """Her okumada yeni urun kodlarini bulur; mevcut durumu sifirlamaz."""
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_registry (
                model_code TEXT PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1,
                source TEXT NOT NULL DEFAULT 'auto',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        existing = {r[0] for r in conn.execute("SELECT model_code FROM model_registry")}
        legacy = set(_legacy_env_models())
        to_add = legacy - existing
        existing.update(to_add)
        try:
            products = conn.execute("SELECT DISTINCT product FROM complaints WHERE product IS NOT NULL AND TRIM(product) <> ''")
            for (product,) in products:
                code = detect_model_from_product(product, existing)
                if code and code not in existing:
                    to_add.add(code)
                    existing.add(code)
        except Exception as exc:
            # complaints tablosu yeni kurulumda henuz yoksa; tablo varsa diger hatalari gizleme.
            if "no such table: complaints" not in str(exc).lower():
                raise
        for code in sorted(to_add):
            conn.execute(
                "INSERT OR IGNORE INTO model_registry (model_code, active, source) VALUES (?, 1, ?)",
                (code, "legacy" if code in legacy else "auto"),
            )
        conn.commit()


def get_model_numbers(include_inactive=False):
    sync_model_registry()
    with db() as conn:
        query = "SELECT model_code FROM model_registry"
        if not include_inactive:
            query += " WHERE active=1"
        query += " ORDER BY model_code COLLATE NOCASE"
        return [r[0] for r in conn.execute(query).fetchall()]


def add_model(model):
    name = normalize_model(model)
    if not name:
        raise ValueError("Model kodu 2-40 karakterlik harf/rakam ve -._/ karakterleri icermelidir.")
    sync_model_registry()
    with db() as conn:
        old = conn.execute("SELECT active FROM model_registry WHERE model_code=?", (name,)).fetchone()
        conn.execute(
            "INSERT INTO model_registry(model_code, active, source) VALUES(?, 1, 'manual') "
            "ON CONFLICT(model_code) DO UPDATE SET active=1",
            (name,),
        )
        conn.commit()
    return not old or not old[0]


def set_model_numbers(models):
    models = list(models)
    requested = list(dict.fromkeys(filter(None, (normalize_model(t) for t in models))))
    invalid = [str(t) for t in models if str(t).strip() and not normalize_model(t)]
    if invalid:
        raise ValueError("Gecersiz model kodu: " + ", ".join(invalid[:5]))
    sync_model_registry()
    with db() as conn:
        for model in requested:
            conn.execute(
                "INSERT INTO model_registry(model_code, active, source) VALUES(?,1,'manual') "
                "ON CONFLICT(model_code) DO UPDATE SET active=1", (model,),
            )
        # Silme listeden kaldirmaktir; ilgili sikayet kayitlari korunur.
        for (model,) in conn.execute("SELECT model_code FROM model_registry WHERE active=1"):
            if model not in requested:
                conn.execute("UPDATE model_registry SET active=0 WHERE model_code=?", (model,))
        conn.commit()
    return requested


def remove_model(model, delete_complaints=False):
    name = normalize_model(model)
    if not name:
        return 0
    sync_model_registry()
    deleted = 0
    with db() as conn:
        if delete_complaints:
            known = [r[0] for r in conn.execute("SELECT model_code FROM model_registry")]
            rows = conn.execute("SELECT id, product, findings, description FROM complaints").fetchall()
            ids = [r[0] for r in rows if resolve_record_model({
                "product": r[1], "findings": r[2], "description": r[3]}, known) == name]
            for cid in ids:
                conn.execute("DELETE FROM complaints WHERE id=?", (cid,))
            deleted = len(ids)
        conn.execute("UPDATE model_registry SET active=0 WHERE model_code=?", (name,))
        conn.commit()
    return deleted
