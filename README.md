# Indian Court Case Law Intelligence Platform
### Product Requirements Document (PRD) + Technical Requirements Document (TRD)
**Version:** 1.0 | **Status:** Draft | **Last Updated:** 2026-06-16

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Product Vision & Goals](#2-product-vision--goals)
3. [Phase Roadmap](#3-phase-roadmap)
4. [Phase 1 — Data Acquisition (Current)](#4-phase-1--data-acquisition-current)
5. [Data Schema — Case Metadata](#5-data-schema--case-metadata)
6. [Technical Architecture](#6-technical-architecture)
7. [PDF → Markdown Conversion Pipeline](#7-pdf--markdown-conversion-pipeline)
8. [Product Features (Post-Data)](#8-product-features-post-data)
9. [API Design](#9-api-design)
10. [Non-Functional Requirements](#10-non-functional-requirements)
11. [Success Metrics](#11-success-metrics)
12. [Open Decisions](#12-open-decisions)

---

## 1. Executive Summary

We are building a **single unified platform** that aggregates case laws from all Indian courts — starting with the Supreme Court of India — and makes them searchable, downloadable, and AI-processable via a clean REST API.

The long-term vision is a legal intelligence layer that enables:
- Lawyers to find the most relevant precedents for their current matter
- AI-assisted drafting of grounds for appeal and written submissions
- A probability-based "Winning Score" predictor trained on historical case outcomes

**Phase 1 is exclusively about data acquisition** — scraping, downloading, structuring, and storing every case law PDF from the Supreme Court of India (~30 lakh / 3 million records), then converting them into clean text format for downstream ML training.

---

## 2. Product Vision & Goals

### Vision
A single, authoritative, machine-readable repository of all Indian court decisions — structured, searchable, and accessible via API — that powers the next generation of legal AI tools in India.

### Primary Users
| User | Need |
|------|------|
| Lawyers / Advocates | Find relevant precedents fast; draft better submissions |
| Law firms | Automate case research; assess litigation risk |
| Legal AI developers | Access clean, structured Indian case law data via API |
| Litigants (self-represented) | Understand relevant decisions in plain language |

### What This Is NOT
- Not a legal advice platform
- Not a court filing system
- Not a replacement for a lawyer
- Not a curated editorial platform — it is a complete, unfiltered corpus

---

## 3. Phase Roadmap

```
Phase 1 (NOW)          Phase 2                 Phase 3
─────────────          ─────────               ─────────
Data Acquisition   →   Search & API        →   AI Features
─────────────          ─────────               ─────────
• Scrape SCI PDFs      • Authenticated API     • Relevant Case Finder
• Download to S3       • Full-text search      • AI Drafting Assistant
• Extract metadata     • Filter by court,      • Winning Score Predictor
• Convert PDF → MD       date, judge, party    • Plain-language summaries
• Build training       • Download endpoint
  corpus               • Rate limiting
                       • Developer docs
```

| Phase | Courts Covered | Target Timeline |
|-------|---------------|-----------------|
| Phase 1 | Supreme Court of India | Current |
| Phase 2 | High Courts (all 25) | Post Phase 1 |
| Phase 3 | District / Sessions Courts | Future |
| Phase 4 | Tribunals (NCLT, ITAT, NGT, etc.) | Future |

---

## 4. Phase 1 — Data Acquisition (Current)

### Source
**Supreme Court of India — VerdictFinder Portal**
- URL: `https://verdictfinder.sci.gov.in/elk_frontend/`
- Total records: ~30,74,569 (as of June 2026)
- Record type: Judgments and Orders

### What We Collect Per Case

For every case we collect **two artefacts**:

#### Artefact 1 — PDF File
- The original judgment/order document as-is from the portal
- Stored in object storage (S3 or compatible)
- Naming convention: `{court_code}/{year}/{diary_no}_{case_no}.pdf`

#### Artefact 2 — Structured Metadata (JSON)
See Section 5 for full schema.

#### Artefact 3 — Markdown Text File
- PDF converted to clean Unicode Markdown
- UTF-8 encoded, Unix line endings (`\n`)
- Stored alongside the PDF in object storage
- Used as training corpus for ML models
- Naming convention: `{court_code}/{year}/{diary_no}_{case_no}.md`

### Current Scraper Summary
- **Stack:** Python 3.10+ · Playwright · Chromium
- **Location:** `scraper/main.py`
- **Behaviour:** Human-mimicking (Gaussian delays, curved mouse movement, persistent browser profile)
- **CAPTCHA:** Manual solve on first visit; subsequent runs reuse session cookies
- **PDF Capture:** Network-level interception — catches PDF bytes regardless of UI rendering pattern (modal, new tab, or download)

---

## 5. Data Schema — Case Metadata

Every case produces one JSON metadata file with the following fields.

```json
{
  "court":                "Supreme Court of India",
  "court_code":           "SCI",
  "source_url":           "https://verdictfinder.sci.gov.in/...",

  "diary_no":             "23971/2026",
  "diary_date":           "2026-04-20",
  "diary_time":           "14:47",
  "diary_section":        "I-B",

  "case_number":          "SLP(C) No. 23971/2026",
  "case_type":            "SLP(C)",
  "cnr_number":           "SCSL012345672026",

  "filing_date":          "2026-04-20",
  "verified_on":          "2026-05-14",

  "last_listed_on":       "2026-06-15",
  "last_listed_bench":    [
    "HON'BLE MR. JUSTICE JOYMALYA BAGCHI",
    "HON'BLE MR. JUSTICE VIPUL M. PANCHOLI"
  ],

  "status":               "Pending",
  "stage":                "Motion Hearing [FRESH (FOR ADMISSION) - CIVIL CASES]",
  "list_after_weeks":     4,
  "stage_order_date":     "2026-06-15",
  "disposal_type":        "Admitted",

  "category_code":        "1601",
  "category_description": "Direct Taxation : Income Tax Act, 1961 ...",

  "petitioners": [
    { "name": "DEPUTY COMMISSIONER OF INCOME TAX CIRCLE 22(2)", "order": 1 }
  ],
  "respondents": [
    { "name": "SWAROVSKI INDIA PRIVATE LIMITED", "order": 1 }
  ],
  "petitioner_advocates": [
    { "name": "SUDARSHAN LAMBA" }
  ],
  "respondent_advocates": [],

  "pdf_file":             "s3://bucket/SCI/2026/23971_2026_SLP_C.pdf",
  "md_file":              "s3://bucket/SCI/2026/23971_2026_SLP_C.md",
  "pdf_size_bytes":       48200,
  "pdf_pages":            1,

  "scraped_at":           "2026-06-16T10:32:00Z",
  "scraper_version":      "1.0.0",
  "library_version":      "v1.0"
}
```

---

## 6. Technical Architecture

### Phase 1 Architecture (Data Acquisition)

```
┌─────────────────────────────────────────────────────────────┐
│                        SCRAPER LAYER                        │
│  Python + Playwright  ·  Persistent browser profile         │
│  Human-mimicking delays  ·  Network-level PDF interception  │
└───────────────────────────┬─────────────────────────────────┘
                            │  PDF bytes + metadata
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                     OBJECT STORAGE (S3)                     │
│  /SCI/{year}/{diary_no}.pdf                                 │
│  /SCI/{year}/{diary_no}.json   ← structured metadata        │
│  /SCI/{year}/{diary_no}.md     ← converted markdown text    │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                   PDF → MARKDOWN PIPELINE                   │
│  pdfplumber / pymupdf  →  clean text  →  .md file           │
└───────────────────────────┬─────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    ML TRAINING CORPUS                       │
│  All .md files = training dataset for legal LLM fine-tuning │
└─────────────────────────────────────────────────────────────┘
```

### Phase 2 Architecture (API Layer)

```
Client (Lawyer / App)
        │  POST /auth/token
        ▼
┌──────────────┐     ┌─────────────────────────────────────┐
│   Auth API   │────▶│         PostgreSQL                  │
│  (JWT/Keys)  │     │  users · api_keys · usage_events    │
└──────────────┘     └─────────────────────────────────────┘
        │
        │  GET /cases/search?query=...
        ▼
┌──────────────────────────────────────────────────────────┐
│              Search API (Elasticsearch / OpenSearch)      │
│  Full-text index over all case MD files + metadata        │
└──────────────────────────────────────────────────────────┘
        │
        │  GET /cases/{id}/download
        ▼
┌──────────────────────────────────────────────────────────┐
│                Pre-signed S3 URL (PDF / MD)               │
└──────────────────────────────────────────────────────────┘
```

### Storage Structure in S3

```
s3://indian-case-laws/
├── SCI/                          ← Supreme Court of India
│   ├── 2024/
│   │   ├── 23971_2024_SLP_C.pdf
│   │   ├── 23971_2024_SLP_C.md
│   │   └── 23971_2024_SLP_C.json
│   └── 2025/
├── HCB/                          ← High Court Bombay (Phase 2)
├── HCD/                          ← High Court Delhi (Phase 2)
└── manifest.json                 ← running index of all files
```

---

## 7. PDF → Markdown Conversion Pipeline

After a PDF is downloaded and saved to S3, a conversion step produces a `.md` file.

### Conversion Requirements
- Output encoding: **UTF-8**, Unix line endings (`\n`)
- Preserve: case title, date, bench, case number, full judgment text
- Strip: page numbers, headers/footers, watermarks, form fields
- Handle: multi-column layouts, tables (convert to Markdown tables), footnotes

### Conversion Tool (Recommended)
```
pdfplumber     — accurate text extraction with layout awareness
pymupdf (fitz) — fallback for scanned/image PDFs (OCR via tesseract)
```

### Output Format
```markdown
# RAMA KUER VS STATE OF BIHAR & ANR.

**Court:** Supreme Court of India
**Diary No:** 2904-2007
**Case No:** SLP(C) 1304/2007
**Order Date:** 23-02-2007
**Bench:** HON'BLE MR. JUSTICE H.K. SEMA, HON'BLE MR. JUSTICE D.K. JAIN
**Category:** Civil

---

ITEM NO.14   COURT NO.6   SECTION XVI

SUPREME COURT OF INDIA
RECORD OF PROCEEDINGS

...full judgment text...
```

---

## 8. Product Features (Post-Data)

### Feature 1 — Relevant Case Law Finder
**Who:** Lawyers preparing arguments or submissions
**What:** Lawyer describes their current case (facts, legal issue, Acts involved) → system returns the most relevant precedents ranked by similarity

**How it works:**
1. Lawyer submits case description via API or UI
2. System runs semantic search over the full MD corpus (vector embeddings)
3. Returns top-N cases with relevance score, excerpt, and download link

**Input:**
```json
{
  "matter_description": "Income tax reassessment after 4 years...",
  "acts": ["Income Tax Act 1961"],
  "section": ["147", "148"],
  "court_level": ["Supreme Court", "High Court"]
}
```

**Output:** Ranked list of cases with similarity score, excerpt, full PDF link.

---

### Feature 2 — AI Drafting Assistant
**Who:** Lawyers drafting written submissions, grounds of appeal, or writ petitions
**What:** Lawyer provides their current draft → AI suggests improvements, identifies missing legal grounds, and recommends supporting case citations

**How it works:**
1. Lawyer uploads draft submission (PDF or text)
2. System extracts legal issues from the draft
3. Searches corpus for relevant precedents
4. AI (fine-tuned on Indian case law) suggests improved language, missing grounds, and supporting citations

**Key constraint:** Output must always cite source cases. No hallucinated citations.

---

### Feature 3 — Winning Score Predictor
**Who:** Lawyers and litigants assessing litigation risk before filing or before a hearing
**What:** Upload your current submissions and pleadings → system returns a probability-based winning score with reasoning

**How it works:**
1. Lawyer uploads submissions (grounds of appeal / written arguments)
2. System extracts: legal issues, Acts cited, factual matrix
3. ML model trained on historical outcomes compares to similar past cases
4. Returns:
   - Winning probability score (0–100%)
   - Similar cases where petitioner won / lost
   - Weakest grounds identified
   - Suggested strengthening citations

**Important disclaimer:** This is a statistical estimate based on historical patterns. It is not legal advice and not a guarantee of outcome.

---

## 9. API Design

### Authentication
- JWT tokens for web users
- API Keys for developer/programmatic access
- All endpoints require authentication except `/health` and `/docs`

### Core Endpoints

```
POST   /auth/register              Register new account
POST   /auth/token                 Get JWT token
POST   /auth/api-keys              Create API key

GET    /cases/search               Full-text + semantic search
GET    /cases/{id}                 Get case metadata by ID
GET    /cases/{id}/download/pdf    Get pre-signed S3 URL for PDF
GET    /cases/{id}/download/md     Get pre-signed S3 URL for MD text

POST   /ai/relevant-cases          Feature 1 — find relevant precedents
POST   /ai/draft-assist            Feature 2 — AI drafting suggestions
POST   /ai/winning-score           Feature 3 — predict winning probability
```

### Search Query Parameters
```
GET /cases/search
  ?q=income+tax+reassessment        Full-text query
  &court=SCI                        Court code filter
  &year_from=2020                   Year range filter
  &year_to=2026
  &judge=CHANDRACHUD                Judge name filter
  &petitioner=COMMISSIONER          Party name filter
  &category=1601                    Category code filter
  &page=1                           Pagination
  &per_page=20
```

### Rate Limits (Proposed)
| Plan | Searches/day | Downloads/day | AI calls/day |
|------|-------------|---------------|--------------|
| Free | 100 | 20 | 5 |
| Professional | 2,000 | 500 | 100 |
| Enterprise | Unlimited | Unlimited | Custom |

---

## 10. Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Scraper rate | ~500–1,000 cases/hour (with human delays) |
| PDF storage | ~30 lakh files × ~100 KB avg = ~300 GB (S3) |
| MD storage | ~30 lakh files × ~50 KB avg = ~150 GB (S3) |
| Search latency | < 500ms for full-text search (p95) |
| API uptime | 99.5% (Phase 2) |
| Data freshness | New SCI judgments scraped within 24 hours of publication |
| Scraper detectability | Human-mimicking: Gaussian delays, persistent profile, stealth JS |
| Data format | UTF-8 everywhere, Unix line endings in all MD files |
| Legal compliance | All data scraped from public government portal; no login required |

---

## 11. Success Metrics

### Phase 1 — Data Acquisition
- [ ] 100% of available SCI judgments downloaded (target: ~30 lakh PDFs)
- [ ] 100% of PDFs converted to clean Markdown with < 5% OCR error rate
- [ ] All metadata fields populated for > 90% of cases
- [ ] Zero data loss — every downloaded file verified against source

### Phase 2 — Search API
- [ ] Full-text search index covering all 30 lakh cases
- [ ] Search returns results in < 500ms (p95)
- [ ] 100+ active developer API consumers within 90 days of launch

### Phase 3 — AI Features
- [ ] Relevant Case Finder: > 80% user satisfaction in manual evaluation
- [ ] Winning Score Predictor: backtested accuracy > 65% on historical outcomes
- [ ] Drafting Assistant: cited cases are 100% real (zero hallucinated citations)

---

## 12. Open Decisions

| # | Decision | Options | Blocking? |
|---|---------|---------|-----------|
| D1 | Object storage provider | AWS S3 / GCP GCS / Cloudflare R2 / MinIO (self-hosted) | Yes — Phase 1 output |
| D2 | PDF-to-text library | pdfplumber vs pymupdf vs Adobe API | Yes — Artefact 3 |
| D3 | Search engine | Elasticsearch / OpenSearch / pgvector | Yes — Phase 2 |
| D4 | ML model base | Fine-tune Llama / Mistral / GPT-4 / Legal-BERT | Yes — Phase 3 |
| D5 | Scraper scale strategy | Single machine vs distributed crawl (multiple IPs) | No — can scale later |
| D6 | Platform brand name | TBD | Yes — needed for API docs, domain |
| D7 | Monetisation model | Freemium API / SaaS subscription / data licensing | No — Phase 2 decision |

---

*This document covers both the Product Requirements (what we are building and why) and Technical Requirements (how we are building it).*
*Update this file as decisions in Section 12 are resolved.*
