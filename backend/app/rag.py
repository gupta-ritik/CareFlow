"""SOP retrieval.

Pipeline: markdown SOPs with front matter -> chunk per section -> metadata (id, version, department, expiry)
-> BM25 index -> department-filtered retrieval -> citations like "SOP-PH-02 v3, Escalation".

Expired and superseded versions are excluded. Retrieved text is data: chunks that look like instructions to the
agent are flagged and dropped. Swap `_rank` for a pgvector similarity query when embeddings are available; the
filters and citations stay the same.
"""
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from rank_bm25 import BM25Okapi

from .redaction import looks_like_injection

KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


@dataclass(frozen=True)
class Chunk:
    doc_id: str
    title: str
    department: str
    version: int
    effective: date
    expires: date
    section: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.doc_id} v{self.version}, {self.section}"


def parse_document(raw: str) -> list[Chunk]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError("SOP must start with front matter (id, title, department, version, effective, expires)")
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    chunks = []
    for section in re.split(r"^## ", m.group(2), flags=re.M)[1:]:
        heading, _, body = section.partition("\n")
        chunks.append(
            Chunk(
                doc_id=meta["id"],
                title=meta["title"],
                department=meta["department"],
                version=int(meta["version"]),
                effective=date.fromisoformat(meta["effective"]),
                expires=date.fromisoformat(meta["expires"]),
                section=heading.strip(),
                text=" ".join(body.split()),
            )
        )
    return chunks


class SopIndex:
    def __init__(self, chunks: list[Chunk], today: date | None = None):
        today = today or date.today()
        latest: dict[str, int] = {}
        for c in chunks:
            if c.effective <= today <= c.expires:
                latest[c.doc_id] = max(latest.get(c.doc_id, 0), c.version)
        self.flagged = [c for c in chunks if looks_like_injection(c.text)]
        self.chunks = [
            c for c in chunks
            if c.effective <= today <= c.expires and latest.get(c.doc_id) == c.version and c not in self.flagged
        ]
        self._bm25 = BM25Okapi([_tokens(f"{c.title} {c.section} {c.text}") for c in self.chunks]) if self.chunks else None

    def search(self, query: str, department: str | None = None, k: int = 2) -> list[Chunk]:
        if not self._bm25:
            return []
        scores = self._bm25.get_scores(_tokens(query))
        ranked = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        out = [self.chunks[i] for i in ranked if scores[i] > 0 and (department is None or self.chunks[i].department == department)]
        return out[:k]


_index: SopIndex | None = None


def load_index(extra: list[str] | None = None) -> SopIndex:
    chunks: list[Chunk] = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        chunks += parse_document(path.read_text())
    for raw in extra or []:
        chunks += parse_document(raw)
    return SopIndex(chunks)


def index() -> SopIndex:
    global _index
    if _index is None:
        _index = load_index()
    return _index


def ingest(raw: str) -> list[Chunk]:
    """Add an uploaded SOP (markdown with front matter). PDF/DOCX would be converted to this format first."""
    global _index
    new = parse_document(raw)
    current = index()
    _index = SopIndex(current.chunks + current.flagged + new)
    return new
