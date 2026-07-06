# Method

This section describes the overall approach used to build and evaluate the
retrieval pipeline, covering the modeling choices made for the drafting
subgraph and the rationale behind them.

## Approach

We used a corrective retrieval-augmented generation loop: a query rewrite
step, filtered hybrid retrieval, chunk relevance grading, and up to two
retries before drafting proceeds with the best available chunks.
