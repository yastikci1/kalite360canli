"""Shared, local, evidence-based failure taxonomy for Kalite360.

Labels describe reported/observed failure modes, not proven causal mechanisms.
Manufacturer findings take precedence over reported symptoms; secondary findings
are retained. No network, database mutation or model/API dependency is used.
"""
from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

CLASSIFIER_VERSION = "2026.10.29-v1"
UNKNOWN_GROUP = "Sınıflandırılamayan"
UNKNOWN_SUBTYPE = "Belirli arıza bulgusu yok"


def _text(value):
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"", "-", "none", "nan", "null", "<na>"} else text


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", _text(text).lower().replace("ı", "i")) if not unicodedata.combining(c)).replace("’", "'")


def _paired(part, fault):
    # Allow linking words, but never cross into another coordinated finding.
    link = r"(?:\s+(?:was|were|is|are|has|had|have|been|be|found|to|that|with|of|the|a|an|out|partially|fully|severely|excessively|also|felt|runs|running|run|durumda|oldugu|tespit|edilen)){0,7}\s+"
    return rf"\b{part}{link}(?:{fault})\b|\b(?:{fault})\s+(?:(?:distal|proximal|front|rear|internal|tube|output|input|drive|motor)\s+){{0,3}}{part}\b"


B = r"(?:bearings?|rulman\w*|bilya\w*)"
SHAFT = r"(?:(?:input|output|drive|driver|rotor)\s+)?(?:shafts?|gears?|teeth|mil\w*|disli\w*)"
BODY = r"(?:tube(?:\s+tip)?|housing|casing|body|guard|footplate|leg|tip|tup\w*|govde\w*|muhafaza\w*)"
DETACH = r"detach(?:ed|ment|ing)?|separat(?:ed|ion)|dislodged|fell out|fell off|came out|loose|ayril\w*|yerinden\s+(?:cik|ayril)\w*"
BREAK = r"broken|break(?:age)?|fractur(?:ed|e)|crack(?:ed)?|shatter(?:ed)?|snapped|kiril\w*|kirik\w*|catla\w*|dagil\w*|parcalan\w*"
WEAR = r"worn(?:\s+out)?|wear|scored|scoring|asin\w*|yipran\w*"
CORROSION = r"corrod(?:ed|ing)|corrosion|rust(?:ed)?|oxidis(?:ed|ation)|oxidiz(?:ed|ation)|korozyon\w*|paslan\w*|oksitlen\w*"
RULES = [
    ("Rulman sistemi", "Rulman yerinden ayrılması", _paired(B, DETACH)),
    ("Rulman sistemi", "Rulman kırılması / dağılması", _paired(B, BREAK)),
    ("Rulman sistemi", "Rulman aşınması", _paired(B, WEAR)),
    ("Rulman sistemi", "Rulman korozyonu", _paired(B, CORROSION)),
    ("Rulman sistemi", "Rulman sıkışması / pürüzlü çalışma", _paired(B, r"seized|stuck|rough|sikis\w*|puruz\w*")),
    ("Rulman sistemi", "Rulman hizalama / yataklama sorunu", _paired(B, r"misaligned|misalignment|displaced|out of alignment|hiza\w* bozuk\w*|yataklama sorun\w*")),
    ("Rulman sistemi", "Rulman hasarı (türü belirtilmemiş)", _paired(B, r"damaged|defective|defect|bad|faulty|hasar\w*|ariza\w*")),
    ("Uç ve kilitleme", "Uç / aksesuar takılamaması", r"\b(?:unable|cannot|can't|could not|difficult|difficulty|not possible)\s+(?:\w+\s+){0,3}insert\w*\b|\b(?:bur|burr|tool|uc\w*)\s+(?:\w+\s+){0,2}(?:cannot be inserted|could not be inserted|difficult to insert|takilam\w*)\b|\b(?:difficult|difficulty)\s+(?:with\s+)?(?:bur\w*\s+)?insertion\b"),
    ("Uç ve kilitleme", "Uç / aksesuar çıkarılamaması", r"\b(?:bur|burr|tool|uc\w*)\s+(?:was |is |were |had |been |found |to be |has )*(?:stuck|jammed|seized|sikis\w*|cikarilam\w*)\b|\b(?:stuck|jammed)\s+(?:bur|burr|tool)\b|\b(?:unable|cannot|can't|difficult|difficulty)\s+(?:to\s+)?(?:remove|extract)\s+(?:the\s+)?(?:bur|burr|tool)\b"),
    ("Uç ve kilitleme", "Uç kilitleme / tutma kaybı", r"\b(?:cannot|can't|could not|would not|does not|doesn't|did not|unable to|not possible to)\s+(?:\w+\s+){0,3}(?:lock|hold|secure)\b|\b(?:bur|burr|tool)\s+(?:came out|slipped|fell out)\b|\b(?:uc\w*|pens\w*)\s+(?:tutm\w*|kilitlenm\w*)\b"),
    ("Uç ve kilitleme", "Pens / kilit mekanizması hasarı", _paired(r"(?:collet|chuck|locking mechanism|detent|pens\w*|kilit\w*)", r"broken|damaged|stuck|worn|jammed|defect\w*|sikis\w*|hasar\w*|ariza\w*")),
    ("Uç ve kilitleme", "Aksesuar oturma / bağlantı uyumsuzluğu", r"\b(?:would not|will not|does not|cannot|could not)\s+(?:seat|attach|engage|fit)\b|\b(?:assembly difficulty|attachment difficulty|assembly incompatibility|aksesuar uyumsuz\w*|yerine oturm\w*)\b"),
    ("Tüp ve hareket mekanizması", "Tüp uzatma / geri çekme sorunu", r"\b(?:tube\s+(?:\w+\s+){0,3}(?:stuck|jammed|locked)|(?:cannot|can't|does not|doesn't|would not|unable to|not possible to)\s+(?:\w+\s+){0,3}(?:extend|retract)|(?:extend|retract)\s+(?:and |or |/)?(?:extend|retract)?\s*(?:tube)?\s*(?:is |was )?(?:stuck|jammed))\b|\btup\w*\s+(?:uzam\w*|kisalm\w*|sikis\w*|geri\s+cekilem\w*)\b"),
    ("Tüp ve hareket mekanizması", "Ayar halkası / hareket sertliği", _paired(r"(?:dial|adjustment ring|collar|ayar\w*)", r"rough|stiff|stuck|jammed|hard|damaged|sert\w*|sikis\w*")),
    ("Mil ve dişli sistemi", "Mil / dişli aşınması", _paired(SHAFT, WEAR)),
    ("Mil ve dişli sistemi", "Mil / dişli kırılması", _paired(SHAFT, BREAK)),
    ("Mil ve dişli sistemi", "Mil / dişli korozyonu", _paired(SHAFT, CORROSION)),
    ("Gövde ve yapısal hasar", "Tüp / gövde eğilmesi veya deformasyonu", _paired(BODY, r"bent|deformed|dent(?:ed|s)?|flattened|enlarged|out of shape|yamul\w*|egil\w*|ezil\w*|deform\w*")),
    ("Gövde ve yapısal hasar", "Tüp / gövde kırılması veya çatlağı", _paired(BODY, BREAK)),
    ("Gövde ve yapısal hasar", "Uç / gövde aşınması", _paired(BODY, WEAR)),
    ("Gövde ve yapısal hasar", "Kesici uç kırılması", _paired(r"(?:bur|burr|cutter|cutting tip|kesici\s+uc\w*)", BREAK)),
    ("Gövde ve yapısal hasar", "Parça gevşemesi / ayrılması", r"\b(?:detached|loose|separated)\s+(?:component|part|stator)\b|\b(?:component|part|stator|rings?)\s+(?:was |is |were |found |to be )*(?:detached|loose|separated|fell out|fell off)\b|\bparca\w*\s+(?:ayril\w*|gevs\w*|kop\w*)\b"),
    ("Gövde ve yapısal hasar", "Ölçü / tolerans uygunsuzluğu", r"\b(?:depth|gap|dimension|diameter|runout|retainer|collet)\b[^.;]{0,55}\b(?:out of spec\w*|outside (?:the )?spec\w*|out of tolerance)\b|\b(?:olcu|tolerans|cap)\w*\s+(?:disi|uygunsuz\w*|sapma\w*)\b"),
    ("Gövde ve yapısal hasar", "Diğer kırılma / çatlak", rf"\b(?:{BREAK})\b"),
    ("Gövde ve yapısal hasar", "Diğer mekanik aşınma", rf"\b(?:{WEAR})\b"),
    ("Termal sorunlar", "Aşırı ısınma", r"\b(?:over[- ]?heat\w*|excessive heat|extremely hot|too hot|hot to (?:the )?touch|became hot|getting hot|thermal damage|asiri isin\w*|cok isin\w*|asiri sicak\w*|isiniyor|isindi)\b"),
    ("Termal sorunlar", "Isıl hasar / yanma bulgusu", r"\b(?:burnt|burned|scorched|melted|thermal burn|yanik\w*|erime\w*|eridi)\b"),
    ("Elektrik ve bağlantı", "Kablo / konnektör hasarı", _paired(r"(?:cable|wire|cord|connector|kablo\w*|konnektor\w*)", r"damaged|broken|frayed|cut|exposed|hasar\w*|kop\w*")),
    ("Elektrik ve bağlantı", "Bağlantı temassızlığı", r"\b(?:intermittent connection|loose connection|temassiz\w*|baglanti kop\w*)\b"),
    ("Elektrik ve bağlantı", "Elektrik kaçağı / izolasyon sorunu", r"\b(?:failed\s+(?:the\s+)?(?:hipot|hi-pot|insulation)|(?:hipot|hi-pot)\s+(?:test\s+)?fail\w*|electrical leakage|electric shock|insulation failure|elektrik kacagi|izolasyon (?:hata\w*|sorun\w*))\b"),
    ("Elektrik ve bağlantı", "Elektronik / sargı arızası", _paired(r"(?:board|pcb|sensor|stator|coil|winding|kart\w*|sensor\w*|bobin\w*|sargi\w*)", r"shorted|burned|failed|damaged|defective|ariza\w*|hasar\w*")),
    ("Elektrik ve bağlantı", "Duman / kıvılcım / kısa devre", r"\b(?:smoke|smoking|spark(?:s|ing)?|arcing|short circuit|duman\w*|kivilcim\w*|kisa devre)\b"),
    ("Fonksiyon ve hareket", "Çalışmama / ani durma", r"\b(?:stalled|stalling|stopped working|not (?:working|functioning|rotating)|(?:did|does|would) not (?:work|function|operate|run|rotate)|(?:unable|failed) to (?:work|operate|run)|failed (?:during|while)|inoperative|non-functional|calismiyor|calismadi|durdu|donmuyor)\b"),
    ("Fonksiyon ve hareket", "Güç / devir kaybı", r"\b(?:loss of power|low power|no power|low torque|speed fluctuation\w*|slow rotation|insufficient torque|guc kay\w*|devir (?:kay\w*|dus\w*)|tork kay\w*)\b"),
    ("Fonksiyon ve hareket", "Anormal ses", r"\b(?:noise|noisy|strange noise|rattling|squeal\w*|grinding noise|loud|anormal ses|gurultu\w*|ses yapiyor)\b"),
    ("Fonksiyon ve hareket", "Titreşim / salgı", r"\b(?:vibrat\w*|wobbl\w*|run[- ]?out|titresim\w*|salgi\w*|yalpala\w*)\b"),
    ("Yüzey ve sızdırmazlık", "Tüp / gövde korozyonu", _paired(BODY, CORROSION)),
    ("Yüzey ve sızdırmazlık", "Diğer korozyon / oksitlenme", rf"\b(?:{CORROSION})\b"),
    ("Yüzey ve sızdırmazlık", "İşaret / renk halkası silinmesi", r"\b(?:laser marking\w*|markings?|color (?:ring|band)|colour (?:ring|band)|index marks|lazer\w*|isaret\w*|renk halka\w*)\b[^.;]{0,70}\b(?:fad\w*|illegible|illigible|discolo\w*|worn|not (?:good|clear|legible)|silin\w*|solm\w*|okunam\w*)\b"),
    ("Yüzey ve sızdırmazlık", "Kirlenme / yabancı madde", r"\b(?:contaminat\w*|foreign (?:material|body)|debris|kirlen\w*|yabanci (?:madde|cisim))\b"),
    ("Yüzey ve sızdırmazlık", "Sızdırmazlık / sıvı kaçağı", r"\b(?:fluid leak\w*|air leak\w*|water ingress|fluid ingress|seal failure|sivi kac\w*|hava kac\w*|sizdirma\w*)\b"),
]
_COMPILED = tuple((group, subtype, re.compile(pattern)) for group, subtype, pattern in RULES)
SUBTYPE_TO_GROUP = {subtype: group for group, subtype, _ in RULES}
SUBTYPE_TO_GROUP[UNKNOWN_SUBTYPE] = UNKNOWN_GROUP
ALL_SUBTYPES = tuple(SUBTYPE_TO_GROUP)
FAILURE_GROUPS = tuple(dict.fromkeys(SUBTYPE_TO_GROUP.values()))
_GENERIC = {"Diğer kırılma / çatlak", "Diğer mekanik aşınma", "Diğer korozyon / oksitlenme"}


