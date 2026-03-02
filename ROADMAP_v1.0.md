# amiagi — Roadmap do v1.0

Plan wdrożenia pełnoprawnego środowiska orkiestracji agentów.
Stan aktualny: **v0.9.0** (Fazy 1–7 zrealizowane, 612 testów).
Stan wyjściowy: v0.2.0 (2 aktorów, multi-backend, skills, session persistence, 328 testów).

Cel: system, w którym Sponsor **opisuje potrzebę**, a framework **powołuje, konfiguruje, testuje i nadzoruje** zespół agentów realizujących złożone zadania.

---

## Strategia priorytetyzacji

### ✅ Zrealizowane (v0.9.0)

Fazy 1–4 dostarczone w release v0.6.0 (136 nowych testów, 21 modułów, 1 static asset):
- **Faza 1**: Agent Registry & Lifecycle — AgentDescriptor, AgentRegistry, AgentFactory, AgentRuntime, LifecycleLogger
- **Faza 2**: AgentWizard — AgentBlueprint, AgentWizardService, PersonaGenerator, SkillRecommender, ToolRecommender, AgentTestRunner
- **Faza 3**: Task Queue & Work Distribution — Task, TaskQueue, TaskDecomposer, WorkAssigner, TaskScheduler
- **Faza 4**: Observability & Dashboard — MetricsCollector, AlertManager, SessionReplay, DashboardServer, DashboardUI

Fazy 5–7 dostarczone w release v0.9.0 (148 nowych testów, 13 modułów, 3 szablony workflow):
- **Faza 5**: Shared Context & Memory — SharedWorkspace, KnowledgeBase, ContextCompressor, CrossAgentMemory, ContextWindowManager
- **Faza 7**: Security & Isolation — AgentPermissionPolicy, PermissionEnforcer, SandboxManager, SecretVault, AuditChain
- **Faza 6**: Workflow Engine — WorkflowDefinition, WorkflowEngine, WorkflowCheckpoint + 3 szablony

### Priorytety na kolejne fazy (od v0.10.0)

Kolejność wynika z analizy zależności i wartości operacyjnej na obecnym stanie v0.9.0:

### ✅ [ZREALIZOWANE] Priorytet 1 — Shared Context & Memory (Faza 5) → v0.9.0
### ✅ [ZREALIZOWANE] Priorytet 2 — Security & Isolation (Faza 7) → v0.9.0
### ✅ [ZREALIZOWANE] Priorytet 3 — Workflow Engine (Faza 6) → v0.9.0

### Priorytet 4 — ŚREDNI: Cost Governance (Faza 8)

**Dlaczego teraz**: z wieloma agentami i API backendem (OpenAI) koszty mogą wymknąć się spod kontroli. BudgetManager + RateLimiter + VRAMScheduler są konieczne do produkcyjnego użycia. Zależy od Dashboard (Faza 4) — alerty kosztowe rozszerzające AlertManager.

### Priorytet 5 — ŚREDNI: Evaluation & Quality (Faza 9) ★ NASTĘPNA

**Dlaczego teraz**: po ustabilizowaniu infrastruktury (Fazy 1–8), trzeba zmierzyć jakość. Rubric scoring, benchmarki, A/B testing i regression detection pozwalają iterować na konfiguracji agentów. AgentTestRunner z Fazy 2 jest zaczątkiem — Faza 9 to pełny framework ewaluacyjny.

### Priorytet 6 — NIŻSZY: External Integration & API (Faza 10)

**Dlaczego teraz**: REST API, webhooks, SDK i CI adapter otwierają system na integracje zewnętrzne. Sens ma dopiero gdy core jest kompletny i przetestowany (Fazy 1–9). Wcześniejsze udostępnienie API narażałoby na breaking changes.

### Priorytet 7 — CAPSTONE: Team Composition (Faza 11 → v1.0.0)

Wieńczący kamień milowy. TeamComposer + DynamicScaler + TeamDashboard. Wymaga wszystkich wcześniejszych faz. Przejście od "zbioru agentów" do "zarządzanego zespołu".

---

## Fazy wdrożenia

