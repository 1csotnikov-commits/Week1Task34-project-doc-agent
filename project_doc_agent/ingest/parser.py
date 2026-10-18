"""Диспетчер парсеров и построение корпуса документов."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from project_doc_agent.config import ProjectConfig
from project_doc_agent.ingest.common import ParseOutput
from project_doc_agent.ingest.docx_parser import parse_docx
from project_doc_agent.ingest.markdown_parser import parse_markdown
from project_doc_agent.ingest.text_parser import parse_txt
from project_doc_agent.models import DocumentBlock, ManualReviewItem, SourceDocument
from project_doc_agent.workspace import TEXT_ENCODINGS


@dataclass
class ParsedCorpus:
    documents: list[SourceDocument] = field(default_factory=list)
    blocks: list[DocumentBlock] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manual_review: list[ManualReviewItem] = field(default_factory=list)

    @property
    def block_map(self) -> dict[str, DocumentBlock]:
        return {block.block_id: block for block in self.blocks}

    @property
    def document_map(self) -> dict[str, SourceDocument]:
        return {document.document_id: document for document in self.documents}


def _read_text(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def parse_document(path: Path, document: SourceDocument, config: ProjectConfig) -> ParseOutput:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_docx(path, document.document_id, document.file)
    text, encoding = _read_text(path)
    if suffix == ".md":
        output = parse_markdown(text, document.document_id, document.file)
    elif suffix == ".txt":
        output = parse_txt(
            text,
            document.document_id,
            document.file,
            detect_headings=config.txt_detect_headings,
        )
    else:
        raise ValueError(f"Неподдерживаемое расширение: {suffix}")
    output.metadata["encoding"] = encoding
    if encoding == "utf-8-replace":
        output.warnings.append(f"Кодировка {document.file} определена с заменой символов")
        output.manual_review.append(
            ManualReviewItem(
                file=document.file,
                reason="Не удалось уверенно определить кодировку; возможны искажения",
                kind="unreadable",
            )
        )
    return output


def parse_corpus(source_dir: Path, documents: list[SourceDocument], config: ProjectConfig) -> ParsedCorpus:
    corpus = ParsedCorpus(documents=[])
    for document in documents:
        path = source_dir / document.file
        try:
            parsed = parse_document(path, document, config)
        except Exception as exc:  # noqa: BLE001 - один файл не должен ронять ingest
            reason = f"Файл {document.file} не разобран: {exc}"
            corpus.warnings.append(reason)
            corpus.manual_review.append(
                ManualReviewItem(file=document.file, reason=reason, kind="unreadable")
            )
            corpus.documents.append(document.model_copy(update={"warnings": [reason]}))
            continue
        document = document.model_copy(
            update={
                "title": parsed.title,
                "metadata": parsed.metadata,
                "block_count": len(parsed.blocks),
                "table_count": parsed.tables,
                "image_count": parsed.images,
                "warnings": parsed.warnings,
            }
        )
        corpus.documents.append(document)
        corpus.blocks.extend(parsed.blocks)
        corpus.warnings.extend(parsed.warnings)
        corpus.manual_review.extend(parsed.manual_review)
    return corpus