def _strip_boilerplate(text):
    return re.split(r"medtronic (?:submits|is submitting)|this report does not constitute|this regulatory report is being submitted|bu rapor.*?(?:kabul|mevzuat)", text, maxsplit=1, flags=re.I)[0]


def _negative(text, start, end):
    before, after = text[max(0, start - 100):start], text[end:end + 65]
    if re.search(r"\b(?:no|without|neither)\s+(?:(?:evidence|signs?|indications?)\s+of\s+)?(?:(?:any|abnormal|excessive|reported|observed|significant|device|further)\s+)*$", before):
        return True
    if re.search(r"\b(?:no|without|neither)\s+(?:(?:overheating|noise|vibration|damage|corrosion|wear|failure|fault|and|or)\b[\s,]*)+$", before):
        return True
    if re.search(r"\b(?:not|never)\s+(?:(?:found|observed|confirmed|reproduced|detected|reported|be|been|to)\s+)*$", before):
        return True
    if re.match(r"\s*(?:(?:was|were|is|are|could|has|been|could|be|was able to)\s+)*(?:not (?:found|observed|confirmed|reproduced|detected)|absent|yok\w*|gorulmedi|gozlenmedi|saptanmadi|tespit edilmedi)", after):
        return True
    # Do not turn a successful test or an unreplicated complaint into a finding.
    if re.search(r"could not reproduce|not reproduced|not confirmed|no fault found|within (?:the )?specification", text):
        return True
    if re.search(r"\b(?:if|may|might|could potentially|should)\s+(?:\w+\s+){0,3}$", before):
        return True
    return False