```text
v0.9.0 (aktualny — Fazy 1–7 zrealizowane)
  │
  ├─ Faza 1: Agent Registry & Lifecycle        → v0.6.0   ✅ DONE
  ├─ Faza 2: AgentWizard                       → v0.6.0   ✅ DONE
  ├─ Faza 3: Task Queue & Work Distribution     → v0.6.0   ✅ DONE
  ├─ Faza 4: Observability & Dashboard          → v0.6.0   ✅ DONE
  ├─ Faza 5: Shared Context & Memory           → v0.9.0   ✅ DONE
  ├─ Faza 7: Security & Isolation (per-agent)   → v0.9.0   ✅ DONE
  ├─ Faza 6: Workflow Engine (DAG)              → v0.9.0   ✅ DONE
  │
  ├─ Faza 8: Resource & Cost Governance         → v0.10.0  ★ PRIORYTET 1 — ŚREDNI
  ├─ Faza 9: Evaluation & Quality Framework     → v0.11.0  ★ PRIORYTET 2 — ŚREDNI
  ├─ Faza 10: External Integration & API        → v0.12.0  ○ PRIORYTET 3 — NIŻSZY
  └─ Faza 11: Persona & Team Composition        → v1.0.0   ◆ CAPSTONE
```

> **Uwaga**: Faza 7 (Security) awansowała przed Fazę 6 (Workflow Engine) — bezpieczeństwo
> musi być na miejscu zanim workflow'y pozwolą agentom wykonywać złożone,
> wielokrokowe operacje bez nadzoru ludzkiego.

---

## Faza 1 — Agent Registry & Lifecycle (v0.6.0) ✅ DONE

**Cel**: przekształcenie hardcoded Polluks/Kastor w dynamiczny rejestr N agentów.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 1.1 | `AgentDescriptor` (domain) | Dataclass: `agent_id`, `name`, `role` (executor/supervisor/specialist), `persona_prompt`, `model_backend` (ollama/openai), `model_name`, `skills: list[str]`, `tools: list[str]`, `state` (enum: IDLE, WORKING, PAUSED, ERROR, TERMINATED), `created_at`, `metadata: dict` |
| 1.2 | `AgentRegistry` (application) | Thread-safe rejestr agentów: `register()`, `unregister()`, `get()`, `list_by_role()`, `list_by_state()`, `update_state()`. Backed by SQLite (tabela `agents`). Emituje zdarzenia lifecycle do JSONL. |
| 1.3 | `AgentFactory` (application) | `create_agent(descriptor) -> AgentRuntime` — tworzy `ChatService` z właściwym clientem (Ollama/OpenAI), wstrzykuje persona prompt + skills, rejestruje w registry. |
| 1.4 | `AgentRuntime` (infrastructure) | Wrapper na `ChatService` + metadata agenta. Metody: `ask()`, `pause()`, `resume()`, `terminate()`. Lifecycle hooks: `on_spawn`, `on_pause`, `on_resume`, `on_terminate`, `on_error`. |
| 1.5 | Lifecycle events (JSONL) | Każda zmiana stanu agenta → wpis w `logs/agent_lifecycle.jsonl`: `{agent_id, event, timestamp, details}`. |
| 1.6 | Migracja Polluks/Kastor | Istniejący Polluks i Kastor stają się agentami w registry (bootstrap przy starcie). `textual_cli.py` odpytuje registry zamiast hardcoded referencji. Supervisor-role flagowany w `AgentDescriptor.role`. |
| 1.7 | Komendy TUI | `/agents list` — lista agentów, stan, model, rola. `/agents info <id\|name>` — szczegóły agenta. `/agents pause <id>` / `/agents resume <id>`. |
| 1.8 | Testy | Unit testy: AgentDescriptor, AgentRegistry (CRUD, concurrency), AgentFactory (mock clients), lifecycle events. Integration: migracja Polluks/Kastor do registry, komendy TUI. |

### Zależności

- Brak zależności od innych faz — to jest fundament.
- Wymaga refactoru `textual_cli.py` w kierunku powiązania z registry zamiast bezpośrednich referencji do `chat_service.ollama_client` / `supervisor_service`.

### Kryteria akceptacji

- [x] Polluks i Kastor działają jak dotychczas, ale są agentami w registry.
- [x] Można programowo (z kodu) utworzyć trzeciego agenta i on działa.
- [x] Lifecycle events logowane do JSONL.
- [x] Komendy `/agents` działają.
- [x] Istniejące 328 testów nadal przechodzą.

---

## Faza 2 — AgentWizard (v0.6.0) ✅ DONE

**Cel**: interaktywny lub programowy proces tworzenia nowego agenta przez Sponsora, prowadzony przez wybrany model LLM.

### Koncepcja

