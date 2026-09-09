# Zoiko Rooms AI Assistant — Requirements Compliance Report

**Date:** 2026-09-03
**Product:** Ask Zoiko · Zoiko Assist (admin + renter/host assistant)
**Spec set:** 15 `.docx` requirements docs under `docs/`
**Implementation reviewed:** `backend/app/services/chat_service.py`, `backend/app/api/routes/chatbot.py`, `backend/app/api/routes/user_chat.py`, `backend/app/models/chat.py`, `backend/app/schemas/chat.py`, `backend/app/api/deps.py`, `backend/app/core/config.py`, `backend/app/models/audit.py`, `src/lib/risk.ts`, `src/components/admin/chat/AdminChatPanel.tsx`, `src/components/user/chat/UserChatPanel.tsx`, `src/lib/chat.ts`, `src/lib/user-chat.ts`, `src/middleware.ts`, backend tests.

---

## 1. Executive Summary

The chatbot is currently a **Phase 1/Phase 2 early-stage, read-only, tool-based assistant**. It is deliberately conservative: the model is treated as untrusted plumbing that never holds authority, every data access goes through the same role-scoped CRUD helpers the REST routes use, and there are **no write tools**.

The implementation covers a meaningful subset of the **foundational / trust-and-safety** requirements (read-only tools, role-scoped authorization, prompt-level guardrails, data-minimization, human escalation, audit logging of the stream, SSE streaming, per-actor conversation isolation). The audited meta (`assistant_surface`, `system_capability`, `model_version`, `system_prompt_version`, `policy_pack_version`) is recorded per generation.

However, the **large target architecture described in the specs is largely not implemented**: there is **no RAG/retrieval pipeline**, **no knowledge-base ingestion/lookup**, **no citation/source-block rendering**, **no jurisdiction/market-pack policy engine**, **no ABAC/PDP**, no feature-flag or handoff subsystem, and the **A1–A3 action-tier / confirmation model** is only partially approximated by prompt text rather than enforced by an action engine.

**Overall status:** Partially compliant. Trust-safety guardrails and role scoping are strong; the retrieval, knowledge, and policy-governance layers required by the specs are unimplemented.

---

## 2. Scope, Method & Sources

### 2.1 Documents assessed
| Doc | Requirement prefix |
|---|---|
| PRD | `ZR-AI-PRD-002` |
| FRS | `ZR-AI-FRS-003` |
| API Documentation | `ZR-AI-API-004` |
| RAG Specification | `ZR-AI-RAG-001` |
| Knowledge Base | `ZR-AI-KB-001`, `ZR-AI-KB-006` |
| Database Schema/ERD | `ZR-AI-DB-005` |
| Prompt Engineering / AI Guardrails | `ZR-AI-PG-001` |
| RBAC / Authorization | `ZR-AI-AUTH-001` |
| Security & Privacy | `ZR-AI-AUTH-001` (cross-ref) |
| Technical Architecture | `ZR-AI-ARCH-001` |
| Conversation Flow | `ZR-AI-CFLOW-001` |
| UI/UX | `ZR-AI-UX-001`, `ZR-AI-UX-007` |
| AI Evaluation Test Cases | `ZR-AI-EVAL-001` |
| Ask Zoiko Assistant (product) | `ZR-AI-UX-001` cross-ref |

### 2.2 Method
Each requirement was mapped to concrete implementation evidence (file + line) by reading the implementation directly and running targeted greps for `rag|retriev|vector|embedding|citation|guardrail|pdp|abac|feature_flag|handoff|confirmation|market_pack`. Statuses:
- ✅ **Implemented** — code evidence found
- ⚠️ **Partially / approximated** — behavior present but weaker than spec (e.g., prompt-text-only vs. enforced)
- ❌ **Not implemented** — no code evidence
- ❓ **Unverifiable** — cannot confirm from code alone

---

## 3. Requirements Traceability Matrix

### 3.1 Core product / assistant behavior (PRD / Ask Zoiko)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| PRD | Assistant is product-context aware (Zoiko Rooms) | ✅ | `ADMIN_SYSTEM_PROMPT` / `USER_SYSTEM_PROMPT` in `chat_service.py:480,521` |
| PRD | Read-only: never create/modify/approve/reject | ✅ | No write tools; prompt-only refusal + no write tool handlers; `execute_tool` role checks `chat_service.py:605` |
| PRD | Assistant does not make eligibility/compliance decisions | ⚠️ | Enforced only via prompt text (`chat_service.py:484`), not an action engine; `classifyRisk` in `src/lib/risk.ts` is client-side display only |
| PRD | Ground answers in tool output, never guess | ✅ | Prompt: “Ground every factual claim in tool output” `chat_service.py:490,533` |
| PRD | Single clarifying question when ambiguous | ✅ | Prompt text `chat_service.py:491,534` |
| PRD | Seek human escalation on high-consequence topics | ✅ | Prompt text in both prompts `chat_service.py:511,551` |

