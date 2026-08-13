from __future__ import annotations

import re

import streamlit as st

from options_learning_kb.config import Settings
from options_learning_kb.db import Database
from options_learning_kb.embeddings import OllamaEmbeddingProvider
from options_learning_kb.service import KnowledgeBaseService
from options_learning_kb.transcript import TranscriptContractError, parse_reviewed_transcript

st.set_page_config(page_title="Options Learning KB", page_icon="📚", layout="wide")


@st.cache_resource
def kb() -> KnowledgeBaseService:
    settings = Settings.from_env()
    settings.validate_runtime()
    return KnowledgeBaseService(
        Database(settings.database_url),
        OllamaEmbeddingProvider(settings.ollama_base_url, settings.embedding_model, settings.embedding_dimensions),
        settings.embedding_model,
        default_search_limit=settings.search_limit,
    )


def seconds_from_timestamp(value: str) -> int | None:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2})", value.strip())
    if not match:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    if minutes > 59 or seconds > 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


st.title("Options Learning KB")
st.caption(
    "Private learning/research retrieval. Results are cited passages, not trading advice, trade authorization, or proof of edge."
)

try:
    service = kb()
except Exception as error:
    st.error(f"The private KB is not configured: {error}")
    st.stop()

registry, ingest, search_tab, test_lab = st.tabs(["Source Registry", "Ingest Studio", "Search", "Test Lab"])

with registry:
    st.subheader("Register a reviewed transcript")
    st.caption(
        "Only reviewed Markdown from mp4-to-transcript is accepted. MP4/video files are intentionally unsupported."
    )
    with st.form("source_upload"):
        uploaded = st.file_uploader("Reviewed Markdown transcript", type=["md", "markdown", "txt"])
        owner = st.text_input("Owner")
        permission_basis = st.text_area("Permission basis", placeholder="Private learning copy authorised by…")
        citation_policy = st.text_input("Citation policy", value="Private learning/research only; do not redistribute.")
        submitted = st.form_submit_button("Register source")
    if submitted:
        if not uploaded or not owner.strip() or not permission_basis.strip():
            st.error("A reviewed Markdown file, owner, and permission basis are required.")
        else:
            try:
                markdown = uploaded.getvalue().decode("utf-8")
                transcript = parse_reviewed_transcript(markdown)
                source = service.register_source(
                    markdown, owner.strip(), permission_basis.strip(), citation_policy.strip()
                )
                st.success(f"Registered {transcript.lesson_title} as DRAFT. Approve it separately after review.")
            except UnicodeDecodeError:
                st.error("The transcript must be UTF-8 Markdown.")
            except (TranscriptContractError, ValueError) as error:
                st.error(str(error))
            except Exception as error:
                st.error(f"Registration failed: {error}")

    sources = service.list_sources()
    st.subheader("Registered sources")
    if sources:
        st.dataframe(sources, use_container_width=True, hide_index=True)
        source_by_label = {f"{row['lesson_title']} — {row['id']}": row for row in sources}
        selected = st.selectbox("Source to approve or disable", list(source_by_label), key="registry_action_source")
        action_columns = st.columns(2)
        if action_columns[0].button("Approve source", type="primary"):
            try:
                service.update_source_status(str(source_by_label[selected]["id"]), "APPROVED")
                st.rerun()
            except ValueError as error:
                st.error(str(error))
        if action_columns[1].button("Disable source"):
            try:
                service.update_source_status(str(source_by_label[selected]["id"]), "DISABLED")
                st.rerun()
            except ValueError as error:
                st.error(str(error))
    else:
        st.info("No source registered yet.")

with ingest:
    st.subheader("Ingest approved sources")
    sources = service.list_sources()
    approved = [source for source in sources if source["review_status"] == "APPROVED"]
    if not approved:
        st.warning("There are no approved sources to ingest.")
    else:
        options = {f"{row['lesson_title']} — {row['source_filename']}": row for row in approved}
        choice = st.selectbox("Approved source", list(options), key="ingest_source")
        source = options[choice]
        st.code(f"source SHA-256: {source['source_sha256']}\ntranscript SHA-256: {source['transcript_sha256']}")
        if st.button("Ingest / safely re-run", type="primary"):
            try:
                result = service.ingest_source(str(source["id"]))
                st.success(result.message)
                st.json(result.__dict__)
            except Exception as error:
                st.error(f"Ingest failed: {error}")
    st.subheader("Ingest runs")
    st.dataframe(service.list_ingest_runs(), use_container_width=True, hide_index=True)

with search_tab:
    st.subheader("Cited semantic retrieval")
    st.caption(
        "The service returns retrieved source text only. It does not produce a recommendation or an uncited answer."
    )
    sources = service.list_sources()
    approved = [source for source in sources if source["review_status"] == "APPROVED"]
    scope_labels = {f"{row['lesson_title']} — {row['id']}": str(row["id"]) for row in approved}
    question = st.text_input("Question", placeholder="What does the lesson say about assignment obligations?")
    selected_labels = st.multiselect("Limit to sources (optional)", list(scope_labels))
    if st.button("Search passages", type="primary"):
        try:
            results = service.search(question, [scope_labels[label] for label in selected_labels] or None)
            if not results:
                st.warning("No cited passage was retrieved. Create a gap in Test Lab instead of inferring an answer.")
            for item in results:
                st.markdown(f"**{item.citation}** · similarity `{item.similarity:.3f}`")
                st.code(item.passage, language=None)
        except Exception as error:
            st.error(f"Search failed: {error}")

with test_lab:
    st.subheader("Retrieval QA")
    sources = service.list_sources()
    source_options = {
        "No expected source": None,
        **{f"{row['lesson_title']} — {row['id']}": str(row["id"]) for row in sources},
    }
    with st.form("qa_question"):
        qa_question = st.text_input("Question")
        expected_label = st.selectbox("Expected source", list(source_options))
        expected_timestamp = st.text_input("Expected timestamp (HH:MM:SS, optional)")
        create_qa = st.form_submit_button("Add QA question")
    if create_qa:
        seconds = seconds_from_timestamp(expected_timestamp) if expected_timestamp else None
        if not qa_question.strip() or (expected_timestamp and seconds is None):
            st.error("Provide a question and use HH:MM:SS for an expected timestamp.")
        else:
            service.create_qa_question(
                qa_question.strip(), source_options[expected_label], seconds, expected_timestamp or None
            )
            st.rerun()
    qa_rows = service.list_qa()
    if qa_rows:
        st.dataframe(qa_rows, use_container_width=True, hide_index=True)
        qa_lookup = {f"{row['question']} — {row['id']}": str(row["id"]) for row in qa_rows}
        qa_to_run = st.selectbox("QA question to run", list(qa_lookup))
        if st.button("Run selected QA"):
            st.json(service.run_qa(qa_lookup[qa_to_run]))

    st.subheader("Unsupported-question gaps")
    with st.form("create_gap"):
        gap_question = st.text_input("Unsupported question")
        gap_reason = st.text_area(
            "Why it is unsupported", value="No approved transcript passage currently supports this question."
        )
        create_gap = st.form_submit_button("Create visible gap")
    if create_gap:
        if gap_question.strip() and gap_reason.strip():
            service.create_gap(gap_question.strip(), None, gap_reason.strip())
            st.success("Gap recorded. No answer was inferred.")
        else:
            st.error("Question and rationale are required.")
    st.dataframe(service.list_gaps(), use_container_width=True, hide_index=True)
