"""Read extracted SUKL files and cache searchable PDF text in SQLite."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import unicodedata


KINDS = ("PIL", "SPC", "CTCAE")
EXTRACTOR_VERSION = "pdftotext-layout-v1"


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c))


def read_csv(path: Path) -> list[dict[str, str]]:
    # SUKL DLP distributions use Windows-1250, semicolons, and significant leading zeros.
    with path.open(encoding="cp1250", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


@dataclass(frozen=True)
class Document:
    id: str
    kind: str
    path: Path

    @property
    def signature(self) -> str:
        stat = self.path.stat()
        value = (str(self.path.resolve()), stat.st_size, stat.st_mtime_ns, EXTRACTOR_VERSION)
        return hashlib.sha256(repr(value).encode()).hexdigest()

    def text_pages(self) -> list[str]:
        result = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(self.path), "-"],
            check=True, capture_output=True, timeout=120,
        )
        pages = result.stdout.decode("utf-8").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()  # pdftotext terminates the final page with a form feed.
        if not any(page.strip() for page in pages):
            raise ValueError(f"{self.id} has no text layer; OCR is required")
        return pages

    def render(self, page: int) -> bytes:
        with tempfile.TemporaryDirectory(prefix="sukl-page-") as directory:
            prefix = Path(directory) / "page"
            subprocess.run(
                ["pdftoppm", "-f", str(page), "-l", str(page), "-singlefile", "-scale-to",
                 "1600", "-png", str(self.path), str(prefix)],
                check=True, capture_output=True, timeout=120,
            )
            return prefix.with_suffix(".png").read_bytes()

def filter_empty_keys(d: dict):
    return {k: v for k, v in d.items() if v not in ("", None)}

class Catalog:
    def __init__(self, data_dir: Path, ctcae: Path, snapshot: str | None = None):
        data_dir = data_dir.resolve()
        candidates = sorted(data_dir.glob("DLP[0-9]*"))
        candidates = [p for p in candidates if p.is_dir()]
        if snapshot is None:
            if len(candidates) != 1:
                raise ValueError("Select --snapshot YYYYMMDD (exactly one extracted DLP directory required)")
            snapshot = candidates[0].name.removeprefix("DLP")
        if not re.fullmatch(r"\d{8}", snapshot):
            raise ValueError("snapshot must be YYYYMMDD")
        self.snapshot = snapshot
        root = data_dir / f"DLP{snapshot}"
        products = read_csv(root / "dlp_lecivepripravky.csv")
        self.products = {r["KOD_SUKL"]: r for r in products}
        self.links = {r["KOD_SUKL"]: filter_empty_keys(r) for r in read_csv(root / "dlp_nazvydokumentu.csv")}
        self.substances = {r["KOD_LATKY"]: filter_empty_keys(r) for r in read_csv(root / "dlp_latky.csv")}
        self.composition: dict[str, list[dict]] = {}
        for row in read_csv(root / "dlp_slozeni.csv"):
            if row["KOD_SUKL"] in (None, "", "0"):
                continue
            self.composition.setdefault(row["KOD_SUKL"], []).append(
                {**filter_empty_keys(row), "substance": self.substances.get(row["KOD_LATKY"], {})}
            )
        self.dictionaries = {}
        for name, column in (("atc", "ATC"), ("formy", "FORMA"), ("cesty", "CESTA"),
                             ("obaly", "OBAL"), ("vydej", "VYDEJ"), ("stavyreg", "REG")):
            path = root / f"dlp_{name}.csv"
            self.dictionaries[name] = {r[column]: r for r in read_csv(path)} if path.exists() else {}
        self.documents: dict[str, Document] = {}
        self.references: dict[str, list[str]] = {}
        self.unsupported: list[str] = []
        for kind in ("PIL", "SPC"):
            folder = data_dir / f"{kind}{snapshot}"
            if not folder.is_dir():
                raise ValueError(f"Missing extracted document folder: {folder}")
            for path in sorted(folder.iterdir()):
                if not path.is_file():
                    continue
                if path.suffix.lower() == ".pdf":
                    doc = Document(f"{kind}:{path.name}", kind, path)
                    self.documents[doc.id] = doc
                else:
                    self.unsupported.append(str(path))
        if not ctcae.is_file():
            raise ValueError(f"Missing CTCAE reference: {ctcae}")
        doc = Document("CTCAE:ctcae-v6.pdf", "CTCAE", ctcae.resolve())
        self.documents[doc.id] = doc
        for code, row in self.links.items():
            for kind in ("PIL", "SPC"):
                if row.get(kind):
                    self.references.setdefault(f"{kind}:{row[kind]}", []).append(code)
        self._search_rows: list[tuple[str, str]] | None = None

    def code(self, value: str) -> str:
        if not re.fullmatch(r"\d{1,7}", value):
            raise ValueError("Use a numeric SUKL code (1–7 digits)")
        code = value.zfill(7)
        if code not in self.products:
            raise ValueError(f"Unknown SUKL code {code}")
        return code

    def metadata(self, code: str) -> dict:
        code = self.code(code)
        return {"snapshot": self.snapshot, "product": self.products[code],
                "composition": self.composition.get(code, []),
                "documents": self.links.get(code, {}),
                "decoded": {name: self.dictionaries[name].get(self.products[code].get(column, ""))
                            for name, column in (("atc", "ATC_WHO"), ("formy", "FORMA"),
                                                 ("cesty", "CESTA"), ("obaly", "OBAL"),
                                                 ("vydej", "VYDEJ"), ("stavyreg", "REG"))}}

    def drug_output(self, code: str, extraction: dict) -> dict:
        metadata = self.metadata(code)
        atc = metadata['product'].get('ATC_WHO', '')
        classes = list(extraction['classes'])
        classes.extend(f'atc:{prefix}' for prefix in self.dictionaries['atc']
                       if atc.startswith(prefix) and len(prefix) in (1, 3, 4, 5, 7))
        classes.extend(f"substance:{row['KOD_LATKY']}" for row in metadata['composition']
                       if row.get('S') == 'L' and row['KOD_LATKY'].isdigit())
        return {**extraction, 'classes': sorted(set(classes)), 'metadata': metadata, 'schema_version': 3}

    def drug_documents(self, code: str) -> tuple[list[Document], list[str]]:
        row = self.links.get(self.code(code), {})
        available, missing = [], []
        for kind in ("PIL", "SPC"):
            doc_id = f"{kind}:{row.get(kind, '')}"
            if doc_id in self.documents:
                available.append(self.documents[doc_id])
            else:
                missing.append(f"{kind}: {row.get(kind) or 'no document in CSV'}")
        return available, missing

    def find_drugs(self, query: str, limit: int = 10) -> list[dict]:
        # Metadata lookup also works before a full text index has been built.
        words = re.findall(r"\w+", fold(query))
        if not words:
            return []
        if self._search_rows is None:
            self._search_rows = []
            for code, product in self.products.items():
                values = list(product.values())
                for component in self.composition.get(code, []):
                    values.extend(component["substance"].values())
                self._search_rows.append((code, fold(" ".join(values))))
        matches = []
        for code, haystack in self._search_rows:
            if all(word in haystack for word in words):
                product = self.products[code]
                docs, missing = self.drug_documents(code)
                matches.append({"code": code, "name": product["NAZEV"], "strength": product["SILA"],
                                "atc": product["ATC_WHO"], "composition": self.composition.get(code, []), "document_ids": [d.id for d in docs],
                                "missing": missing})
                if len(matches) >= max(1, min(limit, 20)):
                    break
        return matches


class Corpus:
    def __init__(self, catalog: Catalog, db_path: Path):
        self.catalog = catalog
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, signature TEXT NOT NULL, kind TEXT NOT NULL,
                pages INTEGER NOT NULL, sha256 TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pages (
                document_id TEXT NOT NULL, page INTEGER NOT NULL, text TEXT NOT NULL,
                PRIMARY KEY(document_id, page)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
                document_id UNINDEXED, kind UNINDEXED, page UNINDEXED, text,
                tokenize='unicode61 remove_diacritics 2'
            );
        """)
        # A cache may outlive a snapshot: purge absent/changed sources before searching it.
        with self.db:
            for row in self.db.execute("SELECT id, signature FROM documents").fetchall():
                source = catalog.documents.get(row["id"])
                if source is None or source.signature != row["signature"]:
                    self._remove(row["id"])

    def close(self) -> None:
        self.db.close()

    def _remove(self, document_id: str) -> None:
        for table in ("documents", "pages", "chunks"):
            column = "id" if table == "documents" else "document_id"
            self.db.execute(f"DELETE FROM {table} WHERE {column}=?", (document_id,))

    def ensure(self, document_id: str) -> bool:
        """Returns True on extraction, False on cache hit. Commits one PDF atomically."""
        doc = self.catalog.documents.get(document_id)
        if doc is None:
            raise ValueError(f"Unknown document ID {document_id}")
        row = self.db.execute("SELECT signature FROM documents WHERE id=?", (document_id,)).fetchone()
        if row and row[0] == doc.signature:
            return False
        pages = doc.text_pages()
        digest = hashlib.sha256("\f".join(pages).encode()).hexdigest()
        with self.db:
            self._remove(document_id)
            self.db.execute("INSERT INTO documents VALUES (?,?,?,?,?)",
                            (doc.id, doc.signature, doc.kind, len(pages), digest))
            for number, text in enumerate(pages, 1):
                self.db.execute("INSERT INTO pages VALUES (?,?,?)", (doc.id, number, text))
                # Bounded chunks retain page boundaries and a little overlap for table rows.
                for offset in range(0, len(text), 3000):
                    self.db.execute("INSERT INTO chunks VALUES (?,?,?,?)",
                                    (doc.id, doc.kind, number, text[offset:offset + 3400]))
        return True

    def pages(self, document_id: str) -> list[dict]:
        self.ensure(document_id)
        return [dict(r) for r in self.db.execute(
            "SELECT document_id, page, text FROM pages WHERE document_id=? ORDER BY page", (document_id,)
        )]

    def page(self, document_id: str, page: int) -> dict:
        self.ensure(document_id)
        row = self.db.execute("SELECT * FROM pages WHERE document_id=? AND page=?",
                              (document_id, page)).fetchone()
        if row is None:
            raise ValueError(f"No page {page} in {document_id}")
        return dict(row)

    def search(self, query: str, kind: str | None = None, document_id: str | None = None,
               limit: int = 6) -> list[dict]:
        words = re.findall(r"[^\W_]+", query, re.UNICODE)[:16]
        if not words:
            return []
        if kind is not None and kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        if document_id:
            self.ensure(document_id)
        # Escape FTS syntax entirely; model supplied operators never become SQL/FTS code.
        match = " AND ".join(f'"{w}"' for w in words)
        sql = "SELECT document_id, kind, page, text FROM chunks WHERE chunks MATCH ?"
        args: list = [match]
        if kind:
            sql += " AND kind=?"
            args.append(kind)
        if document_id:
            sql += " AND document_id=?"
            args.append(document_id)
        sql += " ORDER BY bm25(chunks) LIMIT ?"
        args.append(max(1, min(limit, 8)))
        return [dict(r) for r in self.db.execute(sql, args)]

    def status(self) -> dict:
        rows = self.db.execute("SELECT kind, count(*) n FROM documents GROUP BY kind").fetchall()
        return {"indexed": {r["kind"]: r["n"] for r in rows},
                "available": {k: sum(d.kind == k for d in self.catalog.documents.values()) for k in KINDS},
                "unsupported_files": len(self.catalog.unsupported),
                "note": "Global text search covers indexed PDFs only. Use find_drugs and read_pages for others."}



def matching_combinations(drug: dict, other: dict) -> list[dict]:
    """Candidate matches by shared ID; review effect for clinical conditions."""
    classes = set(other['classes'])
    return [row for row in drug['combinations'] if row['class_id'] in classes]