@dataclass(frozen=True)
class Finding:
    subtype: str
    group: str
    source: str
    evidence: str
    rank: int


@dataclass(frozen=True)
class FailureClassification:
    group: str
    subtype: str
    findings: tuple
    cause_statement: str
    cause_status: str

    @property
    def labels(self):
        return tuple(dict.fromkeys(f.subtype for f in self.findings)) or (UNKNOWN_SUBTYPE,)

    @property
    def source(self):
        return self.findings[0].source if self.findings else "Yeterli metin bulgusu yok"

    def to_record(self):
        return {
            "Arıza Grubu": self.group, "Alt Arıza Türü": self.subtype,
            "Ek Bulgular": "; ".join(x for x in self.labels if x != self.subtype) or "Yok",
            "Sınıflandırma Kaynağı": self.source,
            "Sınıflandırma Kanıtı": self.findings[0].evidence if self.findings else "-",
            "Kök Neden Durumu": self.cause_status, "Raporlanan Neden": self.cause_statement or "Belirtilmemiş",
            "_failure_labels": self.labels,
        }


def _find(text, source, base_rank):
    findings = []
    # Splitting contrast phrases keeps a negative test from hiding a later defect.
    additional = False
    for sentence in re.split(r"(?<=[.;!?])\s*|\b(?:however|but|fakat|ancak)\b", _strip_boilerplate(text), flags=re.I):
        if not sentence.strip():
            continue
        # A secondary finding remains secondary until the next sentence; later
        # evaluation statements can reset the section priority.
        pieces = re.split(r"(?=\bit (?:was|is) (?:(?:also )?noted|noted also)|\bfurthermore\b|\bayrica\b)", sentence, flags=re.I)
        for piece in pieces:
            folded = _fold(piece)
            folded = re.sub(r"\(\s*(?:distal|proximal|front|rear)\s*\)", "", folded)
            if re.search(r"it (?:was|is) (?:(?:also )?noted|noted also)|ayrica|furthermore", folded):
                additional = True
            if re.search(r"evaluation (?:determined|confirmed)|degerlendirme.*tespit", folded):
                additional = False
            cause_clause = bool(re.search(r"likely cause|root cause|kok neden|olasi neden", folded))
            rank = base_rank - (8 if cause_clause else (55 if additional else 0))
            local = []
            for group, subtype, pattern in _COMPILED:
                for m in pattern.finditer(folded):
                    if not _negative(folded, m.start(), m.end()):
                        local.append((m.start(), Finding(subtype, group, source, piece.strip()[:900], rank)))
                        break
            # Specific component defects replace the corresponding vague label
            # only when they are in this same statement, not across the record.
            specific_break = any("kırılması" in f.subtype or "çatlağı" in f.subtype for _, f in local if f.subtype not in _GENERIC)
            specific_wear = any("aşınması" in f.subtype for _, f in local if f.subtype not in _GENERIC)
            specific_corrosion = any("korozyonu" in f.subtype for _, f in local if f.subtype not in _GENERIC)
            for pos, finding in sorted(local, key=lambda item: item[0]):
                if finding.subtype == "Diğer kırılma / çatlak" and specific_break:
                    continue
                if finding.subtype == "Diğer mekanik aşınma" and specific_wear:
                    continue
                if finding.subtype == "Diğer korozyon / oksitlenme" and specific_corrosion:
                    continue
                findings.append(finding)
    return findings


