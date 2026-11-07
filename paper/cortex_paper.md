# Temporal-Stratified Retrieval for AI Agent Memory: A Structure-First Alternative to Vector Search

**Andres Hartmann**
Libertatem

## Abstract

Large language model (LLM) agents operating in fast-paced domains require memory systems that prioritize recency and precision over semantic similarity. We present Cortex, a hierarchical-temporal retrieval engine that indexes markdown-based memory atoms using structured metadata (project, type, tag, temporal layer) without embeddings. Cortex stratifies atoms into hot, warm, and cold layers based on recency, applies a deterministic keyword scoring algorithm with temporal bonuses, and maintains live documentation through marker-based auto-refresh. On a reproducible benchmark of 500 synthetic atoms and 100 queries, Cortex achieves mean search latency of 1.6ms with 100% top-1 precision on tag-matched queries, with the hot layer containing the top result in 66% of cases. Deployed since March 2026 on a production system managing 470 atoms across 14 projects, the system requires zero external dependencies. We argue that for structured, high-velocity knowledge domains, temporal stratification with multi-dimensional indexing provides a practical, interpretable, and low-cost complement to dense vector retrieval.

## 1. Introduction

The emergence of LLM-based autonomous agents has created a new class of software systems that must maintain persistent memory across sessions, tasks, and time horizons. Unlike traditional information retrieval, where a user submits a query against a static corpus, agent memory is dynamic: new knowledge is created continuously, older knowledge becomes less relevant, and the agent's operational context shifts rapidly.

Current approaches to agent memory broadly fall into two categories. Full-context loading appends the entire memory store to the LLM's context window. While modern context windows have grown substantially (100K-1M tokens), loading an entire memory vault remains wasteful: most content is irrelevant to the current query, and token costs scale linearly with context size. Embedding-based retrieval (RAG) converts knowledge into dense vectors and retrieves by cosine similarity. This approach, rooted in techniques like BM25 (Robertson et al., 1995) and extended with learned dense representations (Karpukhin et al., 2020), scales well but introduces infrastructure requirements (an embedding model, a vector database) and a subtle misalignment: semantic similarity does not equal operational relevance. An atom about a deployment failure from 30 days ago may be semantically similar to today's query but operationally irrelevant compared to a decision made 2 hours ago.

We propose a third approach: structure-first retrieval with temporal stratification. Cortex indexes knowledge units (atoms) using explicit metadata rather than learned embeddings, scores them with a deterministic keyword algorithm inspired by classical term-matching approaches, and applies temporal bonuses that naturally prioritize recent context. The system requires no external dependencies, no API calls, and no vector database.

Our contribution is not a novel retrieval algorithm (the scoring function is a weighted keyword match with hand-tuned weights, conceptually similar to a simplified BM25 with a temporal decay factor) but rather an integrated system design that demonstrates how temporal stratification, structured metadata, and file-based storage can together provide a practical memory solution for LLM agents in operational domains. We report on both a reproducible synthetic benchmark and production deployment experience to characterize where this approach works and where it falls short.

## 2. Related Work

### 2.1 Agent Memory Architectures

MemGPT (Packer et al., 2023) introduced a virtual memory hierarchy for LLM agents, using an operating system metaphor with main memory and archival storage. While conceptually similar to our temporal layers, MemGPT uses embedding-based retrieval for its archival tier and requires the LLM to manage memory explicitly through function calls. Cortex externalizes memory management into a deterministic pipeline.

Park et al. (2023) demonstrated generative agents with a memory stream architecture where importance scoring, recency, and relevance jointly determine which memories are retrieved. Their recency weighting uses an exponential decay function. Cortex uses discrete temporal layers (hot/warm/cold) rather than continuous decay, trading granularity for interpretability and lower computational cost.

Shinn et al. (2023) introduced Reflexion, where agents store linguistic feedback from prior episodes in a persistent memory buffer. Their work highlights the importance of structured reflection in agent memory but does not address retrieval at scale.

