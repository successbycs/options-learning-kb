from hashlib import sha256

import pytest

from options_learning_kb.transcript import (
    TranscriptContractError,
    build_chunks,
    canonical_transcript_sha256,
    parse_reviewed_transcript,
)


def reviewed_markdown(status: str = "APPROVED") -> str:
    template = """---
source_filename: lesson-01.mp4
source_sha256: {source_digest}
transcript_sha256: PLACEHOLDER
lesson_title: Assignment obligations
review_status: {status}
---
[00:00:10] A cash-secured put requires capital for potential assignment.
[00:01:15] Premium does not replace an underlying investment thesis.
[00:02:30] Keep a journal of entries, exits, costs, and outcomes.
"""
    with_placeholder = template.format(source_digest="a" * 64, status=status)
    digest = canonical_transcript_sha256(with_placeholder)
    return with_placeholder.replace("PLACEHOLDER", digest)


def test_parses_required_reviewed_contract_and_timestamps():
    transcript = parse_reviewed_transcript(reviewed_markdown())

    assert transcript.review_status == "APPROVED"
    assert transcript.lesson_title == "Assignment obligations"
    assert [segment.label for segment in transcript.segments] == ["00:00:10", "00:01:15", "00:02:30"]
    assert transcript.segments[1].seconds == 75


def test_rejects_tampered_transcript():
    with pytest.raises(TranscriptContractError, match="does not match"):
        parse_reviewed_transcript(reviewed_markdown() + "\n[00:03:00] Tampered.")


def test_chunks_are_timestamped_and_reproducible():
    transcript = parse_reviewed_transcript(reviewed_markdown())
    chunks = build_chunks(transcript, maximum_characters=200)

    assert len(chunks) == 2
    assert chunks[0].timestamp_start_label == "00:00:10"
    assert chunks[1].timestamp_start_label == "00:02:30"
    assert len(chunks[0].chunk_sha256) == 64