AgentWizard to **meta-proces**, w którym Sponsor opisuje **potrzebę** (np. "potrzebuję code reviewera do Pythona"), a wybrany model (np. Kastor lub dedykowany planner) generuje kompletny profil agenta: personę, skills, narzędzia, uprawnienia, i propozycję modelu. Sponsor zatwierdza lub modyfikuje, po czym AgentFactory tworzy agenta, a framework uruchamia testy walidacyjne.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 2.1 | `AgentWizardService` (application) | Orkiestrator procesu: przyjmuje opis potrzeby od Sponsora → prowadzi konwersację z modelem-plannerem → generuje `AgentBlueprint`. |
| 2.2 | `AgentBlueprint` (domain) | Dataclass: `name`, `role`, `persona_prompt`, `system_prompt_template`, `required_skills: list[str]`, `required_tools: list[str]`, `suggested_model: str`, `suggested_backend: str`, `initial_permissions: dict`, `test_scenarios: list[TestScenario]`, `team_function: str` (np. "code reviewer", "researcher", "tester"), `communication_style: str`. |
| 2.3 | `PersonaGenerator` (application) | Moduł generujący persona prompt na podstawie: roli, funkcji w zespole, stylu komunikacji, oczekiwanych skills. Korzysta z szablonów + LLM doprecyzowania. |
| 2.4 | `SkillRecommender` (application) | Na podstawie opisu potrzeby: przegląda `skills/` catalog, rekomenduje istniejące skille, proponuje wygenerowanie brakujących (treść Markdown), tworzy pliki w `skills/<agent_role>/`. |
| 2.5 | `ToolRecommender` (application) | Analogicznie do skills — rekomenduje narzędzia z istniejącego zestawu, sygnalizuje brakujące. |
| 2.6 | `AgentTestRunner` (application) | Uruchamia zestaw scenariuszy testowych na nowo utworzonym agencie: wysyła zadania testowe, mierzy jakość odpowiedzi (keyword match, Kastor-review, rubric scoring). Raportuje wynik do Sponsora. |
| 2.7 | `WizardConversation` (infrastructure) | Wieloturowa konwersacja Sponsor ↔ model-planner. Structured output parsing (JSON z LLM). Checkpoint'owanie stanu konwersacji (wznawianie po przerwaniu). |
| 2.8 | Integracja TUI | `/agent-wizard` — uruchamia interaktywny wizard w panelu Sponsora. Kroki: (1) Sponsor opisuje potrzebę → (2) model generuje blueprint → (3) Sponsor review/edycja → (4) potwierdzenie → (5) AgentFactory.create → (6) AgentTestRunner.validate → (7) raport. `/agent-wizard --from-file <blueprint.yaml>` — tryb nieinteraktywny. |
| 2.9 | Blueprint persistence | Blueprinty zapisywane w `data/agents/blueprints/<agent_name>.yaml`. Reużywalne — `/agent-wizard --clone <name>` tworzy kopię. |
| 2.10 | Testy | Unit: PersonaGenerator, SkillRecommender, ToolRecommender (z mockami LLM). Integration: pełny flow wizard → agent created → test run → raport. |

### Przebieg AgentWizard (flow)

```text
Sponsor: "Potrzebuję agenta do code review w Pythonie"
    │
    ▼
[WizardConversation] ←→ [model-planner (np. Kastor)]
    │  Planner pyta: "Jaki zakres? Backend? Frontend? Testing?"
    │  Sponsor odpowiada: "Backend, FastAPI, SQLAlchemy"
    │
    ▼
[PersonaGenerator] → persona prompt
[SkillRecommender] → lista skills (istniejące + propozycje nowych)
[ToolRecommender]  → lista narzędzi
    │
    ▼
[AgentBlueprint] — kompletny profil
    │  Sponsor review: "Zmień styl na bardziej formalny"
    │
    ▼
[AgentFactory.create(blueprint)] → agent zarejestrowany w Registry
    │
    ▼
[AgentTestRunner.validate(agent, blueprint.test_scenarios)]
    │  Wynik: 4/5 scenariuszy zaliczonych
    │
    ▼
[Raport do Sponsora] → Agent gotowy / wymaga korekcji
```

### Zależności

- **Faza 1** (Agent Registry & Factory) — wymagana w całości.
- Skills system (v0.2.0) — rozszerzenie o dynamiczne tworzenie skills.

### Kryteria akceptacji