### 3.2 API (ZR-AI-API-004)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| API | `POST …/conversations` (create) | ✅ | `chatbot.py:79`, `user_chat.py` |
| API | `GET …/conversations` (list, own only) | ✅ | `chatbot.py:67`, `user_chat.py` |
| API | `DELETE …/conversations/{id}` | ✅ | `chatbot.py:93`, `user_chat.py` |
| API | `GET …/conversations/{id}/messages` | ✅ | `chatbot.py:106`, `user_chat.py` |
| API | Stream assistant reply (SSE) | ✅ | `chatbot.py:116`, `user_chat.py`, `StreamingResponse` `chatbot.py:195` |
| API | Auto-title from first message | ✅ | `chatbot.py:130`; tested `test_admin_chat.py` / `test_user_chat.py` |
| API | 401 on unauthenticated/invalid/inactive | ✅ | deps `get_current_admin`/`get_current_user` `deps.py:17,45`; tested |

### 3.3 Database (ZR-AI-DB-005)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| DB | Conversation table | ✅ | `ChatConversation` `models/chat.py:9` |
| DB | Message table w/ tool-call persistence | ✅ | `ChatMessage` `models/chat.py:32` (`tool_calls_json`, `tool_results_json`, `meta_json`) |
| DB | Role-scoped ownership (admin_id / user_id) | ✅ | `models/chat.py:15,16`; isolation tested |
| DB | Audit trail for chatbot activity | ⚠️ | `AuditEvent` `models/audit.py:9`; chat CRUD + per-message audit written (`chatbot.py:89,103,142–166`), but no audit of **user** chat in `user_chat.py` confirmed scope, and no per-tool-result detail |
| DB | RAG/knowledge/citation/policy tables | ❌ | No such tables; only `meta_json` carries `policy_pack_version="core"` |

### 3.4 RAG (ZR-AI-RAG-001)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| RAG | Retrieval pipeline for docs/listings/market packs | ❌ | No retrieval/vector/embedding code anywhere in backend |
| RAG | Grounded, cited answers from retrieved sources | ❌ | Tools return raw rows; no source-block/citation metadata emitted |
| RAG | Async ingestion of documents into KB | ❌ | Not present |

### 3.5 Knowledge Base (ZR-AI-KB-001, ZR-AI-KB-006)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| KB | Maintains product/domain knowledge base | ❌ | No KB ingestion/content pipeline |
| KB | Content versioning / policy versioning | ⚠️ | Only a hardcoded `POLICY_PACK_VERSION = "core"` (`chat_service.py:47`); no versioned KB content |

### 3.6 Prompt Engineering & Guardrails (ZR-AI-PG-001)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| PG | Model treated as untrusted plumbing | ✅ | Module docstring `chat_service.py:5–7`; no write tools |
| PG | Refuse to make determinations | ✅ | Both prompts (`chat_service.py:494,539`) |
| PG | Fairness — no protected-characteristic inference | ✅ | Both prompts (`chat_service.py:500,543`) |
| PG | Data minimization — never collect secrets/PCI in chat | ✅ | Both prompts (`chat_service.py:506,548`) |
| PG | Never reveal system instructions | ✅ | Both prompts (`chat_service.py:515,556`) |
| PG | No feelings / human identity / licensure claims | ✅ | Both prompts (`chat_service.py:518,558`) |
| PG | Never present confidence as platform determination | ✅ | Both prompts (`chat_service.py:498,542`) |
| PG | Versioned system prompt | ✅ | `SYSTEM_PROMPT_VERSION = "1.0"` (`chat_service.py:48`), recorded in `meta_json` |

### 3.7 RBAC / Authorization (ZR-AI-AUTH-001)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| AUTH | Role-scoped tools | ✅ | `ToolSpec.roles` + `super_admin_only` (`chat_service.py:271–278,449–473,605–617`); tested (`test_admin_chat.py:TestToolGating`, `test_user_chat.py:TestUserToolRegistry`) |
| AUTH | Admin tools gated to super-admin where required | ✅ | `list_bookings/guests/reviews/payments`, `revenue_trend`, etc. `super_admin_only=True`; enforced in `execute_tool` |
| AUTH | Admin token cannot access user chat and vice-versa | ✅ | `deps.py:30–32,58–60`; tested `test_user_chat.py:99` |
| AUTH | Cross-actor conversation isolation (404) | ✅ | `_get_owned_conversation` + per-user scoping; tested |
| AUTH | ABAC / PDP (attribute-based policy decision point) | ❌ | Only static role gating; no PDP/ABAC engine |

