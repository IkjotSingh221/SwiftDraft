# Conclusion

This project set out to determine whether combining dense and sparse
retrieval signals would meaningfully improve the recall of relevant source
passages for a corrective retrieval-augmented drafting pipeline, and the
results reported in the previous section indicate that the answer is a
clear yes across every configuration we tested. The hybrid configuration
consistently outperformed both dense-only and sparse-only baselines,
particularly on queries containing rare technical terminology that the
dense encoder alone tended to under-retrieve. These findings have direct
implications for how the drafting pipeline should route retrieval queries
in future work, suggesting that a purely dense approach would leave
measurable recall on the table for exactly the kind of specialized academic
vocabulary this system is meant to handle well. Beyond the immediate
retrieval ablation, the broader project also demonstrated that a
programmatic, code-based compliance checker can catch structural and
citation-form problems long before a human reviewer would ever need to read
a full draft, which meaningfully reduces the review burden downstream and
keeps the whole pipeline auditable end to end. Future work should extend
this evaluation to a larger and more diverse benchmark, incorporate a
learned query rewriting step ahead of retrieval, and measure whether the
same hybrid advantage holds once citation faithfulness checking is folded
into the loop as an additional filtering stage before a section is
considered complete.
