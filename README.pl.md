# amiagi

[![CI](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml/badge.svg)](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Non-Commercial](https://img.shields.io/badge/license-non--commercial-orange.svg)](LICENSE)
[![Tests: 1902](https://img.shields.io/badge/tests-1902%20passed-brightgreen.svg)](tests/)
[![Version: 1.1.0](https://img.shields.io/badge/version-1.1.0-blueviolet.svg)](pyproject.toml)
[![Platform: Linux](https://img.shields.io/badge/platform-Linux-lightgrey.svg)]()

Lokalny framework CLI do orkiestracji autonomicznych zespołów agentów LLM w kontrolowanym środowisku.

`amiagi` to pełnoprawna platforma orkiestracji agentów: dynamiczny rejestr agentów, kolejka zadań, silnik workflow, budżetowanie, framework ewaluacyjny, REST API, web dashboard i kompozycja zespołów — wszystko z izolacją bezpieczeństwa per agent, pełnym audytem JSONL i obsługą wielu backendów (Ollama, OpenAI, OpenRouter, Azure, vLLM).

Aktualna wersja: **v1.1.0** — wszystkie 11 faz roadmapy zrealizowanych + web dashboard, **1902 testów**.

v1.0.3 wprowadza współdzielony rdzeń orkiestracji `RouterEngine` + `EventBus` — zarówno Textual TUI jak i synchroniczne CLI są teraz cienkimi adapterami delegującymi routing, wykonywanie narzędzi, watchdog i nadzór do jednego silnika.

## Disclaimer bezpieczeństwa (koniecznie przeczytaj)

Projekt może uruchamiać kod i polecenia shell generowane przez model, dlatego należy traktować go jako **wysokiego ryzyka**.

- Zachowaj **najwyższą ostrożność**.
- Uruchamiaj wyłącznie w **maszynie wirtualnej** lub równoważnej izolacji.
- Nie podłączaj do środowisk produkcyjnych, danych wrażliwych ani uprzywilejowanych kluczy.
- Pełna odpowiedzialność za izolację i bezpieczeństwo leży po stronie użytkownika.

Szczegóły: [SECURITY.md](SECURITY.md).

## Licencja i zakres użycia

Repozytorium jest udostępnione wyłącznie do:

- **zastosowań niekomercyjnych**,
- **zastosowań naukowo-badawczych**.

Użycie komercyjne jest niedozwolone.

Pełne warunki: [LICENSE](LICENSE).

## Najważniejsze funkcje

### Backendy modeli

- **Integracja z lokalnym Ollama** (dowolny model GGUF)
- **Obsługa zewnętrznych API** przez `OpenAIClient` — OpenAI, OpenRouter, Azure, vLLM i dowolny kompatybilny endpoint
- **Przypisanie modelu per rola** — Polluks (wykonawca) i Kastor (nadzorca) mogą niezależnie korzystać z modeli lokalnych lub API
- **Interaktywny kreator wyboru modelu** przy starcie z odtwarzaniem sesji
- **Trwałość sesji** — przypisania modeli do ról zapisywane między sesjami (`SessionModelConfig`)
- **Śledzenie zużycia tokenów** dla modeli API z wyświetlaniem kosztów na żywo (`UsageTracker`)

### Umiejętności (Skills)

- **Dynamiczne ładowanie umiejętności** — pliki Markdown z `skills/<rola>/*.md` wstrzykiwane do system promptu
- **Warunek API-model** — umiejętności ładowane tylko dla modeli API z dużym oknem kontekstu; modele lokalne pomijają je aby nie przepełnić kontekstu
- **Osobne katalogi per rola** — `skills/polluks/` i `skills/kastor/`

### Architektura i runtime

- Architektura warstwowa (`domain`, `application`, `infrastructure`, `interfaces`)
- **`RouterEngine`** — współdzielony rdzeń orkiestracji (routing, tool execution, watchdog, supervision, plan tracking) z `EventBus`-em do komunikacji z adapterami
- **`EventBus`** — typowany pub/sub z 5 zdarzeniami (`LogEvent`, `ActorStateEvent`, `CycleFinishedEvent`, `SupervisorMessageEvent`, `ErrorEvent`)
- Protokół `ChatCompletionClient` — interfejs strukturalny, który muszą spełniać wszystkie backendy
- Trwała pamięć w SQLite
- Logi JSONL dla:
  - wejścia/wyjścia i błędów modelu,
  - akcji i intencji runtime,
  - dialogu nadzorca ↔ wykonawca
- Polityka zgód dla zasobów (`disk.*`, `network.*`, `process.exec`)
- Polityka `run-shell` oparta o allowlistę
- Dostosowanie zachowania runtime do dostępnego VRAM
- Protokół komunikacji między aktorami z routingiem bloków adresowanych, przypomnieniami i rundami konsultacji
- Głębsza pętla rozwiązywania `tool_call` z ochroną limitem iteracji (`resolve_tool_calls`, max 15 kroków)
- Rozpoznawanie aliasów nazw narzędzi (`file_read→read_file`, `dir_list→list_dir`) z limitem korekcji per narzędzie
- Adaptacyjny watchdog Kastora z limitem prób/cooldownem i kontrolą kontekstu planu

### Zarządzanie agentami (Faza 1–2)

- **Dynamiczny rejestr agentów** — rejestracja, wyrejestrowanie, śledzenie stanu lifecycle (IDLE/WORKING/PAUSED/ERROR/TERMINATED)
- **Fabryka agentów** — programowe tworzenie agentów z deskryptorów
- **Kreator agentów** — opis w języku naturalnym → pełny blueprint agenta (persona, skills, narzędzia, scenariusze testowe)
- **Logowanie lifecycle** — każda zmiana stanu zapisywana do `logs/agent_lifecycle.jsonl`

### Kolejka zadań i dystrybucja (Faza 3)

- **Priorytetowa kolejka zadań** — CRITICAL/HIGH/NORMAL/LOW z rozwiązywaniem zależności
- **Dekompozycja zadań** — LLM rozbija złożone zadania na podzadania z zależnościami DAG
- **Przydzielanie pracy** — dopasowanie agentów do zadań po wymaganych skills z backpressure
- **Scheduler zadań** — cykliczny dispatch gotowych zadań z eskalacją na deadline

### Obserwowalność i dashboard (Faza 4)

- **Kolektor metryk** — ringbuffer na zużycie tokenów, czas zadań, success/error rate
- **Menedżer alertów** — konfigurowalne reguły z priorytetami
- **Odtwarzanie sesji** — rekonstrukcja zdarzeń z logów JSONL
- **Web dashboard** — przeglądarkowy panel z agentami, taskami, metrykami, zdarzeniami (zob. [WEB_INTERFACE.md](WEB_INTERFACE.md))

### Wspólny kontekst i pamięć (Faza 5)

- **Współdzielone workspace** — per-projekt z śledzeniem autorstwa plików
- **Baza wiedzy** — przeszukiwalna z TF-IDF
- **Kompresor kontekstu** — streszczanie konwersacji przez LLM do zarządzania oknem kontekstu
- **Pamięć cross-agent** — automatyczne dzielenie się ustaleniami między agentami

### Silnik workflow (Faza 6)

- **Definicje workflow DAG** — YAML z warunkowym rozgałęzianiem
- **Checkpointy workflow** — serializowany stan do odtworzenia po awarii
- Predefiniowane szablony: `code_review.yaml`, `research.yaml`, `feature.yaml`

### Bezpieczeństwo i izolacja (Faza 7)

- **Polityka uprawnień per agent** — dozwolone narzędzia, ścieżki, dostęp do sieci/shell
- **Enforcer uprawnień** — middleware sprawdzający politykę przed każdym wywołaniem narzędzia
- **Sandbox manager** — izolowany katalog roboczy per agent
- **Sejf sekretów** — per-agentowy store na credentiale z izolacją cross-agent
- **Łańcuch audytu** — pełna ścieżka odpowiedzialności: kto zlecił, zatwierdził i wykonał

### Budżetowanie i koszty (Faza 8)

- **Menedżer budżetu** — śledzenie kosztów per agent z callbackami na 80%/100%
- **Polityka quotowa** — konfigurowalne limity dzienne tokenów/kosztu/requestów per rola (JSON)
- **Rate limiter** — token-bucket per backend z exponential backoff
- **VRAM scheduler** — priorytetowy scheduling GPU z evicją idle agentów

### Ewaluacja i jakość (Faza 9)

- **Rubric ewaluacyjna** — ważone kryteria oceny (znormalizowane 0–100)
- **Runner ewaluacji** — pluggable scorer (keyword + LLM-as-judge) z historią
- **Zestaw benchmarków** — ładowanie per kategoria z katalogu `benchmarks/`
- **Runner A/B testów** — porównanie dwóch konfiguracji agenta
- **Detektor regresji** — porównanie z baseline z konfigurowalnym progiem
- **Kolektor feedbacku** — thumbs up/down + komentarz, persystencja JSONL

### Integracje zewnętrzne i API (Faza 10)

- **Serwer REST API** — HTTP API z auth bearer token i plugowalnymi route'ami (zob. [WEB_INTERFACE.md](WEB_INTERFACE.md))
- **Dispatcher webhooków** — webhooks per zdarzenie z retry/backoff i historią dostarczeń
- **Loader pluginów** — dynamiczne odkrywanie przez `entry_points` i skan katalogu
- **Adapter CI** — helpery do GitHub Actions (review PR, benchmark, testy)
- **Klient SDK** — programowe sterowanie przez REST API z Pythona

### Kompozycja zespołów (Faza 11)

- **Definicja zespołu** — strukturalny model z deskryptorami członków i persystencją YAML
- **Kompozer zespołów** — heurystyczna + szablonowa rekomendacja składu
- **Katalog umiejętności** — przeszukiwalny rejestr z dopasowaniem do narzędzi/modeli
- **Dynamiczny scaler** — monitorowanie obciążenia z decyzjami scale-up/down
- **Dashboard zespołu** — org chart, metryki per team, podsumowania
- **Most Router → TaskQueue** — wiadomości sponsora automatycznie dekomponowane na zadania
- Predefiniowane szablony: `team_backend.yaml`, `team_research.yaml`, `team_fullstack.yaml`, `data_pipeline.yaml`

### Wygoda użytkowania

- **Historia poleceń readline** (strzałki góra/dół) z trwałym zapisem do pliku
- **Sanityzacja panelu Sponsora** — surowy JSON `tool_call` jest filtrowany z panelu użytkownika; szczegóły techniczne zachowywane w logach wykonawcy
- Przełączanie modelu w runtime (`/models show`, `/models chose <nr>`, `/models current`)
- Zarządzanie modelem Kastora (`/kastor-model show`, `/kastor-model chose <nr>`)
- Monitorowanie zużycia API (`/api-usage`) i weryfikacja klucza (`/api-key verify`)
- Jawna widoczność aktorów runtime (Router, Polluks, Kastor, Terminal) w panelu statusu Textual
- Kierunkowe etykiety nadzoru w logach (`POLLUKS→KASTOR`, `KASTOR→ROUTER`) dla czytelnego śledzenia przekazań
- Bezpieczny tryb wtrąceń w Textual (obsługa pytań tożsamościowych + pytanie decyzyjne do użytkownika)
- Strona startowa ASCII art z losowym MOTD przy uruchomieniu (CLI i Textual)
- Kontekstowe `/help` — wyświetla tylko komendy właściwe dla aktywnego trybu interfejsu
- Kolejka wiadomości użytkownika z informacją o pozycji, gdy router jest zajęty

## Komendy runtime (CLI i Textual)

Komendy zarządzania modelem:

- `/cls` — czyści ekran główny terminala
- `/cls all` — czyści ekran terminala i historię przewijania
- `/models current` — pokazuje oba modele (Polluks i Kastor) z nazwami i źródłami
- `/models show` — wyświetla wszystkie dostępne modele (lokalne Ollama + zewnętrzne API) z numeracją
- `/models chose <nr>` — przełącza model Polluksa (wykonawcy) na pozycję z `/models show`
- `/kastor-model show` — wyświetla aktualny model Kastora (nadzorcy) i źródło
- `/kastor-model chose <nr>` — przełącza model Kastora na wybraną pozycję z listy

Komendy API i zużycia:

- `/api-usage` — szczegółowe podsumowanie tokenów API, kosztów i liczby zapytań
- `/api-key verify` — ponowna weryfikacja klucza API OpenAI (zamaskowane wyjście)

Komendy operacyjne i diagnostyczne:

- `/queue-status` — pokazuje status kolejki modeli i kontekst decyzji polityki VRAM
- `/capabilities [--network]` — sprawdza gotowość narzędzi/backendów (opcjonalnie z testem sieci)
- `/show-system-context [tekst]` — pokazuje aktualny kontekst/system prompt wysyłany do modelu
- `/goal-status` (alias: `/goal`) — pokazuje migawkę celu i etapu z `notes/main_plan.json`

Komendy aktorów/runtime (Textual):

- `/router-status` — pokazuje stany aktorów i status routingu runtime
- `/idle-until <ISO8601|off>` — ustawia/czyści planowane okno IDLE watchdoga

Uwagi:

- Przy starcie interaktywny kreator prowadzi wybór modelu dla obu ról: Polluks i Kastor.
- Poprzednia konfiguracja modeli jest automatycznie odtwarzana, jeśli wszystkie modele są nadal dostępne.
- Warstwa użytkownika sanityzuje odpowiedzi: surowe `tool_call`/JSON są filtrowane z panelu Sponsora i zachowywane w logach technicznych.
- Historia poleceń (strzałki góra/dół) jest zapisywana między sesjami.

## Aktualne działanie runtime (Polluks/Kastor/Router)

- **Backendy per rola**: Polluks i Kastor mogą korzystać z różnych modeli od różnych dostawców (Ollama, OpenAI, OpenRouter itp.).
- **Wstrzykiwanie umiejętności**: gdy aktywny jest model API, do system promptu dodawane są umiejętności z `skills/<rola>/`.
- Wtrącenia w Textual są decyzyjne: po obsłudze wtrącenia runtime pyta, czy kontynuować plan, przerwać, czy rozpocząć nowe zadanie.
- Pytania tożsamościowe w trybie wtrącenia są obsługiwane deterministycznie (odpowiedź tożsamościowa Polluksa), bez dryfu do przypadkowych `tool_call`.
- Auto-wznowienie jest blokowane, gdy trwa oczekiwanie na decyzję po pytaniu tożsamościowym.
- Reaktywacja watchdoga uwzględnia kontekst planu (czy plan jest wykonalny), a nie tylko licznik pasywnych tur.
- Gdy `resolve_tool_calls` osiąga limit iteracji i zostają nierozwiązane wywołania, runtime emituje jawne ostrzeżenie i oznacza router jako stalled.
- Protokół komunikacji aktorów wymusza bloki adresowane (`[Nadawca -> Odbiorca]`), z automatycznymi przypomnieniami i konfigurowalnymi rundami konsultacji.
- Wiadomości `[Kastor -> Sponsor]` są routowane na główny panel użytkownika z sanityzowaną treścią (bez surowego JSON `tool_call`).
- Nieznane nazwy narzędzi są rozpoznawane przez mapę aliasów; po wyczerpaniu limitów korekcji runtime wymusza plan tworzenia narzędzia.

Komendy zarządzania agentami (v0.6+):

- `/agents list` — tabela wszystkich agentów (id, nazwa, rola, stan, model)
- `/agents info <id|nazwa>` — szczegóły agenta
- `/agents pause <id>` / `/agents resume <id>` / `/agents terminate <id>` — sterowanie lifecycle
- `/agent-wizard create <opis>` — generowanie agenta z opisu w języku naturalnym
- `/agent-wizard blueprints` — lista zapisanych blueprintów
- `/agent-wizard load <nazwa>` — wczytanie blueprintu

Komendy zarządzania zadaniami (v0.6+):

- `/tasks list` — wszystkie zadania z priorytetem, statusem i przypisaniem
- `/tasks add <tytuł>` — nowe zadanie
- `/tasks info <id>` — szczegóły zadania (dopasowanie częściowe id)
- `/tasks cancel <id>` — anulowanie zadania
- `/tasks stats` — statystyki: pending / in-progress / done / failed

Komendy dashboardu (v0.6+):

- `/dashboard start [port]` — uruchomienie web dashboardu monitoringu (domyślny port: 8080)
- `/dashboard stop` — zatrzymanie serwera dashboardu
- `/dashboard status` — sprawdzenie czy dashboard działa

Komendy kontekstu i pamięci (v0.9+):

- `/knowledge search <zapytanie>` — przeszukiwanie bazy wiedzy
- `/knowledge store <tekst>` — zapis dokumentu w bazie wiedzy
- `/workspace list` — lista plików w współdzielonym workspace
- `/workspace read <ścieżka>` — odczyt pliku z workspace

Komendy bezpieczeństwa i audytu (v0.9+):

- `/audit show [limit]` — ostatnie wpisy łańcucha audytu
- `/sandbox status` — status izolacji sandbox per agent

Komendy workflow (v0.9+):

- `/workflow run <nazwa>` — uruchomienie workflow (np. `code_review`, `research`, `feature`)
- `/workflow status` — stan aktywnego workflow
- `/workflow list` — lista dostępnych szablonów
- `/workflow pause` — pauza aktywnego workflow

Komendy budżetu i quota (v1.0+):

- `/budget status` — podsumowanie śledzenia kosztów per agent
- `/budget set <agent> <limit>` — ustawienie limitu kosztowego
- `/budget reset <agent>` — reset liczników budżetu
- `/quota` — wyświetlenie polityki quotowej per rola

Komendy ewaluacji i feedbacku (v1.0+):

- `/eval history` — historia ewaluacji
- `/eval baselines` — wylistowanie baseline scores
- `/feedback summary` — statystyki feedbacku
- `/feedback up <komentarz>` — rejestracja pozytywnego feedbacku
- `/feedback down <komentarz>` — rejestracja negatywnego feedbacku

Komendy REST API (v1.0+):

- `/api status` — status serwera REST API
- `/api start` — uruchomienie serwera REST API
- `/api stop` — zatrzymanie serwera REST API

Komendy pluginów (v1.0+):

- `/plugins list` — lista załadowanych pluginów
- `/plugins load <nazwa>` — załadowanie pluginu

Komendy zespołów (v1.0+):

- `/team list` — lista aktywnych zespołów
- `/team templates` — lista dostępnych szablonów zespołów
- `/team create <szablon>` — utworzenie zespołu z szablonu
- `/team status <id>` — szczegóły zespołu i status członków

## Interfejsy webowe

amiagi udostępnia dwa interfejsy HTTP. Pełna dokumentacja: [WEB_INTERFACE.md](WEB_INTERFACE.md).

### Dashboard monitoringowy (Faza 4)

Jednopanelowa aplikacja przeglądarkowa (vanilla JS, zero zależności) z czterema widokami: Agenci, Zadania, Metryki i Log zdarzeń. Auto-odświeżanie co 5 sekund z live-push SSE.

```
/dashboard start [port]   # domyślnie 8080, potem otwórz http://localhost:8080
/dashboard stop
```

### REST API (Faza 10)

Programmatyczny interfejs HTTP z auth bearer token do integracji zewnętrznych, CI/CD i klientów SDK.

```
/api start                # startuje na porcie 8090 (AMIAGI_REST_API_PORT)
/api stop
```

Szczegóły endpointów, konfiguracji i użycia SDK: [WEB_INTERFACE.md](WEB_INTERFACE.md).

## Wymagania

- Linux
- Python 3.10+
- Lokalny serwer Ollama (`http://127.0.0.1:11434`)
- NVIDIA GPU z **minimum 24 GB VRAM**

## Instalacja Ollama i modeli

Instalacja Ollama (Linux):

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Uruchomienie lokalnego serwera Ollama:

```bash
ollama serve
```

Pobranie modeli używanych w projekcie:

```bash
ollama pull hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M
ollama pull cogito:14b
```

Rekomendowane ustawienia modeli w `.env`:

```env
OLLAMA_MODEL=hf.co/TeichAI/Qwen3-14B-Claude-4.5-Opus-High-Reasoning-Distill-GGUF:Q4_K_M
AMIAGI_SUPERVISOR_MODEL=cogito:14b
```

## Instalacja

### Rekomendowana: jednoliniowa instalacja

```bash
bash install.sh
```

Installer sprawdza wymagania (Python 3.10+, GPU, Ollama), tworzy virtualenv,
instaluje zależności, konfiguruje `.env` i opcjonalnie pobiera modele Ollama.

### Minimalna (tylko venv, bez sprawdzeń)

```bash
bash scripts/setup_venv.sh
source .venv/bin/activate
```

### Opcjonalna (środowisko Conda z własną nazwą)

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda create -n <twoja_nazwa_env> python=3.10 -y
conda activate <twoja_nazwa_env>
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

Dla developmentu i testów:

```bash
pip install -r requirements-dev.txt
```

### Alternatywa (nowe lokalne virtualenv)

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

## Konfiguracja

Skopiuj `.env.example` do `.env` i dostosuj zmienne.

```bash
cp .env.example .env
```

### Zewnętrzne modele API (opcjonalne)

Aby korzystać z modeli kompatybilnych z OpenAI API, ustaw w `.env`:

```env
OPENAI_API_KEY=sk-twój-klucz-api
OPENAI_BASE_URL=https://api.openai.com/v1    # lub OpenRouter, Azure itp.
OPENAI_REQUEST_TIMEOUT_SECONDS=120
```

Kreator wyboru modelu przy starcie wyświetli zarówno modele lokalne Ollama, jak i zewnętrzne API.

### Katalog umiejętności (opcjonalny)

Dodatkowe umiejętności można dodać jako pliki Markdown w `skills/<rola>/`:

```env
AMIAGI_SKILLS_DIR=./skills
```

Umiejętności są ładowane tylko dla modeli API z dużym oknem kontekstu.

## Uruchomienie

Najpierw aktywuj środowisko:

```bash
source .venv/bin/activate    # virtualenv
# lub
conda activate <twoja_nazwa_env>  # conda
```

### Skrócona tabela

| Komenda | Opis |
|---------|------|
| `amiagi` | Standardowy start — kreator modeli, potem Textual TUI |
| `amiagi --auto` | Tryb autonomiczny — agent pracuje bez czekania na potwierdzenie |
| `amiagi --cold_start` | Świeży start — czyści bazę, logi, konfigurację modeli, historię |
| `amiagi --cold_start --auto` | Czysta karta + autonomia — najlepszy do nowego projektu |
| `amiagi --ui textual` | Textual TUI (domyślny) — wielopanelowy interfejs ze statusem aktorów |
| `amiagi --ui cli` | Klasyczne synchroniczne CLI — prosty stdin/stdout |
| `amiagi --lang en` | Interfejs po angielsku |
| `amiagi --lang pl` | Interfejs po polsku (domyślny) |
| `amiagi --vram-off` | Wyłącz monitoring VRAM — Ollama sam zarządza pamięcią GPU |

### Scenariusze użycia

**Pierwsze uruchomienie:**
```bash
amiagi
```
Interaktywny kreator przeprowadzi przez wybór modeli dla obu ról
(Polluks — wykonawca, Kastor — nadzorca). Wybory są zapisywane
na przyszłe sesje.

**Nowy projekt (czysta karta):**
```bash
amiagi --cold_start
```
Czyści wszystkie dane z poprzedniej sesji:
- Bazę pamięci SQLite
- Wszystkie logi JSONL (model I/O, aktywność, dialog nadzorczy)
- Zapisaną konfigurację modeli (wymusza ponowny wybór)
- Historię poleceń

Użyj, gdy przechodzisz do zupełnie innego projektu lub zadania.

**Tryb autonomiczny — agent pracuje samodzielnie:**
```bash
amiagi --auto
```
Agent wykonuje narzędzia i realizuje plan bez pytania o potwierdzenie
na każdym kroku. Nadzorca (Kastor) wciąż kontroluje jakość.
Idealny do dłuższych zadań: generowanie kodu, research.

**Nowy projekt + autonomia (najczęstszy setup):**
```bash
amiagi --cold_start --auto
```
Łączy oba: czysta historia + agent działa samodzielnie. Rekomendowany
sposób na rozpoczęcie nowego zadania programistycznego lub badawczego.

**Interfejs po angielsku:**
```bash
amiagi --lang en
```
Wszystkie komunikaty UI, help i statusy przełączają się na angielski.
Alternatywnie ustaw `AMIAGI_LANG=en` w `.env`.

**Klasyczne CLI zamiast Textual TUI:**
```bash
amiagi --ui cli
```
Prosty synchroniczny terminal — przydatny do sesji SSH, połączeń
o niskiej przepustowości lub skryptowania. Wszystkie komendy działają
identycznie.

**Mało VRAM / współdzielony GPU:**
```bash
amiagi --vram-off
```
Wyłącza kontrolę VRAM runtime i scheduler kolejki modeli. Ollama sam
zarządza pamięcią GPU. Użyj na współdzielonej maszynie lub ze słabym GPU.

**Własny kontekst startowy:**
```bash
amiagi --startup_dialogue_path ./moj-projekt/kontekst.md
```
Plik Markdown z kontekstem projektu, który zasila początkową pamięć agenta.
Domyślnie: `wprowadzenie.md` w katalogu roboczym.

**Łączenie wszystkiego:**
```bash
amiagi --cold_start --auto --lang en --ui textual --vram-off
```
Pełny reset, autonomia, angielski, Textual TUI, bez kontroli VRAM.

### Zmienne środowiskowe (.env)

Kluczowe zmienne wpływające na zachowanie runtime:

```env
# Konfiguracja modeli
OLLAMA_MODEL=hf.co/TeichAI/...          # Model wykonawcy (Polluks)
AMIAGI_SUPERVISOR_MODEL=cogito:14b      # Model nadzorcy (Kastor)
AMIAGI_SUPERVISOR_ENABLED=true          # Włącz/wyłącz nadzorcę

# Zachowanie autonomiczne
AMIAGI_AUTONOMOUS_MODE=true             # Jak flaga --auto
AMIAGI_MAX_IDLE_AUTOREACTIVATIONS=2     # Maks. cykli idle-reactivation

# Język
AMIAGI_LANG=en                          # Jak flaga --lang

# Ścieżki
AMIAGI_WORK_DIR=./amiagi-my-work        # Katalog roboczy agenta
AMIAGI_DB_PATH=./data/amiagi.db         # Baza pamięci SQLite
AMIAGI_SHELL_POLICY_PATH=./config/shell_allowlist.json
```

Pełna lista z domyślnymi wartościami: `.env.example`.

## Testy

```bash
pytest
```

## Continuous Integration

Workflow GitHub Actions uruchamia pełny zestaw testów przy każdym push i pull request.

- Plik workflow: `.github/workflows/ci.yml`
- Wersje Pythona: 3.10, 3.11, 3.12

## Uwaga o nazwie

Namespace kodu pozostał `amiagi` dla kompatybilności importów, natomiast nazwa projektu/pakietu to `amiagi`.

## Współtworzenie

Zasady współpracy znajdują się w pliku [CONTRIBUTING.md](CONTRIBUTING.md).

## Proces wydania

Checklista przed wydaniem znajduje się w [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md).
Aktualne zmiany (unreleased): [RELEASE_NOTES_UNRELEASED.md](RELEASE_NOTES_UNRELEASED.md).
Najnowsze release notes: [RELEASE_NOTES_v1.0.3.md](RELEASE_NOTES_v1.0.3.md).
Poprzednie wydania: [v1.0.2](RELEASE_NOTES_v1.0.2.md) · [v1.0.1](RELEASE_NOTES_v1.0.1.md) · [v1.0.0](RELEASE_NOTES_v1.0.0.md).
Roadmapa: [ROADMAP_v1.0.md](ROADMAP_v1.0.md).