### 3.8 Security & Privacy
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| SEC | No secrets in user-facing errors | ✅ | `_log_failure` + user-safe messages `chatbot.py:33–41,169–193` |
| SEC | Config errors logged, not leaked | ✅ | `ChatServiceError.log_detail` (`chat_service.py:568–575`) |
| SEC | Timeouts & bounded retries on provider | ✅ | `GROQ_TIMEOUT_SECONDS`, `CONNECTION_RETRY_ATTEMPTS`, `_open_stream` `chat_service.py:37–38,592` |
| SEC | Bounded tool loop (prevent runaway) | ✅ | `for _turn in range(5)` `chat_service.py:648` |
| SEC | History window bounded | ✅ | `MAX_HISTORY_MESSAGES = 30` `chatbot.py:26` |
| SEC | `.env.example` provides safe placeholders | ✅ | `.env.example` has `change-…`/placeholder values |

### 3.9 Technical Architecture (ZR-AI-ARCH-001)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| ARCH | Tool-based assistant (function calling) | ✅ | Groq function-calling loop `chat_service.py:648–715` |
| ARCH | Streaming responses | ✅ | SSE throughout |
| ARCH | Feature flags | ❌ | Not implemented |
| ARCH | Handoff to human agent | ❌ | Prompt-only escalation text; no handoff subsystem |
| ARCH | Confirmation/action-tier engine (A1–A3) | ❌ | Only approximated by prompt refusal text; no A1–A3 enforcement |

### 3.10 Conversation Flow (ZR-AI-CFLOW-001)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| CFLOW | Multi-turn context retained | ✅ | History persisted + replayed (`_conversation_history` `chatbot.py:50`) |
| CFLOW | Tool traffic not replayed into context | ✅ | Only user/assistant text replayed `chatbot.py:58–59` |

### 3.11 UI/UX (ZR-AI-UX-001 / ZR-AI-UX-007)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| UX | Admin chat panel w/ SSE streaming | ✅ | `AdminChatPanel.tsx` |
| UX | User chat panel w/ SSE streaming | ✅ | `UserChatPanel.tsx` |
| UX | Launcher FAB both roles | ✅ | `ChatLauncherFab.tsx`, `UserChatLauncherFab.tsx` |
| UX | Assistant brand avatar | ✅ | `AssistantAvatar.tsx` |
| UX | Risk-class contextual reliance warning (Layer C) | ⚠️ | `src/lib/risk.ts` classifies R0–R4 client-side and shows a confirm-with-authority warning; but this is display-only, not a server-enforced boundary |
| UX | Markdown rendering of answers | ✅ | `MarkdownMessage.tsx` |
| UX | Explicit disclaimer copy | ✅ | Panel intro copy `AdminChatPanel.tsx:430`, `UserChatPanel.tsx:406` |

### 3.12 AI Evaluation Test Cases (ZR-AI-EVAL-001)
| Req | Requirement | Status | Evidence |
|---|---|---|---|
| EVAL | Suite of evaluation test cases for the assistant | ❌ | No eval harness/test-case runner found in repo |
| EVAL | Guardrail/refusal tests | ❌ | No backend tests assert refusal/determination behavior |

---

## 4. Implemented Features (Evidence Summary)

1. **Role-scoped read-only tool registry** — 20 tools; admin vs. super-admin vs. user filtered by `ToolSpec.roles`/`super_admin_only` (`chat_service.py:281–446`, `449`).
2. **Deterministic authorization outside the model** — `execute_tool` re-checks role/server-side (`chat_service.py:605–617`), so a model cannot overreach tools.
3. **Dual system prompts** matching admin vs. renter/host personas, both encoding guardrails (`chat_service.py:480,521`).
4. **SSE streaming** with `text`/`tool`/`tool_error`/`done`/`error` events (`chatbot.py:195`, `chat_service.py:629`).
5. **Persistence** — conversations, messages, tool-call JSON, and runtime `meta_json` (`models/chat.py`).
6. **Audit logging** of conversation create/delete, per-message tool usage, and errors (`chatbot.py:89,103,142–193`).
7. **Bounded loops/timeouts/retries** and user-safe error text.
8. **Conversation isolation** between and across roles.
9. **Frontend chat UI** for both admin and user, with FABs, markdown, avatar, and risk-class reliance warning.
10. **Backend integration tests** for both chat endpoints (CRUD, auth, SSE, tool gating, isolation).

---

