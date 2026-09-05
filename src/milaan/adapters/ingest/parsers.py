"""CSV/XLSX row reading and upload hardening (TRD §2.4: file size cap, row-count cap,
MIME/extension validation, zip-bomb rejection).

Deliberately reads everything as `str` — never lets pandas or any library infer a numeric
dtype for money fields. Type conversion to `Money`/`Decimal` happens explicitly and only in
domain/ingest_transform.py, so there is exactly one place a float could ever sneak into the
money path, and it's guarded by the no-float pre-commit hook.
"""

from __future__ import annotations

import csv
import io
import zipfile

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 200_000
MAX_XLSX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_XLSX_COMPRESSION_RATIO = 100
ALLOWED_EXTENSIONS = ("csv", "xlsx")


class UploadValidationError(Exception):
    """Raised for any upload-hardening failure — size, extension, encoding, or zip-bomb."""


def _extension_of(filename: str) -> str:
    return filename.lower().rsplit(".", 1)[-1] if "." in filename else ""


def validate_upload(filename: str, content: bytes) -> None:
    if len(content) > MAX_FILE_BYTES:
        raise UploadValidationError(f"File exceeds size cap of {MAX_FILE_BYTES // (1024 * 1024)}MB")
    ext = _extension_of(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadValidationError(f"Unsupported file extension: .{ext or '?'}")
    if ext == "xlsx":
        _check_xlsx_zip_bomb(content)
    else:
        try:
            content[:8192].decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UploadValidationError("CSV file is not valid UTF-8 text") from exc


def _check_xlsx_zip_bomb(content: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise UploadValidationError("File is not a valid .xlsx (zip) archive") from exc
    infolist = zf.infolist()
    total_uncompressed = sum(i.file_size for i in infolist)
    total_compressed = sum(i.compress_size for i in infolist) or 1
    if total_uncompressed > MAX_XLSX_UNCOMPRESSED_BYTES:
        raise UploadValidationError("XLSX uncompressed size exceeds the safety cap")
    if total_uncompressed / total_compressed > MAX_XLSX_COMPRESSION_RATIO:
        raise UploadValidationError(
            "XLSX compression ratio is implausibly high (possible zip bomb)"
        )


def read_rows(filename: str, content: bytes) -> list[dict[str, str]]:
    """Returns rows as `dict[str, str]` keyed by the *raw* source header — mapping to
    canonical fields happens later, in the caller, once a `MappingResult` is available."""
    validate_upload(filename, content)
    ext = _extension_of(filename)
    rows = _read_csv_rows(content) if ext == "csv" else _read_xlsx_rows(content)
    if len(rows) > MAX_ROWS:
        raise UploadValidationError(f"Row count {len(rows)} exceeds cap of {MAX_ROWS}")
    return rows


def _read_csv_rows(content: bytes) -> list[dict[str, str]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    return [{k: (v if v is not None else "") for k, v in row.items()} for row in reader]


def _read_xlsx_rows(content: bytes) -> list[dict[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if ws is None:
        raise UploadValidationError("XLSX workbook has no active sheet")
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        return []
    headers = [str(h) if h is not None else "" for h in header_row]
    rows: list[dict[str, str]] = []
    for raw_row in rows_iter:
        row = {h: ("" if v is None else str(v)) for h, v in zip(headers, raw_row, strict=False)}
        rows.append(row)
    return rows