LangChain and LlamaIndex provide memory abstractions for LLM applications, typically backed by vector stores (Pinecone, ChromaDB, FAISS). Mem0 and Zep offer managed memory services with automatic summarization and embedding-based retrieval. These systems optimize for semantic recall but do not model temporal decay or operational priority as first-class features. Gao et al. (2024) survey the RAG landscape comprehensively, noting that retrieval quality remains a bottleneck in production systems. Sumers et al. (2024) propose a cognitive architecture framework for language agents that situates memory alongside action, perception, and learning, highlighting the need for memory systems tailored to agent workflows rather than general-purpose search.

### 2.2 Term-Matching and Temporal Models in Information Retrieval

BM25 (Robertson et al., 1995) remains a strong baseline for keyword-based retrieval, using term frequency and inverse document frequency to rank documents. Cortex's scoring function is conceptually simpler than BM25: it uses fixed per-field weights rather than TF-IDF statistics, reflecting the small vault sizes (hundreds, not millions of documents) where corpus statistics provide limited signal. Recent work on learned sparse retrieval (Formal et al., 2022) bridges the gap between keyword and dense approaches, though at the cost of a training pipeline that Cortex avoids.

Time-aware retrieval has been studied extensively in web search (Li and Croft, 2003; Efron and Golovchinsky, 2011), where temporal relevance models weight documents by publication date. Berberich et al. (2010) proposed explicit time-aware ranking for web archives. Our approach differs in using discrete temporal layers rather than continuous decay functions, which provides interpretability and allows layer-specific retrieval policies.

### 2.3 Knowledge Management Systems

Obsidian, Notion, and similar tools use wikilink graphs and databases for knowledge navigation. Cortex is compatible with Obsidian (atoms are standard markdown with wikilinks) but adds programmatic indexing and retrieval that these tools lack. The Zettelkasten method (Ahrens, 2017) inspired the atom-based knowledge structure, where each atom is a self-contained unit of knowledge with explicit metadata and cross-references. Zhang et al. (2025) survey knowledge graph construction from LLMs, noting parallels between structured knowledge extraction and the kind of metadata-rich atoms Cortex maintains.

## 3. System Architecture

Cortex consists of nine modules operating on a vault of markdown atoms: a configuration loader, an index builder, a smart loader (query engine), an atom writer, a layer compressor, a MOC refresher, an auto-linker, a frontmatter migration tool, and a retrieval tracker.

### 3.1 Atoms

The fundamental unit of memory is an atom: a markdown file with YAML frontmatter containing structured metadata.

```yaml
---
id: 20260412_deploy_freeze_during_release
name: Deploy freeze during release windows
type: decision
project: ops
status: active
created: 2026-04-12
updated: 2026-04-12
tags: [deploy, risk, decision]
links: []
---
```

The frontmatter schema defines nine required fields (id, name, type, project, status, created, updated, tags, links) and three optional fields (description, pnl_impact, confidence). The type field supports 14 values covering the taxonomy of operational knowledge: rule, insight, incident, project, person, reference, decision, lesson, event, loss, win, concept, feedback, and user.

### 3.2 Indexing

The index builder scans every markdown file in the vault, parses frontmatter, extracts wikilinks and hashtags, and produces five JSON indexes:

1. **Manifest** (flat array): All atom metadata. Used as the primary data source for scoring via linear scan.
2. **By-project** (object): Atoms grouped by project name. Used for project-scoped filtering.
3. **By-type** (object): Atoms grouped by type. Enables type-specific retrieval (e.g., all rules).
4. **By-tag** (object): Atoms grouped by tag. Supports tag-based filtering.
5. **Graph** (adjacency list): Directed link graph from wikilinks and backtick references. Enables orphan detection and hub analysis.

The current search implementation performs a linear scan over the manifest for keyword scoring. The grouped indexes (by-project, by-type, by-tag) are used for structured filtering and could be leveraged for pre-filtering in future versions to reduce scan scope. Indexing is a single-pass walk over the vault filesystem. On a 500-atom vault, full reindexing completes in under 5 seconds.

### 3.3 Temporal Stratification

The layer compressor classifies each atom into one of three temporal layers based on the `updated` field:

- **Hot** (updated within 2 days): Full-priority atoms representing active work, recent decisions, and in-progress incidents.
- **Warm** (updated 2-7 days ago): Settled decisions and recent history. Still operationally relevant but no longer in active flux.
- **Cold** (updated more than 7 days ago): Background knowledge, archived findings, and historical context.

The thresholds (2 and 7 days) are configurable. The classification is based on `updated`, not `created`, so an old atom that is actively maintained stays hot.

In our production deployment (470 atoms), the distribution is approximately 6% hot, 15% warm, and 79% cold. In our synthetic benchmark (Section 4), configured with a similar distribution (10% hot, 20% warm, 70% cold), the hot layer contained the top result in 66% of queries, consistent with the recency bias inherent in operational work.

### 3.4 Scoring Algorithm

Given a query consisting of one or more keywords, the smart loader scores each atom in the manifest:

**Base scoring** (per keyword):
- Name substring match: +10.0
- Tag exact match: +8.0
- Project exact match: +5.0
- Description substring match: +4.0
- Path substring match: +3.0

The weights were set heuristically based on the intuition that an atom's name is the strongest signal of relevance, followed by explicit tags (which represent author-assigned semantics), then project membership, description, and finally file path. We did not tune these weights on held-out data; they were set once during initial development and have remained unchanged. Systematic weight optimization is left for future work.

**Multi-keyword bonus:** If all query keywords match (across any combination of name, description, path, tags, and project), the base score is multiplied by 1.5. This rewards precision: atoms that match every query term are favored over atoms that match only some.

**Temporal bonus** (applied only if base score > 0):
- Hot layer: +2.0
- Warm layer: +1.0
- Cold layer: +0.0

The temporal bonus is a tiebreaker, not a primary signal. It ensures that when two atoms have similar keyword relevance, the more recent one ranks higher.

**Status penalty:**
- Archived atoms: score multiplied by 0.3
- Superseded atoms: score multiplied by 0.5

The scoring algorithm is entirely deterministic: the same query on the same vault always produces the same ranking. Unlike BM25, it does not use corpus statistics (TF-IDF), which provides stability as the vault grows but sacrifices the ability to down-weight common terms automatically.

### 3.5 MOC Auto-Refresh

Map-of-Content (MOC) files serve as human-readable dashboards for each project domain. The MOC refresher keeps them current through marker-based injection:

```markdown
<!-- AUTO:section_name -->
...content replaced on each run...
<!-- /AUTO:section_name -->
```

The update engine uses atomic writes (temporary file plus rename) to prevent corruption. Content outside markers is never touched, preserving manual edits. The refresher follows a collect-compute-render-update pipeline and is designed to be extended with custom data collectors. The open-source release includes a vault-count collector; our production deployment extends it with database queries and service health checks.

### 3.6 Retrieval Tracker

Every search is logged to an append-only JSONL file with query terms, result paths, scores, temporal layers, and latency. Per-atom access statistics (count, first/last accessed) are maintained in a separate JSON file. This enables post-hoc precision analysis, dead atom detection, and the generation of the benchmark data reported in Section 4.

### 3.7 Cross-Reference Analysis

The auto-linker module discovers connections between atoms by converting backtick path references to wikilinks, detecting atom name mentions, and analyzing the link graph for orphans and hubs. In our production vault, 79% of atoms are orphans (no incoming or outgoing links), while the top three hubs account for 30% of all link connections. This hub-and-spoke pattern is typical of operational knowledge bases where a few index documents aggregate references to many domain-specific atoms.

## 4. Evaluation

### 4.1 Reproducible Benchmark

We provide a benchmark script (`benchmarks/run_benchmark.py`) that generates a synthetic vault, runs queries, and reports metrics. All results below are reproducible by running the script with the default random seed.

**Setup:** 500 synthetic atoms across 5 projects, with temporal distribution matching our production vault (10% hot, 20% warm, 70% cold). Atoms are assigned 1-4 tags from a pool of 14 domain terms. 100 queries are generated from single-tag and two-tag combinations.