## 5. Gaps / Non-Implemented Requirements

| Area | Gap | Severity |
|---|---|---|
| **RAG** | No retrieval/vector embeddings; answers are structured tool rows, not retrieved documents | High (core spec layer) |
| **Knowledge Base** | No KB ingestion, content, or domain docs; hardcoded `policy_pack_version="core"` | High |
| **Citations** | No source-block/citation metadata surfaced to the user | High |
| **Policy engine / market packs** | No jurisdiction/market-pack policy rules; `POLICY_PACK_VERSION` is a constant | Medium |
| **A1–A3 action tiers** | No enforcement of action tiers / confirmation workflow (prompt text only) | Medium |
| **ABAC / PDP** | Only static role gating; no attribute-based policy decision | Medium |
| **Feature flags** | Not implemented | Low |
| **Handoff** | No handoff subsystem; escalation is prompt text only | Medium |
| **Determination refusal** | Guarded by prompt text only — not enforced by any deterministic layer | Medium |
| **Server risk boundary** | Risk classification is client-side display only; no server-side authoritative risk routing | Medium |
| **Eval suite (ZR-AI-EVAL-001)** | No evaluation test cases / harness | High (governance) |

---

## 6. Undocumented Features

Features present in code but not specified by the requirements docs:
- `MAX_TOOL_ROWS = 20` row cap per tool (`chat_service.py:36`).
- `MAX_HISTORY_MESSAGES = 30` history window (`chatbot.py:26`).
- 5-turn bounded tool loop (`chat_service.py:648`).
- Auto-titling of conversations from the first message (`chatbot.py:130`).
- `tool_error` SSE event and `tool_results_json` replay column (`models/chat.py:49`).
- Client-side risk classifier (`src/lib/risk.ts`) — partially tied to `ZR-AI-UX-001 §6` comment but goes beyond spec.
- Correlation IDs threaded into audit events (`get_correlation_id`).
- Password-change token invalidation for user chat sessions (`deps.py:68–71`).

---

## 7. Risks & Security Concerns

1. **Hardcoded/stored secrets in `.env` (not committed, but operational risk).** `backend/.env` holds a real `GROQ_API_KEY`, `JWT_SECRET`, and `SEED_ADMIN_PASSWORD` (masked in this review). These are production-looking values stored in plaintext; forbid committing `.env` and rotate if it was ever shared. `config.py` also has weak dev defaults (`jwt_secret="dev-secret-change-me"`, `seed_admin_password="change-this-password"`, non-TLS). **High.**
2. **Prompt-text-only guardrails.** Determination refusal, fairness, and escalation all rely on model compliance; a prompt-injection-plausible failure has no deterministic backstop. **Medium.**
3. **No rate limiting on the chat stream at the API layer** (only Groq’s own limit surfaced as an error). Unauthenticated-free auth exists, but authenticated users could spam provider calls/cost. **Medium.**
4. **Client-side risk classification** can be bypassed; the "confirm with the authoritative record" warning is cosmetic in the client. **Low.**
5. **No citations means unverifiable claims** for high-consequence topics; grounding is in tool rows but the source record id may not be surfaced to the user. **Medium.**
6. **`StreamingResponse` without `X-Content-Type-Options`/full security headers** observed; confirm production headers at the proxy/CDN layer. **Low.**
7. **`COOKIE_SECURE=false`** in `.env` while a production `https://app.zoikorooms.com` CORS origin is configured — cookies are not `Secure`-flagged in this config, risking transport disclosure. **High** (flagged previously; revert to true in prod).

---

## 8. Test Coverage

### 8.1 Present
- `backend/tests/test_admin_chat.py` — conversation CRUD, ownership isolation, auth (401/403, inactive/pending), SSE streaming, rate-limit error event, tool gating (super-admin vs admin vs user).
- `backend/tests/test_user_chat.py` — CRUD, user isolation, cross-role isolation (admin token denied), inactive-user 401, SSE, tool registry whitelist.
- `backend/tests/test_chat_schema.py` — schema validation.

### 8.2 Gaps
- **No frontend tests** for chat panels (`*.test.tsx` absent).
- **No tests for guardrail/refusal behaviour** (determination, fairness, PII refusal) — ZR-AI-EVAL-001 coverage missing.
- **No tests for the client-side risk classifier** (`src/lib/risk.ts`).
- **No tests for audit-event content** of chat messages, or for `user_chat.py` audit paths.
- **No tests for error-copy leakage** (ensuring no env-var/provider internals reach the SSE stream).

---

*Report generated by automated traceability review against the implementation. Statuses are evidence-based from source inspection; run the backend suite with `pytest` in `backend/` to re-verify tests pass.*
