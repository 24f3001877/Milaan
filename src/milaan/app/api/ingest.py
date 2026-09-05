"""Ingest and schema-mapping routes (TRD §2.5)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from milaan.adapters.ingest.parsers import UploadValidationError
from milaan.adapters.ingest.service import preview_file
from milaan.app.deps import get_db, require_bearer_token

router = APIRouter(
    prefix="/api/v1/ingest", tags=["ingest"], dependencies=[Depends(require_bearer_token)]
)


class MappingConfirmRequest(BaseModel):
    fingerprint: str
    source_type: str
    mapping: dict[str, str]


@router.post("/preview")
async def ingest_preview(
    source_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    try:
        preview = preview_file(source_type, file.filename or "upload", content)
    except UploadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    upload_id = uuid.uuid4()
    upload_dir = Path("data/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    (upload_dir / str(upload_id)).write_bytes(content)
    (upload_dir / f"{upload_id}.json").write_text(
        json.dumps(
            {
                "filename": file.filename or "upload.csv",
                "source_type": source_type,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
        + "\n",
    )

    cached = db.execute(
        text(
            "SELECT method, confirmed_by_human FROM schema_mapping "
            "WHERE source_type = :st AND header_fingerprint = :fp"
        ),
        {"st": source_type, "fp": preview.header_fingerprint},
    ).fetchone()
    mapping_method = "cached" if cached else preview.method

    return {
        "file_id": str(upload_id),
        "filename": file.filename or "upload.csv",
        "header_fingerprint": preview.header_fingerprint,
        "mapping": preview.mapping,
        "field_confidence": preview.field_confidence,
        "overall_confidence": preview.overall_confidence,
        "method": mapping_method,
        # "cached" alone hides where the cached mapping came from, so the UI could not tell a
        # remembered deterministic mapping from a remembered model one and had to assume the
        # worse case. Both columns are already selected above; surfacing them costs nothing.
        "cached_from_method": cached.method if cached else None,
        "confirmed_by_human": bool(cached.confirmed_by_human) if cached else False,
        "unmapped_required": preview.unmapped_required,
        "sample_rows": preview.sample_rows,
        "total_rows": preview.total_rows,
        "blocking": preview.overall_confidence < 0.85 and bool(preview.unmapped_required),
    }


@router.post("/mapping/confirm", status_code=201)
def confirm_mapping(body: MappingConfirmRequest, db: Session = Depends(get_db)):
    from milaan.domain.schema_fields import FIELDS_BY_SOURCE

    invalid = set(body.mapping.values()) - set(FIELDS_BY_SOURCE.get(body.source_type, ()))
    if invalid:
        raise HTTPException(status_code=422, detail=f"unknown canonical fields: {sorted(invalid)}")

    mapping_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO schema_mapping (id, source_type, header_fingerprint, mapping, "
            "field_confidence, method, confirmed_by_human, created_at) VALUES "
            "(:id, :st, :fp, CAST(:mapping AS JSONB), CAST(:conf AS JSONB), 'human', true, now()) "
            "ON CONFLICT (source_type, header_fingerprint) DO UPDATE SET "
            "mapping = EXCLUDED.mapping, confirmed_by_human = true"
        ),
        {
            "id": str(mapping_id),
            "st": body.source_type,
            "fp": body.fingerprint,
            "mapping": json.dumps(body.mapping),
            "conf": json.dumps(dict.fromkeys(body.mapping, 1.0)),
        },
    )
    db.commit()
    return {"id": str(mapping_id), "status": "confirmed"}


@router.get("/mappings")
def list_mappings(db: Session = Depends(get_db)):
    rows = db.execute(
        text(
            "SELECT id, source_type, header_fingerprint, method, confirmed_by_human, created_at "
            "FROM schema_mapping ORDER BY created_at DESC"
        )
    ).fetchall()
    return [
        {
            "id": str(r.id),
            "source_type": r.source_type,
            "header_fingerprint": r.header_fingerprint,
            "method": r.method,
            "confirmed_by_human": r.confirmed_by_human,
            "created_at": r.created_at.isoformat()
            if isinstance(r.created_at, datetime)
            else r.created_at,
        }
        for r in rows
    ]