- [x] Sponsor opisuje potrzebę w języku naturalnym → agent jest tworzony automatycznie.
- [x] Blueprint jest pełny: persona, skills, tools, permissions, scenariusze testowe.
- [ ] Sponsor może przeglądać i edytować blueprint przed zatwierdzeniem. *(częściowo — brak interaktywnej edycji w TUI)*
- [x] AgentTestRunner uruchamia scenariusze i raportuje wyniki.
- [x] Blueprint zapisany na dysku, reużywalny.

> **Uwaga**: Faza 2 zrealizowana w trybie heurystycznym + LLM fallback. Interaktywna
> wieloturowa konwersacja Sponsor ↔ planner (`WizardConversation`, 2.7) nie została
> zaimplementowana — do rozważenia jako enhancement w przyszłym release'ie.

---

## Faza 3 — Task Queue & Work Distribution (v0.6.0) ✅ DONE

**Cel**: wielozadaniowość z priorytetami, deadlinami i automatycznym przydzielaniem do agentów.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 3.1 | `Task` (domain) | Dataclass: `task_id`, `title`, `description`, `priority` (enum: CRITICAL, HIGH, NORMAL, LOW), `status` (PENDING, ASSIGNED, IN_PROGRESS, REVIEW, DONE, FAILED), `assigned_agent_id`, `parent_task_id` (subtasks), `dependencies: list[task_id]`, `deadline: datetime|None`, `created_at`, `result: str`, `metadata: dict`. |
| 3.2 | `TaskQueue` (application) | Priority queue z dependency resolution: `enqueue()`, `dequeue_next(agent_skills)`, `mark_done()`, `mark_failed()`, `get_ready_tasks()` (taski z zaspokojonych zależnościach). Backed by SQLite. |
| 3.3 | `TaskDecomposer` (application) | Przyjmuje złożone zadanie + kontekst → korzysta z LLM (Kastor lub planner) → zwraca listę subtasków z zależnościami. Structured output → lista `Task` z `parent_task_id` i `dependencies`. |
| 3.4 | `WorkAssigner` (application) | Matching: ready tasks ↔ idle agents. Algorytm: (1) filtruj agentów wg wymaganych skills, (2) sortuj wg obciążenia (least loaded), (3) przydziel. Backpressure: gdy brak idle agentów → task czeka w kolejce. |
| 3.5 | `TaskScheduler` (infrastructure) | Pętla główna (async timer): co N sekund sprawdza `get_ready_tasks()`, wywołuje `WorkAssigner`, uruchamia agentów. Respektuje `deadline` — eskaluje CRITICAL jeśli blisko terminu. |
| 3.6 | Komendy TUI | `/tasks list [--status X]`, `/tasks add <opis>`, `/tasks info <id>`, `/tasks cancel <id>`, `/tasks assign <task_id> <agent_id>` (ręczne). |
| 3.7 | Integracja z routerem | Obecny router dla Sponsora staje się "human task interface" — wiadomość Sponsora → `TaskDecomposer` → subtaski → `TaskQueue`. |
| 3.8 | Testy | Unit: TaskQueue (priority, dependency resolution, FIFO within priority), WorkAssigner (skill matching, backpressure), TaskDecomposer (mock LLM). Integration: pełny flow task → decompose → assign → execute → done. |

### Zależności

- **Faza 1** (Agent Registry) — wymagany `AgentRegistry.list_by_state(IDLE)`.
- Faza 2 opcjonalna ale korzystna (więcej agentów = więcej sensu w kolejce).

---

## Faza 4 — Observability & Monitoring Dashboard (v0.6.0) ✅ DONE

**Cel**: web-based dashboard z real-time widokiem na cały system agentów.

> **Dlaczego tak wcześnie?** amiagi loguje wszystko do JSONL od v0.1.0. Te dane już istnieją — dashboard to niskokosztowa inwestycja o ogromnej wartości operacyjnej. Bez observability zarządzanie N agentami (od Fazy 3) byłoby ślepe.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 4.1 | `MetricsCollector` (infrastructure) | Zbieranie metryk: task duration, agent utilization, token consumption, success rate, error rate. In-memory ringbuffer + periodic flush do SQLite. |
| 4.2 | `DashboardServer` (infrastructure) | Lekki web server (wbudowany Python: `http.server` + SSE lub WebSocket). Endpointy: `/api/agents`, `/api/tasks`, `/api/metrics`, `/api/events` (SSE stream). |
| 4.3 | `DashboardUI` (interfaces) | Single-page HTML/JS (vanilla, zero dependencies). Panele: (1) Agent overview (karta per agent: stan, model, task, cost), (2) Task board (Kanban: pending → in_progress → review → done), (3) Metrics (wykresy: tokens/min, cost/hour, task throughput), (4) Event log (live stream zdarzeń). |
| 4.4 | `TraceViewer` | Wizualizacja łańcucha: user request → decomposition → agent executions → results. Timeline view jak Jaeger/zipkin, ale dla agentowych trace'ów. |
| 4.5 | `AlertManager` (application) | Reguły alertów: agent unresponsive > X min, error rate > threshold. Powiadomienia: log + TUI notification + opcjonalnie webhook. Rozszerzany o alerty kosztowe po Fazie 8. |
| 4.6 | `SessionReplay` (infrastructure) | Odtworzenie sesji z JSONL: załaduj logi → replay zdarzeń na timeline. Do debugowania i audytu. |
| 4.7 | Komendy TUI | `/dashboard start [--port 8080]`, `/dashboard stop`. |
| 4.8 | Testy | Unit: MetricsCollector (ringbuffer, flush), AlertManager (rule evaluation). Integration: DashboardServer (HTTP endpoints, SSE). |

