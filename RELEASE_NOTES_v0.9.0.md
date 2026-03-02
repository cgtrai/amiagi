# Release Notes — amiagi v0.9.0

**Data wydania:** 2025-07-15

## Podsumowanie

Wersja v0.9.0 implementuje trzy kolejne fazy roadmapy v1.0:
- **Faza 5** — Wspólna pamięć i kontekst (Shared Context & Knowledge)
- **Faza 7** — Bezpieczeństwo i sandboxing (Security & Sandboxing)
- **Faza 6** — Silnik workflow (Workflow Engine)

Łącznie **148 nowych testów** (612 ogółem), **13 nowych modułów**, **3 szablony workflow**,
bez regresji w istniejącej funkcjonalności. 0 błędów Pylance.

---

## Faza 5 — Wspólna pamięć i kontekst

### Nowe moduły

| Moduł | Warstwa | Opis |
|---|---|---|
| `infrastructure/shared_workspace.py` | infrastructure | Współdzielony system plików z śledzeniem autorstwa (JSONL audit log) |
| `infrastructure/knowledge_base.py` | infrastructure | Baza wiedzy z wyszukiwaniem TF-IDF w SQLite |
| `application/context_compressor.py` | application | Kompresja historii konwersacji (LLM + heurystyka fallback) |
| `application/cross_agent_memory.py` | application | Pamięć między-agentowa do transferu wiedzy (JSONL persistence) |
| `application/context_window_manager.py` | application | Menedżer okna kontekstowego — priorytetowe składanie prompta |

### Nowe komendy TUI

- `/knowledge status` — liczba wpisów w bazie wiedzy
- `/knowledge search <zapytanie>` — wyszukiwanie TF-IDF
- `/knowledge add <tekst>` — dodanie wpisu
- `/workspace list` — lista plików we wspólnym workspace
- `/workspace read <ścieżka>` — odczyt pliku
- `/workspace log` — historia zmian z autorami

### Konfiguracja (env)

| Zmienna | Domyślnie |
|---|---|
| `AMIAGI_SHARED_WORKSPACE_DIR` | `./data/shared_workspace` |
| `AMIAGI_KNOWLEDGE_BASE_PATH` | `./data/knowledge.db` |
| `AMIAGI_CROSS_MEMORY_PATH` | `./data/cross_agent_memory.jsonl` |
| `AMIAGI_CONTEXT_WINDOW_MAX_TOKENS` | `8000` |

---

## Faza 7 — Bezpieczeństwo i sandboxing

### Nowe moduły

| Moduł | Warstwa | Opis |
|---|---|---|
| `domain/permission_policy.py` | domain | Polityka uprawnień per-agent (narzędzia, ścieżki, sieć, shell, rozmiar pliku) |
| `application/permission_enforcer.py` | application | Middleware weryfikujący uprawnienia z logiem odmów |
| `infrastructure/sandbox_manager.py` | infrastructure | Izolowane katalogi per-agent z ochroną przed path traversal |
| `infrastructure/secret_vault.py` | infrastructure | Magazyn sekretów z obfuskacją XOR (izolacja per-agent) |
| `application/audit_chain.py` | application | Łańcuch audytu append-only (JSONL) |

### Nowe komendy TUI

- `/audit list` — ostatnie 20 wpisów audytu
- `/audit agent <id>` — wpisy audytu konkretnego agenta
- `/sandbox list` — lista aktywnych sandboxów z rozmiarem
- `/sandbox create <agent_id>` — tworzenie sandboxa
- `/sandbox destroy <agent_id>` — usunięcie sandboxa

### Konfiguracja (env)

| Zmienna | Domyślnie |
|---|---|
| `AMIAGI_SANDBOX_DIR` | `./data/sandboxes` |
| `AMIAGI_VAULT_PATH` | `./data/vault.json` |
| `AMIAGI_AUDIT_LOG_PATH` | `./logs/audit.jsonl` |

---

## Faza 6 — Silnik workflow

### Nowe moduły

| Moduł | Warstwa | Opis |
|---|---|---|
| `domain/workflow.py` | domain | Model DAG workflow (NodeType, NodeStatus, WorkflowNode, WorkflowDefinition) |
| `application/workflow_engine.py` | application | Interpreter DAG z fan-out/fan-in, bramkami i warunkami |
| `infrastructure/workflow_checkpoint.py` | infrastructure | Zapis/odczyt stanu workflow do JSON |

### Typy węzłów

- **EXECUTE** — delegacja do executora
- **REVIEW** — recenzja (delegate to executor)
- **GATE** — bramka wymagająca zatwierdzenia (ręcznego lub auto)
- **FAN_OUT** / **FAN_IN** — rozgałęzienie i synchronizacja równoległych ścieżek
- **CONDITIONAL** — warunkowe wykonanie (skip jeśli warunek fałszywy)

### Predefiniowane szablony workflow

| Szablon | Opis | Plik |
|---|---|---|
| `code_review` | Code → Review → Fix → Re-review → Approve | `data/workflows/code_review.json` |
| `research` | Fan-out research → Synthesise → Gate → Report | `data/workflows/research.json` |
| `feature` | Plan → Approve → Implement → Test → Review → Deploy | `data/workflows/feature.json` |

### Nowe komendy TUI

- `/workflow list` — aktywne workflow z postępem
- `/workflow run <szablon>` — uruchomienie workflow z szablonu
- `/workflow status <run_id>` — szczegóły węzłów
- `/workflow approve <run_id> <node_id>` — zatwierdzenie bramki
- `/workflow pause <run_id>` / `/workflow resume <run_id>` — wstrzymanie/wznowienie
- `/workflow templates` — lista dostępnych szablonów

### Konfiguracja (env)

| Zmienna | Domyślnie |
|---|---|
| `AMIAGI_WORKFLOWS_DIR` | `./data/workflows` |
| `AMIAGI_WORKFLOW_CHECKPOINT_DIR` | `./data/workflow_checkpoints` |

---

## Poprawki i ulepszenia

- **WorkflowEngine**: Węzły zależne od FAILED node są automatycznie oznaczane SKIPPED (nie blokują zakończenia run)
- **config.py**: 11 nowych pól konfiguracyjnych z obsługą zmiennych środowiskowych
- **main.py**: Bootstrap wszystkich nowych serwisów (Phase 5/6/7)
- **textual_cli.py**: 10 nowych importów, pełny pass-through nowych serwisów, 6 nowych handlerów komend

## Statystyki

| Metryka | Wartość |
|---|---|
| Nowe moduły produkcyjne | 13 |
| Nowe pliki testów | 13 |
| Nowe testy | 148 |
| Testy łącznie | 612 |
| Błędy Pylance | 0 |
| Wersja Python | 3.10.12 |