**Latency** (keyword search only, manifest pre-loaded):

| Metric | Value |
|--------|-------|
| Mean | 1.62ms |
| Median | 1.69ms |
| P95 | 2.07ms |
| P99 | 2.83ms |

Manifest loading adds approximately 20-50ms depending on vault size and disk speed, for a total end-to-end latency of 22-52ms on a 500-atom vault.

**Precision** (top-1 result contains at least one expected tag):

| Metric | Value |
|--------|-------|
| Top-1 precision | 100/100 (100%) |

This high precision reflects the deterministic nature of keyword scoring on a vault with explicit tags: when a query term exactly matches a tag, the scoring function reliably surfaces the matching atom. Precision would be lower for queries using synonyms or paraphrases not present in atom metadata.

**Temporal layer distribution of top results:**

| Layer | Count | % |
|-------|-------|---|
| Hot | 66 | 66% |
| Warm | 28 | 28% |
| Cold | 6 | 6% |

The temporal bonus causes hot atoms to rank above warm and cold atoms when keyword scores are similar, confirming that temporal stratification achieves its design goal.

### 4.2 Production Deployment

Cortex has been deployed since March 2026 managing an operational knowledge base of 470 atoms across 14 projects. We report observational findings (not controlled measurements):

- The system has operated with zero downtime since deployment.
- Anecdotally, the hot and warm layers (21% of the vault) contain the relevant result for the majority of routine operational queries.
- The multi-keyword bonus was added after observing that single-keyword queries on common tags (e.g., "deploy") produced noisy results. The bonus improved perceived relevance.
- The MOC auto-refresh pipeline runs every 15 minutes in production with custom data collectors (database queries, service health checks) beyond what is included in the open-source release.

These observations are informal. A controlled evaluation with labeled relevance judgments and a representative query workload is planned for future work using the retrieval tracker (Section 3.6).

### 4.3 Comparison with Embedding-Based Retrieval

We did not conduct a head-to-head comparison with an embedding-based system on the same vault. Such a comparison is complicated by the fact that Cortex relies on explicit metadata (tags, project names) that embedding models would not have access to unless included in the document text. A fair comparison would require either (a) embedding the full atom text including frontmatter, or (b) using a hybrid approach. We leave this comparison for future work.

We note qualitatively that Cortex's advantage is not primarily speed (local embedding models can achieve similar latency) but rather the elimination of infrastructure and the interpretability of deterministic scoring.

## 5. Discussion

### 5.1 When Structure-First Retrieval Works

Cortex is designed for a specific class of memory systems: structured, high-velocity, domain-specific knowledge bases where:

- Atoms have strong metadata (project, type, tags) that can be indexed.
- Recency correlates with relevance.
- The vocabulary is bounded (domain-specific terms recur frequently).
- The vault is small enough for linear scan (under 10,000 atoms).

In these conditions, keyword scoring with temporal bonuses provides a practical alternative to embedding-based retrieval, with advantages in latency, cost, interpretability, and infrastructure simplicity. We do not claim superior precision in general; the systems address different trade-off points.

### 5.2 Limitations

Cortex is not suitable for:

- **Cross-domain semantic search.** Without embeddings, it cannot match semantically related but lexically different queries (e.g., "revenue" and "sales"). This is mitigated by explicit tagging but not fully solved.
- **Large-scale vaults.** Linear scan of the manifest becomes slow above approximately 10,000 atoms. The indexes could be extended with inverted indexes or B-trees for larger vaults.
- **Unstructured notes.** Atoms without frontmatter metadata are poorly served by keyword scoring. The migration tool addresses this by inferring metadata from content, but the quality of inference is limited.
- **Weight sensitivity.** The scoring weights are hand-tuned and have not been validated on diverse domains. Different operational contexts may benefit from different weight configurations.

### 5.3 Future Work

