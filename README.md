# ATT&CK Analyst Assistant

> Hybrid-search RAG over MITRE ATT&CK that answers SOC questions with cited sources.
> **Result:** _(filled in after evaluation: recall@10 dense vs hybrid, faithfulness)_

## Results

| Config | recall@10 | MRR | nDCG@10 | Faithfulness | p95 latency |
|---|---|---|---|---|---|
| Dense only | – | – | – | – | – |
| BM25 only | – | – | – | – | – |
| Hybrid (RRF) | – | – | – | – | – |
| Hybrid + reranker | – | – | – | – | – |

_Demo video: (link)_

## Architecture

```mermaid
flowchart LR
    A[ATT&CK STIX bundle] --> B[Ingest<br/>techniques, groups, software,<br/>mitigations, detections]
    B --> C[Chunk]
    C --> D[Dense index<br/>pgvector]
    C --> E[Keyword index<br/>BM25]
    Q[Analyst question] --> D & E
    D & E --> F[Fusion RRF] --> G[Reranker] --> H[LLM answer<br/>with citations]
```

## Design decisions

See [docs/DECISIONS.md](docs/DECISIONS.md).

## What did not work

_(filled in as the project goes)_

## Run it

```bash
python -m src.ingest.attack     # step 1: download + build data/processed/*.jsonl
```

## Project log

Progress and numbers per step: [docs/LOG.md](docs/LOG.md). Step-by-step explanations: [docs/steps/](docs/steps/).
