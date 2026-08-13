import re

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


def test_yaml_front_matter_is_safe_and_timestamps_must_be_ordered():
    markdown = reviewed_markdown().replace(
        "lesson_title: Assignment obligations",
        'lesson_title: "Assignment: obligations"\ncourse_title: [private, reviewed]',
    )
    digest = canonical_transcript_sha256(markdown)
    markdown = re.sub(r"transcript_sha256: [0-9a-f]{64}", f"transcript_sha256: {digest}", markdown)
    transcript = parse_reviewed_transcript(markdown)
    assert transcript.lesson_title == "Assignment: obligations"

    unordered = reviewed_markdown().replace("[00:01:15]", "[00:00:05]")
    unordered_digest = canonical_transcript_sha256(unordered)
    unordered = re.sub(
        r"transcript_sha256: [0-9a-f]{64}",
        f"transcript_sha256: {unordered_digest}",
        unordered,
    )
    with pytest.raises(TranscriptContractError, match="non-decreasing"):
        parse_reviewed_transcript(unordered)