def _cause(report, root):
    if root:
        clauses = [root]
        status = "Kayıtlı neden açıklaması (doğrulama ayrıca değerlendirilmeli)"
    else:
        clauses = re.findall(r"[^.;!?]*(?:likely cause|root cause|kök neden|olası neden)[^.;!?]*[.;!?]?", _strip_boilerplate(report), flags=re.I)
        status = "Üreticinin olası neden açıklaması"
    if not clauses:
        return "", "Neden açıklaması bulunmuyor"
    statement = " ".join(clauses).strip()[:1200]
    if re.search(r"could not|couldn't|couldn.t|not (?:be |able to |been )?(?:determin|identif|confirm)|belirlenem|tespit edileme", _fold(statement)):
        status = "Üretici nedeni belirleyememiş"
    return statement, status


@lru_cache(maxsize=8192)
def _classify(description, report, root):
    cause_statement, cause_status = _cause(report, root)
    findings = _find(report, "Üretici / teknik servis raporu", 100)
    findings += _find(root, "Kayıtlı neden açıklaması", 95)
    findings += _find(description, "Şikâyet metni (bildirim)", 60)
    # Never drop a legitimate minority category to make the distribution uniform.
    # Prefer specific defects to generic functional symptoms in the same tier.
    generic_penalty = _GENERIC | {"Çalışmama / ani durma", "Güç / devir kaybı"}
    ordered = sorted(enumerate(findings), key=lambda item: (-(item[1].rank - (10 if item[1].subtype in generic_penalty else 0)), item[0]))
    unique = {}
    for _, finding in ordered:
        unique.setdefault(finding.subtype, finding)
    findings = tuple(unique.values())
    primary = findings[0] if findings else None
    return FailureClassification(primary.group if primary else UNKNOWN_GROUP, primary.subtype if primary else UNKNOWN_SUBTYPE, findings, cause_statement, cause_status)