- **Hybrid retrieval.** Combine keyword scoring with lightweight local embeddings (e.g., sentence-transformers) for a two-stage pipeline: keyword pre-filter followed by semantic re-ranking.
- **Weight optimization.** Use the retrieval tracker's access logs to optimize scoring weights based on actual usage patterns.
- **Automatic decay.** Replace fixed temporal thresholds with learned decay functions that adapt to per-project velocity.
- **Graph-aware scoring.** Incorporate link centrality (PageRank-like) as a scoring signal, boosting atoms that are well-connected in the knowledge graph.
- **Formal evaluation.** Conduct a controlled precision/recall study with labeled relevance judgments on the production vault.

## 6. Conclusion

We presented Cortex, a structure-first retrieval engine for AI agent memory that achieves practical performance through temporal stratification and deterministic keyword scoring, without requiring embeddings, vector databases, or external dependencies. On a reproducible benchmark of 500 atoms and 100 queries, the system achieves 1.6ms mean search latency and 100% top-1 precision on tag-matched queries. Deployed on a production system managing 470 atoms across 14 projects, the system demonstrates that for structured, high-velocity knowledge domains, explicit metadata and temporal layering provide a practical and interpretable complement to dense vector retrieval.

The system is open source at https://github.com/vocatus23/Cortex under the MIT license. The benchmark is included in the repository and can be reproduced with `python3 benchmarks/run_benchmark.py`.

## References

Ahrens, S. (2017). How to Take Smart Notes: One Simple Technique to Boost Writing, Learning and Thinking. Sonderedition.

Berberich, K., Bedathur, S., Neumann, T., & Weikum, G. (2010). A time machine for text search. In Proceedings of the 33rd international ACM SIGIR conference on Research and development in Information Retrieval.

Efron, M., & Golovchinsky, G. (2011). Estimation methods for ranking recent information. In Proceedings of the 34th international ACM SIGIR conference on Research and development in Information Retrieval.

Formal, T., Lassance, C., Piwowarski, B., & Clinchant, S. (2022). From Distillation to Hard Negative Sampling: Making Sparse Neural IR Models More Effective. In Proceedings of the 45th international ACM SIGIR conference on Research and development in Information Retrieval.

Gao, Y., Xiong, Y., Gao, X., Jia, K., Pan, J., Bi, Y., Dai, Y., Sun, J., Wang, M., & Wang, H. (2024). Retrieval-Augmented Generation for Large Language Models: A Survey. arXiv preprint arXiv:2312.10997.

Karpukhin, V., Oguz, B., Min, S., Lewis, P., Wu, L., Edunov, S., Chen, D., & Yih, W. (2020). Dense Passage Retrieval for Open-Domain Question Answering. In Proceedings of the 2020 Conference on Empirical Methods in Natural Language Processing (EMNLP).

Li, X., & Croft, W.B. (2003). Time-based language models. In Proceedings of the 12th international conference on Information and knowledge management.

Packer, C., Wooders, S., Lin, K., Fang, V., Patil, S.G., Stoica, I., & Gonzalez, J.E. (2023). MemGPT: Towards LLMs as Operating Systems. arXiv preprint arXiv:2310.08560.

Park, J.S., O'Brien, J.C., Cai, C.J., Morris, M.R., Liang, P., & Bernstein, M.S. (2023). Generative Agents: Interactive Simulacra of Human Behavior. In Proceedings of the 36th Annual ACM Symposium on User Interface Software and Technology (UIST '23).

Robertson, S.E., Walker, S., Jones, S., Hancock-Beaulieu, M., & Gatford, M. (1995). Okapi at TREC-3. In Proceedings of the Third Text REtrieval Conference (TREC-3).

Shinn, N., Cassano, F., Gopinath, A., Narasimhan, K., & Yao, S. (2023). Reflexion: Language Agents with Verbal Reinforcement Learning. In Advances in Neural Information Processing Systems (NeurIPS 2023).

Sumers, T.R., Yao, S., Narasimhan, K., & Griffiths, T.L. (2024). Cognitive Architectures for Language Agents. Transactions on Machine Learning Research (TMLR).

Zhang, J., Chen, B., Zhang, L., Ke, X., & Ding, H. (2025). A Comprehensive Survey on Automatic Knowledge Graph Construction. ACM Computing Surveys.