### Zależności

- **Faza 1** (Agent Registry) — dane agentów.
- **Faza 3** (Task Queue) — dane tasków.
- UsageTracker (v0.2.0) — istniejące metryki tokenów i kosztów.

---

## Faza 5 — Shared Context & Memory (v0.9.0) ✅ DONE

**Cel**: agenci współdzielą wiedzę bez powtarzania kontekstu.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 5.1 | `SharedWorkspace` (infrastructure) | Wspólny katalog projektowy per-task/per-team. Agenci czytają/piszą pliki, zmiany widoczne natychmiast. Śledzenie autorstwa (kto co zmienił). |
| 5.2 | `KnowledgeBase` (infrastructure) | Wektorowa baza wiedzy (SQLite + embeddingi lub prosty TF-IDF na start). `store(text, metadata)`, `query(question, top_k)`. Per-projekt, dostępna dla wszystkich agentów. |
| 5.3 | `ContextCompressor` (application) | Streszczanie historii konwersacji gdy kontekst przekracza okno modelu. `compress(messages, max_tokens) -> summarized_messages`. Używa LLM do generowania streszczeń. |
| 5.4 | `CrossAgentMemory` (application) | Agent A kończy task → kluczowe wnioski zapisywane w shared memory. Agent B na starcie task'a dostaje relevant context z memory. Format: `{agent_id, task_id, key_findings, timestamp}`. |
| 5.5 | `ContextWindowManager` (application) | Inteligentne budowanie kontekstu: system prompt + skills + relevant memory + task context + conversation history. Dbanie o fit w okno modelu. |
| 5.6 | Testy | Unit: SharedWorkspace (concurrent read/write), KnowledgeBase (store/query), ContextCompressor (mock LLM), CrossAgentMemory (CRUD). |

### Zależności

- **Faza 1** (Agent Registry) — metadata agentów.
- **Faza 3** (Task Queue) — kontekst per-task.

---

## Faza 6 — Workflow Engine / DAG (v0.9.0) ✅ DONE

**Cel**: deklaratywne przepływy pracy z warunkowym branchingiem i równoległością.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 6.1 | `WorkflowDefinition` (domain) | DAG: nodes (task templates) + edges (dependencies, conditions). Format: YAML/JSON. Wbudowane typy: `execute`, `review`, `gate` (human approval), `fan_out`, `fan_in`, `conditional`. |
| 6.2 | `WorkflowEngine` (application) | Interpreter DAG: śledzi stan każdego node'a, odpala ready nodes, obsługuje branche warunkowe, fan-out/fan-in (parallel → merge). |
| 6.3 | `WorkflowCheckpoint` (infrastructure) | Serializacja stanu workflow do SQLite/JSON. Wznowienie po awarii/restarcie. |
| 6.4 | Predefiniowane workflow'y | `code_review.yaml` — code → review → fix → re-review → merge. `research.yaml` — search → summarize → validate → report. `feature.yaml` — plan → implement → test → review → deploy. |
| 6.5 | Komendy TUI | `/workflow run <name>`, `/workflow status`, `/workflow pause`, `/workflow list` (dostępne szablony). |
| 6.6 | Testy | Unit: WorkflowEngine (linear, conditional, parallel, fan-out/fan-in). Integration: pełny code_review workflow z mock agentami. |

### Zależności

- **Faza 3** (Task Queue) — workflow generuje taski.
- **Faza 1** (Agent Registry) — workflow przydziela agentów.

---

