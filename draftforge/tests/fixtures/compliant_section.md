# 1 Results

We evaluated the proposed pipeline on the held-out benchmark split and
observed consistent improvements over the baseline retrieval system across
every metric we tracked. The hybrid dense and sparse retrieval configuration
recovered more of the labeled relevant passages than either retrieval mode
used in isolation, confirming the ablation hypothesis proposed in the
related work section. Manual inspection of a random sample of runs
suggested the gains were concentrated in queries containing rare technical
terms, where the sparse BM25 component contributed most of the recovered
passages that the dense encoder alone missed [@doe2020].

![pipeline results chart](chart.png)

Fig. 1: Recall at five and ten across retrieval configurations.

Table 1: Recall at five and ten for each retrieval configuration.

| Configuration | Recall@5 | Recall@10 |
| --- | --- | --- |
| Dense only | 0.61 | 0.74 |
| Sparse only | 0.58 | 0.70 |
| Hybrid | 0.79 | 0.88 |

## 1.1 Metrics

Recall at five and mean reciprocal rank were computed against the labeled
query set described earlier, and both metrics improved monotonically as
retrieval breadth increased from five to twenty candidates before
plateauing beyond that point.
