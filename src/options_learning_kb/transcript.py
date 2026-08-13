from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

import yaml

TIMESTAMP = re.compile(r"^\[(?P<label>\d{2}:\d{2}:\d{2})\]\s*(?P<text>.*)$")
FRONT_MATTER = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n?", re.DOTALL)
REQUIRED_FIELDS = ("source_filename", "source_sha256", "transcript_sha256", "lesson_title", "review_status")


class TranscriptContractError(ValueError):
    """The Markdown file is not a reviewed mp4-to-transcript artifact."""


@dataclass(frozen=True)
class TimestampedSegment:
    seconds: int
    label: str
    text: str


@dataclass(frozen=True)
class Transcript:
    markdown: str
    metadata: Mapping[str, str]
    segments: tuple[TimestampedSegment, ...]

    @property
    def lesson_title(self) -> str:
        return self.metadata["lesson_title"]

    @property
    def review_status(self) -> str:
        return self.metadata["review_status"].upper()

    @property
    def source_sha256(self) -> str:
        return self.metadata["source_sha256"].lower()

    @property
    def transcript_sha256(self) -> str:
        return self.metadata["transcript_sha256"].lower()

    @property
    def source_filename(self) -> str:
        return self.metadata["source_filename"]


@dataclass(frozen=True)
class ChunkDraft:
    ordinal: int
    timestamp_start_seconds: int
    timestamp_start_label: str
    timestamp_end_seconds: int | None
    timestamp_end_label: str | None
    passage: str
    chunk_sha256: str


def _metadata(markdown: str) -> tuple[dict[str, str], str]:
    match = FRONT_MATTER.match(markdown)
    if not match:
        raise TranscriptContractError("A reviewed transcript needs YAML-style front matter.")
    try:
        parsed = yaml.safe_load(match.group("body")) or {}
    except yaml.YAMLError as error:
        raise TranscriptContractError("Transcript front matter is not valid YAML.") from error
    if not isinstance(parsed, dict):
        raise TranscriptContractError("Transcript front matter must be a mapping.")
    values = {str(key).strip().lower(): str(value).strip() for key, value in parsed.items() if value is not None}
    missing = [field for field in REQUIRED_FIELDS if not values.get(field)]
    if missing:
        raise TranscriptContractError(f"Missing reviewed-transcript metadata: {', '.join(missing)}")
    for field in ("source_sha256", "transcript_sha256"):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", values[field]):
            raise TranscriptContractError(f"{field} must be a 64-character SHA-256 hex digest.")
    if values["review_status"].upper() not in {"DRAFT", "APPROVED", "DISABLED"}:
        raise TranscriptContractError("review_status must be DRAFT, APPROVED, or DISABLED.")
    return values, markdown[match.end() :]


def _seconds(label: str) -> int:
    hours, minutes, seconds = (int(part) for part in label.split(":"))
    if minutes > 59 or seconds > 59:
        raise TranscriptContractError(f"Invalid timestamp: {label}")
    return hours * 3600 + minutes * 60 + seconds


def canonical_transcript_sha256(markdown: str) -> str:
    """Hash the transcript with its declared digest line removed.

    This avoids the self-referential checksum problem while still detecting
    content changes. The uploader accepts either this canonical digest or a
    whole-file digest for compatibility with an upstream exporter.
    """
    without_digest = re.sub(r"(?mi)^transcript_sha256:\s*[^\n]*\n?", "", markdown)
    return sha256(without_digest.encode("utf-8")).hexdigest()


def parse_reviewed_transcript(markdown: str) -> Transcript:
    metadata, body = _metadata(markdown)
    if not markdown.strip():
        raise TranscriptContractError("Transcript is empty.")
    whole_file_digest = sha256(markdown.encode("utf-8")).hexdigest()
    expected_digest = metadata["transcript_sha256"].lower()
    if expected_digest not in {whole_file_digest, canonical_transcript_sha256(markdown)}:
        raise TranscriptContractError("transcript_sha256 does not match the uploaded Markdown artifact.")

    segments: list[TimestampedSegment] = []
    prior_seconds = -1
    for raw_line in body.splitlines():
        match = TIMESTAMP.match(raw_line.strip())
        if not match:
            continue
        text = match.group("text").strip()
        if text:
            seconds = _seconds(match.group("label"))
            if seconds < prior_seconds:
                raise TranscriptContractError("Transcript timestamps must be in non-decreasing order.")
            segments.append(TimestampedSegment(seconds, match.group("label"), text))
            prior_seconds = seconds
    if not segments:
        raise TranscriptContractError("Transcript must contain timestamped [HH:MM:SS] passages.")
    return Transcript(markdown=markdown, metadata=metadata, segments=tuple(segments))


def build_chunks(transcript: Transcript, maximum_characters: int = 1200) -> list[ChunkDraft]:
    """Keep timestamps attached while packing adjacent reviewed passages."""
    if maximum_characters < 200:
        raise ValueError("maximum_characters must be at least 200.")
    groups: list[list[TimestampedSegment]] = []
    current: list[TimestampedSegment] = []
    current_length = 0
    for segment in transcript.segments:
        rendered = f"[{segment.label}] {segment.text}"
        if current and current_length + len(rendered) + 1 > maximum_characters:
            groups.append(current)
            current, current_length = [], 0
        current.append(segment)
        current_length += len(rendered) + 1
    if current:
        groups.append(current)

    drafts: list[ChunkDraft] = []
    for ordinal, group in enumerate(groups):
        passage = "\n".join(f"[{segment.label}] {segment.text}" for segment in group)
        start, end = group[0], group[-1]
        digest_payload = f"{transcript.transcript_sha256}:{ordinal}:{passage}".encode()
        drafts.append(
            ChunkDraft(
                ordinal=ordinal,
                timestamp_start_seconds=start.seconds,
                timestamp_start_label=start.label,
                timestamp_end_seconds=end.seconds,
                timestamp_end_label=end.label,
                passage=passage,
                chunk_sha256=sha256(digest_payload).hexdigest(),
            )
        )
    return drafts