## Faza 7 — Security & Isolation per-agent (v0.9.0) ✅ DONE

**Cel**: każdy agent ma własne uprawnienia i izolowane środowisko wykonania.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 7.1 | `AgentPermissionPolicy` (domain) | Per-agent zestaw uprawnień: `allowed_tools: list[str]`, `allowed_paths: list[glob]`, `network_access: bool`, `shell_access: bool`, `max_file_size_bytes`, `read_only_paths: list[glob]`. |
| 7.2 | `PermissionEnforcer` (application) | Middleware na `tool_calling`: przed execute sprawdza policy agenta. Odmowa → log + komunikat do agenta. |
| 7.3 | `SandboxManager` (infrastructure) | Izolowane środowisko per agent: osobny working directory, ograniczony filesystem view. Opcjonalnie: nsjail/bubblewrap na Linuxie. |
| 7.4 | `SecretVault` (infrastructure) | Per-agent store na credentiale (API keys, tokeny). Agent "researcher" ma swój API key, agent "deployer" ma SSH key. Izolacja: agent A nie widzi secretów agenta B. |
| 7.5 | `AuditChain` (application) | Łańcuch odpowiedzialności: każda akcja systemowa → `{agent_id, action, target, timestamp, approved_by}`. Kto zlecił, kto zatwierdził, kto wykonał. |
| 7.6 | Integracja z AgentWizard | Blueprint definiuje `initial_permissions`. Wizard waliduje z Sponsorem: "Agent chce dostęp do shell — potwierdzasz?". |
| 7.7 | Testy | Unit: PermissionEnforcer (allow/deny per tool, per path), SandboxManager (isolation). Integration: agent bez uprawnień do shell nie może wykonać `run_shell`. |

### Zależności

- **Faza 1** (Agent Registry) — identity dla permission lookup.
- **Faza 2** (AgentWizard) — initial permissions w blueprint.

---

## Faza 8 — Resource & Cost Governance (v0.10.0)
**Cel**: budżetowanie, rate limiting, zarządzanie GPU/VRAM dla wielu agentów.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 8.1 | `BudgetManager` (application) | Per-agent, per-task, per-session limity kosztów. `check_budget(agent_id, estimated_cost) -> bool`. Blokowanie agenta po wyczerpaniu budżetu. Alerty przy 80% i 100%. |
| 8.2 | `RateLimiter` (infrastructure) | Token bucket per backend: respektuje RPM/TPM limity API. Wspólny dla wszystkich agentów korzystających z tego samego backendu. Backoff z retry. |
| 8.3 | `VRAMScheduler` (infrastructure) | Rozszerzenie istniejącego `VRAMAdvisor`. Gdy wielu agentów Ollama → kolejkuje requesty, priorytetyzuje wg task priority. Eviction policy: idle agent traci slot first. |
| 8.4 | `QuotaPolicy` (domain) | Konfigurowalny: `{agent_role: {daily_token_limit, daily_cost_limit, max_requests_per_hour}}`. Plik YAML. |
| 8.5 | Komendy TUI | `/budget status`, `/budget set <agent> <limit_usd>`, `/quota status`. |
| 8.6 | Testy | Unit: BudgetManager (threshold alerts, blocking), RateLimiter (token bucket, backoff), VRAMScheduler (priority queue). |
| 8.7 | Integracja z Dashboard | Rozszerzenie AlertManager (Faza 4) o alerty kosztowe: cost > budget, 80% threshold. Nowy panel w DashboardUI: cost breakdown per agent. |

### Zależności

- **Faza 1** (Agent Registry) — per-agent tracking.
- **Faza 4** (Dashboard) — integracja alertów kosztowych.
- UsageTracker (v0.2.0) — rozszerzenie do per-agent cost tracking.

---

## Faza 9 — Evaluation & Quality Framework (v0.11.0)

