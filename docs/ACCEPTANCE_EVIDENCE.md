# MVP acceptance evidence

This file deliberately records no course material, hashes, host details, or credentials. Complete it on the T480 after a real reviewed transcript is supplied through the private UI.

| Criterion | Evidence to record privately | Current repository state |
| --- | --- | --- |
| Reviewed transcript approved and ingested | Source ID, source/document hashes, timestamped run | Awaiting a real private transcript |
| Database readback | Source/document/chunk and ingest-run row counts | Migration and readback query supplied |
| Semantic retrieval | QA question, returned lesson/timestamp, score | Awaiting T480 model/database execution |
| QA pack | QA question IDs and PASS results | Test Lab implemented; needs real corpus pack |
| Unsupported question | Gap ID and OPEN status | Test Lab implemented |
| Course material absent from Git | `git status`, `.gitignore`, private upload path | Enforced by repository policy and ignore rules |

## Local automated checks

`pytest` verifies the reviewed Markdown contract, tamper detection, timestamped chunk creation, Ollama response/dimension handling, and the pgvector schema’s required controls. It intentionally uses no course transcript.
