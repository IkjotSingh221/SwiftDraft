This paper presents an agentic retrieval-augmented generation pipeline for
drafting long, format-compliant academic documents directly from a
student's own source material, spanning project reports, thesis chapters,
and paper drafts. The system ingests uploaded source documents through a
structure-aware parsing and chunking pipeline, embeds each chunk with both
dense and sparse representations, and stores them in a hybrid vector index
that supports metadata-filtered retrieval scoped to a planner-assigned
outline. A planning stage produces a hierarchical outline of chapters,
sections, and leaf subsections directly from the target format
specification, pausing for human approval before any drafting begins so
the student retains full control over structure and scope. Each leaf
section is then drafted independently through a corrective retrieval loop
that rewrites queries, grades retrieved chunks for relevance, retries when
grades are poor, and finally drafts prose that cites only bibliography keys
known to exist in the project's citation store, eliminating hallucinated
references by construction rather than by post-hoc filtering. A citation
verifier checks that every cited claim is actually supported by its
retrieved source chunk, flagging unsupported claims for redrafting with
specific, actionable feedback rather than a generic rejection. A
continuity editor reviews only the boundary paragraphs between adjacent
sections, keeping cross-section consistency bounded rather than requiring
any single agent to hold the entire document in context at once. Format
compliance -- word counts, required sections, heading depth, citation
marker form, and caption rules -- is checked entirely with deterministic
code against a machine-readable format specification, never by asking a
language model to judge its own compliance, and any violation is routed
back to the owning section's drafter as concrete feedback rather than
surfaced only at the very end of the run. We describe the full system
architecture, the format specification schema that drives both planning
and compliance checking, and an evaluation harness covering retrieval
quality, citation faithfulness, compliance pass rate, and cost, and report
results honestly including the failure modes we observed during
development and testing of the complete pipeline end to end.