**Cel**: systematyczna ocena jakości agentów z benchmarkami, A/B testami i regression detection.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 9.1 | `EvalRubric` (domain) | Konfigurowalny zestaw kryteriów oceny: `correctness`, `completeness`, `style`, `tool_efficiency`, custom criteria. Wagi per kryterium. Format YAML. |
| 9.2 | `EvalRunner` (application) | Uruchamia agent na zestawie zadań testowych, zbiera odpowiedzi, ocenia wg rubric (LLM-as-judge + heurystyki). Raport: score per kryterium, aggregate. |
| 9.3 | `BenchmarkSuite` (infrastructure) | Predefiniowane zestawy zadań per kategoria: code_generation, code_review, research, planning. Ładowane z `benchmarks/<category>/*.yaml`. |
| 9.4 | `ABTestRunner` (application) | Porównanie dwóch konfiguracji agenta (różny model, prompt, skills) na identycznym zestawie zadań. Raport: win/loss/tie per task, aggregate score delta. |
| 9.5 | `RegressionDetector` (application) | Porównanie wyników aktualnego eval z baseline (zapisany poprzedni run). Alert jeśli score spadła > threshold. |
| 9.6 | `HumanFeedbackCollector` (interfaces) | Sponsor ocenia wynik agenta (thumbs up/down + komentarz). Feedback zapisywany → do przyszłego fine-tuningu promptów. |
| 9.7 | Komendy TUI | `/eval run <agent> [--benchmark X]`, `/eval compare <agent_a> <agent_b>`, `/eval history <agent>`. |
| 9.8 | Testy | Unit: EvalRubric (scoring logic), ABTestRunner (comparison), RegressionDetector (threshold). Integration: pełny eval flow z mock agentem. |

### Zależności

- **Faza 1** (Agent Registry).
- **Faza 2** (AgentWizard) — test scenarios z blueprint.

---

## Faza 10 — External Integration & API (v0.12.0)

**Cel**: programowe sterowanie frameworkiem i integracja z zewnętrznymi systemami.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 10.1 | `RESTServer` (infrastructure) | HTTP API: `POST /agents` (create), `GET /agents`, `POST /tasks`, `GET /tasks/{id}`, `POST /workflows/run`, `GET /metrics`. Auth: bearer token. |
| 10.2 | `WebhookDispatcher` (infrastructure) | Konfigurowalny: na zdarzenia (task_done, agent_error, budget_alert) → HTTP POST na zarejestrowane URL-e. Retry z backoff. |
| 10.3 | `PluginLoader` (application) | Dynamiczne ładowanie narzędzi/skills z zewnętrznych pakietów Python (`entry_points`). `amiagi plugin install <package>`, `amiagi plugin list`. |
| 10.4 | `CIAdapter` (infrastructure) | GitHub Actions integration: agent jako step w workflow. `amiagi ci review --pr <nr>` — code review PR. `amiagi ci test --suite X` — uruchomienie benchmarku. |
| 10.5 | `SDKClient` (package) | Python SDK: `from amiagi.sdk import AmiagiClient; client = AmiagiClient("http://..."); client.create_agent(...)`. Publikowalny na PyPI jako `amiagi-sdk`. |
| 10.6 | Testy | Unit: RESTServer (endpoint routing, auth), WebhookDispatcher (retry logic). Integration: SDK → REST → agent creation → task execution. |

### Zależności

- Wszystkie wcześniejsze fazy (API eksponuje pełną funkcjonalność).

---

## Faza 11 — Persona & Team Composition (v1.0.0)

**Cel**: przejście od "zbioru agentów" do "zarządzanego zespołu" z rolami, strukturą i dynamicznym skalowaniem.

### Deliverables

| # | Komponent | Opis |
|---|-----------|------|
| 11.1 | `TeamDefinition` (domain) | Dataclass: `team_id`, `name`, `members: list[AgentDescriptor]`, `lead_agent_id`, `workflow: str`, `project_context: str`. Format YAML. |
| 11.2 | `TeamComposer` (application) | Na podstawie opisu projektu (od Sponsora) → rekomenduje skład zespołu: ile agentów, jakie role, jakie skills. Korzysta z LLM + heurystyk (złożoność zadania → rozmiar zespołu). |
| 11.3 | `SkillCatalog` (application) | Centralny rejestr umiejętności z metadanymi: `{skill_name, description, required_tools, min_context_tokens, compatible_models, difficulty_level}`. Skills matchowane do agentów automatycznie. |
| 11.4 | `DynamicScaler` (application) | Monitoruje obciążenie zespołu. Task queue rośnie → scaler proponuje powołanie dodatkowego agenta (temporary). Task done → agent retirement (zwolnienie zasobów). |
| 11.5 | `TeamDashboard` (interfaces) | Rozszerzenie web dashboard: widok per-team. Org chart agentów. Communication flow (kto z kim rozmawia). Team metrics (throughput, cost, quality). |
| 11.6 | Predefiniowane team templates | `team_backend.yaml` — architect + 2 devs + reviewer + tester. `team_research.yaml` — searcher + analyst + writer. `team_fullstack.yaml` — backend + frontend + qa + devops. |
| 11.7 | Komendy TUI | `/team create <template\|wizard>`, `/team list`, `/team status <id>`, `/team scale <id> +1` / `/team scale <id> -1`. |
| 11.8 | Testy | Unit: TeamComposer (recommendation logic), DynamicScaler (scale up/down triggers), SkillCatalog (matching). Integration: team creation → task execution → scaling → completion. |

