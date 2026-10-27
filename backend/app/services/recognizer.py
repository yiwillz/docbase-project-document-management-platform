"""智能投递的识别器（PRD F4.2、第 6 节"识别器插件化"）。

接口：输入一句描述、文件名、文件夹路径、可选的供应商，输出候选位置（应交项）+ 置信度 + 命中理由，
以及猜测的来源、客户退回的线索（传送单号、审核代码）。第一版只有规则识别器；第二版加 AI 识别器，
实现同一个接口，和规则识别器并用。

规则识别器：一批文件先建一次项目索引（ProjectIndex），每个文件在"描述 + 文件夹名 + 文件名"里找线索：
- 平台命名的文件再传回来（"1.2.72&73_…"）：开头的种类编号和设备编号，最强；
- 种类编号（3.5）、设备编号（72）：作为独立的一段出现才算，日期和版本号里的数字先去掉；
- 设备名 / 别名、种类名 / 识别关键词 / 分类、模块名：词组匹配，长的优先；
- 选了供应商就只在这家的设备里找；还没交（没有文件）的应交项加一点分。
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Deliverable,
    DocumentType,
    Equipment,
    FileDeliverable,
    Module,
    ReviewCode,
    Transmittal,
    TransmittalItem,
)
from app.services import naming
from app.services import review_codes as codes_svc

HIGH, MEDIUM = 0.75, 0.45  # 置信度分档：≥ 0.75 高，≥ 0.45 中，其余低（默认进待归类）
_WORD = re.compile(r"[^\W_]+(?:\.[^\W_]+)*")  # 词：字母数字，允许中间带点（3.5、v1.2）
_SPLIT = re.compile(r"[\s_\-/\\()\[\]{},;:&+#'\"]+")
# 太常见、不该单独算作种类名命中的词
_STOP = {"the", "and", "for", "of", "to", "a", "an", "in", "on", "with", "by", "doc", "docs",
         "document", "documents", "file", "files", "list", "data", "rev", "ver", "version",
         "final", "draft", "new", "copy", "pdf"}


def level(score: float) -> str:
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


# ---------------------------------------------------------------- 输入输出


@dataclass
class RecognizeInput:
    filename: str
    description: str | None = None
    folder: str | None = None  # 拖入文件夹时的相对路径（不含文件名）
    vendor: str | None = None  # 先选的供应商，缩小范围


@dataclass
class Reason:
    kind: str  # platform_name / submittal_no / tag / equipment_name / type_name / keyword / category / module / vendor / not_delivered
    text: str  # 命中的内容
    where: str  # filename / folder / description / -


@dataclass
class Candidate:
    deliverable_ids: list[int]
    score: float
    reasons: list[Reason] = field(default_factory=list)

    @property
    def level(self) -> str:
        return level(self.score)

    def out(self) -> dict[str, Any]:
        return {"deliverable_ids": self.deliverable_ids, "score": round(self.score, 2),
                "level": self.level, "reasons": [r.__dict__ for r in self.reasons]}


@dataclass
class ClientReply:
    """客户退回的线索：审核代码 + 传送单（文件名里写了，或这一项最近一张没回复的传送单）。"""

    code: str
    code_text: str
    transmittal_id: int | None
    transmittal_no: str | None
    item_ids: list[int] = field(default_factory=list)

    def out(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class Recognition:
    candidates: list[Candidate]
    source: str | None  # 猜的来源：client / vendor / None（按位置默认）
    client_reply: ClientReply | None = None
    transmittal_hint: tuple[int, str] | None = None

    @property
    def best(self) -> Candidate | None:
        return self.candidates[0] if self.candidates else None

    def out(self) -> dict[str, Any]:
        return {"candidates": [c.out() for c in self.candidates], "source": self.source,
                "client_reply": self.client_reply.out() if self.client_reply else None,
                "level": self.best.level if self.best else "low"}


class Recognizer(Protocol):
    def recognize(self, item: RecognizeInput) -> Recognition: ...


# ---------------------------------------------------------------- 文字处理


def norm(text: str | None) -> str:
    """小写、分隔符换成空格、去掉多余空格。"""
    return " ".join(_SPLIT.split((text or "").casefold())).strip()


def strip_noise(text: str) -> str:
    """去掉版本号和日期（里面的数字不能当设备编号）。"""
    spans = [m.span() for m in naming.VERSION_RE.finditer(text)]
    spans += [m.span() for m, _ in naming._dates(text)]
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return text


def has_phrase(haystack: str, phrase: str) -> bool:
    """词组作为完整的词出现（haystack、phrase 都已 norm）。"""
    if not phrase:
        return False
    return re.search(rf"(?<![^\W_]){re.escape(phrase)}(?![^\W_])", haystack) is not None


def has_code(haystack: str, code: str) -> bool:
    """编号作为独立的一段出现：前后不能紧挨字母数字，也不能是更长编号的一部分（3.5 不算 13.5、3.50）。"""
    return re.search(rf"(?<![^\W_.]){re.escape(code)}(?![^\W_]|\.[^\W_])", haystack) is not None


def words(text: str) -> set[str]:
    return {w for w in _WORD.findall(text) if len(w) > 1 and w not in _STOP}


# ---------------------------------------------------------------- 项目索引


@dataclass
class _Doc:
    id: int
    no: str
    name: str
    name_n: str
    phrases: list[tuple[str, str, str]]  # (norm 后的词组, kind：type_name / keyword / category, 原写法)
    words: set[str]
    per_equipment: bool
    per_module: bool


@dataclass
class _Eq:
    id: int
    tag: str | None
    tag_n: str | None
    name: str
    phrases: dict[str, str]  # 名称和别名：norm 后 → 原写法
    vendor: str | None
    vendor_n: str | None


class ProjectIndex:
    """一批文件识别前读一次项目结构。"""

    def __init__(self, session: Session, project_id: int) -> None:
        self.project_id = project_id
        docs = session.scalars(select(DocumentType).where(
            DocumentType.project_id == project_id, DocumentType.active)).all()
        self.docs: dict[int, _Doc] = {}
        for d in docs:
            phrases = [(norm(d.name), "type_name", d.name)]
            phrases += [(norm(k), "keyword", k) for k in d.keywords or []]
            if d.category:
                phrases.append((norm(d.category), "category", d.category))
            self.docs[d.id] = _Doc(id=d.id, no=d.submittal_no, name=d.name, name_n=norm(d.name),
                                   phrases=[p for p in phrases if p[0]],
                                   words=words(norm(d.name)) | {w for k in d.keywords or []
                                                                for w in words(norm(k))},
                                   per_equipment=d.per_equipment, per_module=d.per_module)
        self.equipment: dict[int, _Eq] = {}
        for e in session.scalars(select(Equipment).where(Equipment.project_id == project_id,
                                                         Equipment.active)):
            self.equipment[e.id] = _Eq(
                id=e.id, tag=e.tag, tag_n=e.tag.casefold() if e.tag else None, name=e.name,
                phrases={norm(x): x for x in [e.name, *(e.aliases or [])] if norm(x)},
                vendor=e.vendor, vendor_n=norm(e.vendor) if e.vendor else None)
        self.modules = {m.id: (m.name, norm(m.name)) for m in session.scalars(
            select(Module).where(Module.project_id == project_id, Module.active))}
        # 应交项：(种类, 设备, 模块) → id
        self.deliverables: dict[tuple[int, int | None, int | None], int] = {}
        self.by_doc: dict[int, list[tuple[int, int | None, int | None]]] = defaultdict(list)
        for d in session.scalars(select(Deliverable).where(Deliverable.project_id == project_id,
                                                           Deliverable.obsolete.is_(False))):
            if d.document_type_id not in self.docs:
                continue
            key = (d.document_type_id, d.equipment_id, d.module_id)
            self.deliverables[key] = d.id
            self.by_doc[d.document_type_id].append((d.id, d.equipment_id, d.module_id))
        self.delivered = set(session.scalars(select(FileDeliverable.deliverable_id).join(
            Deliverable, Deliverable.id == FileDeliverable.deliverable_id).where(
            Deliverable.project_id == project_id)))
        self.vendors = sorted({e.vendor for e in self.equipment.values() if e.vendor}, key=len,
                              reverse=True)
        # 未作废的传送单：编号 → id；应交项 → 传送单明细（新的在后）
        self.transmittals: dict[str, int] = {}
        self.items_by_deliverable: dict[int, list[tuple[int, int, bool]]] = defaultdict(list)
        for item, t in session.execute(
                select(TransmittalItem, Transmittal)
                .join(Transmittal, Transmittal.id == TransmittalItem.transmittal_id)
                .where(Transmittal.project_id == project_id, Transmittal.void.is_(False))
                .order_by(Transmittal.id, TransmittalItem.id)):
            self.transmittals[t.number] = t.id
            self.items_by_deliverable[item.deliverable_id].append(
                (t.id, item.id, item.review_code is not None))
        self.transmittal_numbers = {v: k for k, v in self.transmittals.items()}
        self.codes: list[ReviewCode] = codes_svc.list_codes(session, project_id)


# ---------------------------------------------------------------- 规则识别器


class RuleRecognizer:
    def __init__(self, index: ProjectIndex) -> None:
        self.ix = index

    # ------------------------------------------------------------ 入口
    def recognize(self, item: RecognizeInput) -> Recognition:
        stem, _ = naming.split_ext(item.filename)
        sources = [("filename", stem), ("folder", item.folder or ""),
                   ("description", item.description or "")]
        texts = [(where, norm(strip_noise(text))) for where, text in sources if text]
        # 带字母的设备编号（如 R101）可能被当成版本号去掉，按原文找
        raw_texts = [(where, norm(text)) for where, text in sources if text]
        vendor = item.vendor
        vendor_reason = None
        if not vendor:  # 文件夹或描述里写了供应商名
            for v in self.ix.vendors:
                hit = next((w for w, t in texts if w != "filename" and has_phrase(t, norm(v))), None)
                if hit:
                    vendor, vendor_reason = v, Reason("vendor", v, hit)
                    break

        candidates = self._platform_name(stem) or self._rules(texts, raw_texts, vendor)
        if vendor_reason:
            for c in candidates:
                c.reasons.append(vendor_reason)
        candidates.sort(key=lambda c: (-c.score, len(c.deliverable_ids), c.deliverable_ids))
        # 同一个位置只留分数最高的一条
        seen, unique = set(), []
        for c in candidates:
            key = tuple(sorted(c.deliverable_ids))
            if key not in seen:
                seen.add(key)
                unique.append(c)
        unique = unique[:8]
        # 并列第一（如同名设备没写编号）：分不出是哪一个，最多算"中"，让人选
        if len(unique) > 1 and unique[0].score - unique[1].score < 0.01:
            for c in unique:
                if unique[0].score - c.score < 0.01:
                    c.score = min(c.score, HIGH - 0.05)

        reply = self._client_reply(sources, unique[0] if unique else None)
        source = "client" if reply else ("vendor" if vendor else None)
        return Recognition(candidates=unique, source=source, client_reply=reply)

    # ------------------------------------------------------------ 平台命名的文件再传回来
    def _platform_name(self, stem: str) -> list[Candidate]:
        for doc in sorted(self.ix.docs.values(), key=lambda d: -len(d.no)):
            found = naming.platform_prefix(stem, doc.no)
            if not found:
                continue
            raw_tags, rest_text = found
            prefix = doc.no + ("." + "&".join(raw_tags) if raw_tags else "")
            reasons = [Reason("platform_name", prefix, "filename")]
            rest = norm(rest_text)
            if doc.per_equipment:
                tags = [t.casefold() for t in raw_tags]
                ids = [i for i, eq_id, _ in self.ix.by_doc[doc.id]
                       if eq_id and self.ix.equipment.get(eq_id)
                       and (self.ix.equipment[eq_id].tag_n in tags
                            or (not tags and self._eq_name_hit(self.ix.equipment[eq_id], rest)))]
                if ids:
                    return [Candidate(ids, 0.95, reasons)]
            elif doc.per_module:
                for i, _, module_id in self.ix.by_doc[doc.id]:
                    if module_id and has_phrase(rest, self.ix.modules[module_id][1]):
                        return [Candidate([i], 0.95, reasons)]
            else:
                whole = self.ix.deliverables.get((doc.id, None, None))
                if whole:
                    return [Candidate([whole], 0.95, reasons)]
        return []

    @staticmethod
    def _eq_name_hit(eq: _Eq, text: str) -> bool:
        return any(has_phrase(text, p) for p in eq.phrases)

    # ------------------------------------------------------------ 一般规则
    def _doc_hits(self, texts: list[tuple[str, str]]) -> dict[int, tuple[float, list[Reason]]]:
        hits: dict[int, tuple[float, list[Reason]]] = {}
        all_words = set().union(*(words(t) for _, t in texts)) if texts else set()
        for doc in self.ix.docs.values():
            score, reasons = 0.0, []
            for where, t in texts:
                if has_code(t, doc.no.casefold()):
                    score, reasons = max(score, 0.6), reasons + [Reason("submittal_no", doc.no, where)]
                    break
            best_phrase = None
            for phrase, kind, original in sorted(doc.phrases, key=lambda p: -len(p[0])):
                where = next((w for w, t in texts if has_phrase(t, phrase)), None)
                if where:
                    best_phrase = (kind, original, where)
                    break
            if best_phrase:
                kind, original, where = best_phrase
                weight = {"type_name": 0.5, "keyword": 0.5, "category": 0.2}[kind]
                score = max(score, weight) + (0.1 if score else 0)
                reasons.append(Reason(kind, original, where))
            elif doc.words and (common := doc.words & all_words):
                ratio = len(common) / len(doc.words)
                if ratio >= 0.5:
                    score = max(score, 0.35 * ratio)
                    reasons.append(Reason("type_words", " ".join(sorted(common)), "-"))
            if score:
                hits[doc.id] = (min(score, 0.65), reasons)
        return hits

    def _eq_hits(self, texts: list[tuple[str, str]], raw_texts: list[tuple[str, str]],
                 vendor: str | None) -> dict[int, tuple[float, Reason]]:
        hits: dict[int, tuple[float, Reason]] = {}
        vendor_n = norm(vendor) if vendor else None
        name_hits: dict[str, list[int]] = defaultdict(list)
        for eq in self.ix.equipment.values():
            if vendor_n and eq.vendor_n != vendor_n:
                continue
            if eq.tag_n:
                pool = texts if eq.tag_n.isdigit() else raw_texts
                where = next((w for w, t in pool if has_code(t, eq.tag_n)), None)
                if where:
                    hits[eq.id] = (0.4, Reason("tag", eq.tag, where))
                    continue
            for phrase in sorted(eq.phrases, key=len, reverse=True):
                where = next((w for w, t in texts if has_phrase(t, phrase)), None)
                if where:
                    name_hits[phrase].append(eq.id)
                    hits[eq.id] = (0.3, Reason("equipment_name", eq.phrases[phrase], where))
                    break
        # 长的名称命中时，短的（被它包含的）不算："mixer a" 命中就不要 "mixer"
        longer = sorted(name_hits, key=len, reverse=True)
        for i, short in enumerate(longer):
            if any(short != long_ and has_phrase(long_, short) for long_ in longer[:i]):
                for eq_id in name_hits[short]:
                    if hits.get(eq_id, (0, None))[1].kind == "equipment_name":
                        hits.pop(eq_id, None)
        # 同名设备多台都命中（没写编号）：每台分数打折
        for ids in name_hits.values():
            alive = [i for i in ids if i in hits and hits[i][1].kind == "equipment_name"]
            if len(alive) > 1:
                for i in alive:
                    hits[i] = (0.3 / len(alive) + 0.05, hits[i][1])
        return hits

    def _rules(self, texts: list[tuple[str, str]], raw_texts: list[tuple[str, str]],
               vendor: str | None) -> list[Candidate]:
        doc_hits = self._doc_hits(texts)
        eq_hits = self._eq_hits(texts, raw_texts, vendor)
        module_hits = {mid: next((w for w, t in texts if has_phrase(t, n)), None)
                       for mid, (_, n) in self.ix.modules.items()}
        module_hits = {k: v for k, v in module_hits.items() if v}
        out: list[Candidate] = []

        def bonus(ids: list[int]) -> tuple[float, list[Reason]]:
            if any(i not in self.ix.delivered for i in ids):
                return 0.05, [Reason("not_delivered", "", "-")]
            return 0.0, []

        # 没认出种类、认出了设备：这台设备的按设备种类都是候选（还没交的优先）
        docs = dict(doc_hits)
        if not docs and eq_hits:
            for doc in self.ix.docs.values():
                if doc.per_equipment:
                    docs[doc.id] = (0.0, [])

        for doc_id, (doc_score, doc_reasons) in docs.items():
            doc = self.ix.docs[doc_id]
            rows = self.ix.by_doc.get(doc_id, [])
            if doc.per_equipment:
                matched = [(i, eq_id) for i, eq_id, _ in rows if eq_id in eq_hits]
                tagged = [(i, e) for i, e in matched if eq_hits[e][1].kind == "tag"]
                if len(tagged) > 1:  # 写了几个设备编号：一个文件挂几台
                    ids = [i for i, _ in tagged]
                    b, br = bonus(ids)
                    out.append(Candidate(ids, doc_score + 0.4 + b,
                                         doc_reasons + [eq_hits[e][1] for _, e in tagged] + br))
                for i, eq_id in matched:
                    b, br = bonus([i])
                    out.append(Candidate([i], doc_score + eq_hits[eq_id][0] + b,
                                         doc_reasons + [eq_hits[eq_id][1]] + br))
                if not matched and len(rows) == 1 and doc_score:
                    out.append(Candidate([rows[0][0]], doc_score + 0.1, list(doc_reasons)))
            elif doc.per_module:
                matched = [(i, m) for i, _, m in rows if m in module_hits]
                for i, m in matched:
                    b, br = bonus([i])
                    out.append(Candidate([i], doc_score + 0.3 + b, doc_reasons + [
                        Reason("module", self.ix.modules[m][0], module_hits[m])] + br))
                if not matched and doc_score:
                    for i, _, _ in rows:
                        out.append(Candidate([i], doc_score * 0.6, list(doc_reasons)))
            elif doc_score:
                whole = self.ix.deliverables.get((doc_id, None, None))
                if whole:
                    b, br = bonus([whole])
                    out.append(Candidate([whole], doc_score + 0.25 + b, doc_reasons + br))
        for c in out:
            # 写了几个设备编号时，"一个文件挂几台"排在单台前面
            c.score = min(c.score, 0.9) + (0.02 if len(c.deliverable_ids) > 1 else 0)
        return [c for c in out if c.score > 0.1]

    # ------------------------------------------------------------ 客户退回
    def _client_reply(self, sources: list[tuple[str, str]], best: Candidate | None
                      ) -> ClientReply | None:
        raw = " ".join(t for _, t in sources)
        hint = next(((tid, no) for no, tid in self.ix.transmittals.items()
                     if no.casefold() in raw.casefold()), None)
        code = self._find_code(sources)
        if code is None:
            return None
        code_obj, code_text = code
        items: list[int] = []
        transmittal = hint
        if best:
            for did in best.deliverable_ids:
                rows = self.ix.items_by_deliverable.get(did, [])
                if hint:
                    rows = [r for r in rows if r[0] == hint[0]]
                else:  # 没写传送单号：这一项最近一张还没回复的
                    rows = [r for r in rows if not r[2]][-1:]
                for tid, item_id, _ in rows:
                    items.append(item_id)
                    if transmittal is None:
                        transmittal = (tid, self.ix.transmittal_numbers[tid])
        if not items and not hint:
            return None
        return ClientReply(code=code_obj.code, code_text=code_text,
                           transmittal_id=transmittal[0] if transmittal else None,
                           transmittal_no=transmittal[1] if transmittal else None, item_ids=items)

    def _find_code(self, sources: list[tuple[str, str]]) -> tuple[ReviewCode, str] | None:
        """审核代码：写明"Code 1 / Status B / 代码 2"，或出现代码的名称、别名（如 Approved as noted）。
        单独一个数字不算（太容易误认）。"""
        for _, text in sources:
            m = re.search(r"(?:code|status|代码|审核代码)\s*[:#：-]?\s*([A-Za-z0-9]{1,3})(?![A-Za-z0-9])",
                          text, re.IGNORECASE)
            if m:
                found = codes_svc.find_code(self.ix.codes, m.group(1))
                if found:
                    return found, m.group(0).strip()
        names = []
        for c in self.ix.codes:
            for name in {c.label_en, c.label_zh, *(c.aliases or [])}:
                if name and len(norm(name)) > 3:
                    names.append((norm(name), c, name))
        names.sort(key=lambda x: -len(x[0]))  # 长的优先："approved as noted" 先于 "approved"
        for _, text in sources:
            t = norm(text)
            for n, c, original in names:
                if has_phrase(t, n):
                    return c, original
        return None


def recognize_many(session: Session, project_id: int, items: list[RecognizeInput]
                   ) -> list[Recognition]:
    rec = RuleRecognizer(ProjectIndex(session, project_id))
    return [rec.recognize(i) for i in items]