def classify_failure(row):
    """Classify a dict/Series without changing it; original text before translation."""
    return _classify(
        _text(row.get("description")) or _text(row.get("tr_description")),
        _text(row.get("action_plan")) or _text(row.get("tr_action_plan")),
        _text(row.get("root_cause")),
    )


def extract_root_cause(text):
    """Compatibility wrapper; the returned label is a failure mode, not proof."""
    return classify_failure({"description": text}).subtype


def resolve_smart_root_cause(row):
    return classify_failure(row).subtype


def annotate_failures(frame):
    """Return a copy with the same index and taxonomy columns on every screen."""
    out = frame.copy()
    records = [classify_failure(row).to_record() for row in frame.to_dict("records")]
    keys = FailureClassification(UNKNOWN_GROUP, UNKNOWN_SUBTYPE, (), "", "").to_record()
    for key in keys:
        out[key] = [r[key] for r in records]
    return out


def filter_failures(frame, label):
    if frame.empty:
        return frame.copy()
    if label in FAILURE_GROUPS:
        mask = frame["_failure_labels"].map(lambda labels: any(SUBTYPE_TO_GROUP[x] == label for x in labels))
    else:
        mask = frame["_failure_labels"].map(lambda labels: label in labels)
    return frame[mask]


def detect_failure_filter(query):
    q = _fold(query)
    for label in sorted(ALL_SUBTYPES, key=len, reverse=True):
        if _fold(label) in q:
            return label
    detected = extract_root_cause(query)
    if detected != UNKNOWN_SUBTYPE:
        return detected
    aliases = [
        (r"rulman|bearing|bilya", "Rulman sistemi"),
        (r"pens|collet|kilit|uc tak|uc cikar", "Uç ve kilitleme"),
        (r"tup.*(?:hareket|uzat|cek)|ayar halkasi", "Tüp ve hareket mekanizması"),
        (r"mil|disli|shaft|gear", "Mil ve dişli sistemi"),
        (r"kirik|kirilma|catlak|deformasyon", "Gövde ve yapısal hasar"),
        (r"isinma|termal|overheat", "Termal sorunlar"),
        (r"kablo|elektrik|izolasyon|temassiz", "Elektrik ve bağlantı"),
        (r"ses|titresim|calism|guc kaybi", "Fonksiyon ve hareket"),
        (r"korozyon|paslan|oksit|isaret|renk halkasi|sizdirmazlik", "Yüzey ve sızdırmazlık"),
    ]
    for pattern, group in aliases:
        if re.search(rf"\b(?:{pattern})", q):
            return group
    return ""


def legacy_failure_group(subtype):
    """Preserve existing SLA heuristics while changing the display taxonomy."""
    group = SUBTYPE_TO_GROUP.get(subtype, UNKNOWN_GROUP)
    return {
        "Rulman sistemi": "Rulman / Mekanik Aşınma",
        "Uç ve kilitleme": "Mekanik Sıkışma / Kilitlenme",
        "Tüp ve hareket mekanizması": "Mekanik Sıkışma / Kilitlenme",
        "Mil ve dişli sistemi": "Rulman / Mekanik Aşınma",
        "Gövde ve yapısal hasar": "Fiziksel Kırılma / Çatlak",
        "Termal sorunlar": "Aşırı Isınma", "Elektrik ve bağlantı": "Elektriksel Hata / Kaçak",
        "Fonksiyon ve hareket": "Ani Durma / Çalışmama",
        "Yüzey ve sızdırmazlık": "Korozyon / Oksitlenme",
    }.get(group, "Diğer / Tanımlanamayan")