### Zależności

- Wszystkie wcześniejsze fazy.

### Kryteria akceptacji v1.0.0

- [ ] Sponsor mówi "zbuduj mi aplikację X" → framework powołuje zespół, dekomponuje zadanie, przydziela, nadzoruje, raportuje.
- [ ] Wielu agentów działa równolegle z izolowanymi uprawnieniami.
- [ ] Dashboard pokazuje cały system w real-time.
- [ ] Koszty kontrolowane budżetem per-agent.
- [ ] Jakość mierzona automatycznymi benchmarkami.
- [ ] System dostępny przez TUI, web dashboard i REST API.

---

## Podsumowanie faz

| Faza | Wersja | Klucz. deliverable | Status | Priorytet |
|------|--------|---------------------|--------|----------|
| 1. Agent Registry & Lifecycle | v0.6.0 | Dynamiczny rejestr N agentów | ✅ DONE | — |
| 2. AgentWizard | v0.6.0 | Sponsor opisuje → agent powstaje | ✅ DONE | — |
| 3. Task Queue & Work Distribution | v0.6.0 | Multi-task z priorytetami i DAG | ✅ DONE | — |
| 4. Observability & Dashboard | v0.6.0 | Web dashboard + trace viewer | ✅ DONE | — |
| 5. Shared Context & Memory | v0.9.0 | Baza wiedzy + cross-agent memory | ✅ DONE | — |
| 7. Security & Isolation | v0.9.0 | Per-agent permissions + sandbox | ✅ DONE | — |
| 6. Workflow Engine (DAG) | v0.9.0 | Deklaratywne przepływy pracy | ✅ DONE | — |
| **8. Resource & Cost Governance** | **v0.10.0** | **Budżety, rate limiting, VRAM scheduler** | następna | **★ 1 Średni** |
| **9. Evaluation & Quality** | **v0.11.0** | **Benchmarki, A/B testing, regression** | planowana | **★ 2 Średni** |
| 10. External Integration & API | v0.12.0 | REST API, webhooks, SDK, CI | planowana | ○ 3 Niższy |
| 11. Persona & Team Composition | v1.0.0 | Zarządzane zespoły + dynamic scaling | planowana | ◆ Capstone |

---

## Estymacja (orientacyjna)

Przy założeniu pracy iteracyjnej (implementacja + testy + dokumentacja per faza):

- Fazy 1–4: ✅ **ZREALIZOWANE** w v0.6.0 (464 testy, 21 modułów)
- Fazy 5+7+6: ✅ **ZREALIZOWANE** w v0.9.0 (612 testów, 34 modułów łącznie)
- Fazy 8–9: governance + ewaluacja (~2 release'y)
- Fazy 10–11: integracja + finalizacja (~2 release'y)

Pozostało: **4 wersje minor** od v0.10.0 do v1.0.0.

---

## Różnicowanie od konkurencji

Co sprawia, że amiagi po v1.0.0 będzie **lepszy** niż CrewAI / AutoGen / LangGraph:

| Cecha | CrewAI/AutoGen/LangGraph | amiagi v1.0.0 |
|---|---|---|
| Nadzór | Brak lub basic delegation | Głęboki, wielopoziomowy (Kastor + rubric + human feedback) |
| Bezpieczeństwo | Brak / globalne | Per-agent permissions + sandbox + audit chain |
| Audyt | Minimal | Pełny JSONL + trace viewer + session replay |
| AgentWizard | Brak — agenty definiuje programista | Sponsor opisuje potrzebę → agent tworzony automatycznie |
| Cost governance | Brak lub basic | Per-agent budżety, alerty, quotas |
| Skill system | Tools only | Markdown skills + dynamiczne generowanie + catalog |
| Quality | Brak systematycznej ewaluacji | Rubric scoring + benchmarki + A/B + regression detection |
| UI | Kod only lub basic chat | TUI + web dashboard + REST API |

**Kluczowe USP amiagi**: framework, który nie tylko orkiestruje agentów, ale **aktywnie nadzoruje jakość, bezpieczeństwo i koszty** — jak SOC/DevOps platform dla agentów AI.
