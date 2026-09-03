"""Upload and execute a customer-defined document proof of work."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..contracts import (
    AgentBlueprintResult,
    CompanyConfig,
    DocumentInput,
    FileEntry,
    InterviewResult,
    ProofResult,
)
from .common import Conversation, StageValidationError, request_model


@dataclass(frozen=True)
class ProofStageOutput:
    result: ProofResult
    uploaded_files: list[FileEntry]


async def run_proof_of_work(
    conversation: Conversation,
    assessment_id: str,
    company: CompanyConfig,
    interview: InterviewResult,
    blueprint: AgentBlueprintResult,
    documents: list[DocumentInput],
) -> ProofStageOutput:
    if len(documents) != 3 or {document.format for document in documents} != {"pdf", "xlsx", "png"}:
        raise StageValidationError("the proof requires exactly one PDF, XLSX, and PNG")

    uploaded: list[FileEntry] = []
    for document in documents:
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", document.name)
        remote_path = (
            f"~/.agent37-gateway/workspace/project009/{assessment_id}/{safe_name}"
        )
        uploaded.append(
            await conversation.client.upload_file(
                conversation.instance_url,
                document.local_path,
                remote_path,
            )
        )

    context = {
        "workflow": interview.summary.model_dump(mode="json"),
        "blueprint": blueprint.model_dump(mode="json"),
        "customer_proof_goal": company.proof_goal,
        "documents": [
            {
                "document_name": document.name,
                "format": document.format,
                "remote_path": entry.path,
            }
            for document, entry in zip(documents, uploaded, strict=True)
        ],
        "required_fields": company.proof_fields,
    }
    prompt = f"""
Run a document proof of work using the three attached customer files. The proof goal
and fields were supplied by the customer. Do not assume these files are quotations or
that their subjects are vendors unless the supplied context says so. Use your own
terminal, Python libraries, text extraction, and vision capabilities.
Project009 has not parsed the documents for you.

Context:
{json.dumps(context, ensure_ascii=False, indent=2)}

Complete the six proof steps in order. Identify each document's subject, extract and
normalize every required field, compare the documents where comparison is meaningful,
and flag missing values, contradictions, or uncertainty. Produce an advisory conclusion
that directly answers the customer's proof goal and preserves human review. Every
supported value needs a source locator appropriate to its
format: `page N` for PDF, `Sheet!A1` for XLSX, or an `image region` description for
PNG. Unsupported values must be null, below full confidence, and review_required.
Return only JSON matching this schema:
{json.dumps(ProofResult.model_json_schema(), ensure_ascii=False)}
""".strip()
    result = await request_model(
        conversation,
        prompt,
        ProofResult,
        label="proof-initial",
        files=[entry.path for entry in uploaded],
    )

    audit_prompt = f"""
Audit the complete proof result you just produced against the attached source files.
Return the full corrected ProofResult JSON, not a patch.

Initial result to audit:
{result.model_dump_json(indent=2)}

Mandatory verification procedure:
1. Re-open the XLSX with Python/openpyxl and print every relevant non-empty cell.
   For every `Sheet!A1` citation, read that exact cell again and confirm its value
   supports the cited field. Cite a deliberately blank value cell for a missing field
   and mention the adjacent label in evidence. Never infer a row number visually.
2. Re-open the PDF and verify every cited value is actually on the named page.
3. Re-open the PNG and verify every cited image-region description or bounding box
   encloses the visible value.
4. Recalculate or cross-check values when the supplied fields and document content make
   that possible. Never invent a formula that is not supported by the workflow.
5. Ensure each required comparison row covers all three documents and
   that every comparison source agrees with its corresponding extraction source.

Keep unsupported values null and human-reviewable. Return only JSON matching:
{json.dumps(ProofResult.model_json_schema(), ensure_ascii=False)}
""".strip()
    result = await request_model(
        conversation,
        audit_prompt,
        ProofResult,
        label="proof-source-audit",
        files=[entry.path for entry in uploaded],
    )

    expected = {document.name: document.format for document in documents}
    actual = {extraction.document_name: extraction.document_format for extraction in result.extractions}
    if actual != expected:
        raise StageValidationError(
            f"proof document set did not match uploaded files: expected {expected}, got {actual}"
        )
    required_fields = set(context["required_fields"])
    for extraction in result.extractions:
        extraction_fields = {field.name for field in extraction.fields}
        if extraction_fields != required_fields:
            raise StageValidationError(
                f"{extraction.document_name} did not contain all required fields: "
                f"expected {sorted(required_fields)}, got {sorted(extraction_fields)}"
            )
    comparison_fields = {row.field for row in result.comparison_rows}
    if comparison_fields != required_fields:
        raise StageValidationError(
            "proof comparison rows did not match the customer-required fields: "
            f"expected {sorted(required_fields)}, got {sorted(comparison_fields)}"
        )
    return ProofStageOutput(result=result, uploaded_files=uploaded)
