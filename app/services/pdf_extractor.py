"""PDF Comment Extraction and Parsing Service."""

import hashlib
import io
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import pypdf


class PDFExtractor:
    """Extracts, parses, and separates pre-written comments from PDF documents."""

    # Regex patterns for item boundaries
    # Numbered patterns: "1. ", "1) ", "[1] ", "(1) ", "1 - "
    # Unified pattern for any item boundary: numbered ("1.", "1)", "[1]", "(1)", "1 -") or bulleted ("•", "-", "*", "—", "▪")
    ITEM_BOUNDARY_PATTERN = re.compile(
        r"^\s*(?:(?:\[\s*\d+\s*\]|\(\s*\d+\s*\)|\d+[\.\)\-:])|[\u2022\u2023\u25E6\u2043\u2219\*\-\—\–▪])\s+"
    )

    def extract_comments_from_pdf(
        self,
        pdf_input: Union[str, bytes, io.BytesIO],
        remove_duplicates: bool = True
    ) -> Dict[str, Any]:
        """
        Extracts individual comments from an uploaded PDF file or bytes.
        Returns a dict with extracted comments list, stats, and pdf hash.
        """
        pdf_bytes = self._get_bytes(pdf_input)
        pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

        # Read pages
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        total_pages = len(reader.pages)

        if total_pages == 0:
            raise ValueError("The uploaded PDF has 0 pages.")

        # Check if password protected
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("The PDF is password protected and cannot be read.")

        raw_text_pages: List[str] = []
        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text() or ""
                raw_text_pages.append(page_text)
            except Exception as e:
                raw_text_pages.append("")

        full_raw_text = "\n\n".join(raw_text_pages).strip()

        # Detect scanned/empty PDF
        if len(full_raw_text.replace("\n", "").replace(" ", "")) < 20:
            raise ValueError(
                "The uploaded PDF appears to be a scanned image or contains no readable digital text. "
                "Please upload a PDF containing searchable text."
            )

        # Parse individual comments
        extracted_comments = self._parse_comments(full_raw_text)

        if not extracted_comments:
            raise ValueError("Could not extract any valid comments from the PDF. Ensure comments are separated clearly.")

        total_extracted = len(extracted_comments)
        unique_comments = []
        seen = set()
        duplicates_count = 0

        for comment in extracted_comments:
            normalized = " ".join(comment.split()).strip()
            if not normalized:
                continue

            if remove_duplicates:
                # Compare lower-case for duplicate detection while preserving original text
                norm_key = normalized.lower()
                if norm_key in seen:
                    duplicates_count += 1
                    continue
                seen.add(norm_key)

            unique_comments.append(normalized)

        return {
            "comments": unique_comments,
            "total_extracted": total_extracted,
            "total_unique": len(unique_comments),
            "duplicates_removed": duplicates_count,
            "pdf_hash": pdf_hash,
            "total_pages": total_pages,
            "sample": unique_comments[:5] if unique_comments else [],
        }

    def _get_bytes(self, pdf_input: Union[str, bytes, io.BytesIO]) -> bytes:
        if isinstance(pdf_input, bytes):
            return pdf_input
        if isinstance(pdf_input, str):
            with open(pdf_input, "rb") as f:
                return f.read()
        if hasattr(pdf_input, "read"):
            if hasattr(pdf_input, "seek"):
                pdf_input.seek(0)
            data = pdf_input.read()
            if hasattr(pdf_input, "seek"):
                pdf_input.seek(0)
            return data
        raise ValueError("Invalid PDF input type.")

    def _parse_comments(self, text: str) -> List[str]:
        """
        Parses raw text into comments, identifying numbered, bulleted, or paragraph separation.
        Handles multiline comments by preserving text until the next comment boundary.
        """
        lines = [line.strip() for line in text.splitlines()]
        clean_lines = [l for l in lines if l]

        # 1. Check for item boundaries (numbered, bulleted, bracketed)
        boundary_indices = []
        for idx, line in enumerate(clean_lines):
            if self.ITEM_BOUNDARY_PATTERN.match(line):
                boundary_indices.append(idx)

        # If item boundaries detected, group lines into single comments
        if len(boundary_indices) >= 2:
            return self._group_by_indices(clean_lines, boundary_indices, strip_prefix_pattern=self.ITEM_BOUNDARY_PATTERN)

        # 2. Fallback: Double newlines / blank-line separated paragraphs
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
        if len(paragraphs) > 1:
            results = []
            for p in paragraphs:
                clean_p = self.ITEM_BOUNDARY_PATTERN.sub("", p)
                clean_p = " ".join(clean_p.split())
                if clean_p:
                    results.append(clean_p)
            return results

        # 3. Fallback: single non-empty line per comment
        return [self.ITEM_BOUNDARY_PATTERN.sub("", l).strip() for l in clean_lines if len(l) > 2]

    def _group_by_indices(
        self,
        lines: List[str],
        start_indices: List[int],
        strip_prefix_pattern: Optional[re.Pattern] = None
    ) -> List[str]:
        """Groups lines between boundary indices into single multiline comments."""
        comments: List[str] = []
        num_items = len(start_indices)

        for i in range(num_items):
            start = start_indices[i]
            end = start_indices[i + 1] if i + 1 < num_items else len(lines)
            chunk_lines = lines[start:end]

            if not chunk_lines:
                continue

            first_line = chunk_lines[0]
            if strip_prefix_pattern:
                first_line = strip_prefix_pattern.sub("", first_line)

            all_lines = [first_line] + chunk_lines[1:]
            joined_comment = " ".join(part.strip() for part in all_lines if part.strip())

            if joined_comment:
                comments.append(joined_comment)

        return comments


# Global extractor instance
pdf_extractor = PDFExtractor()
