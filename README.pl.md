# amiagi

[![CI](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml/badge.svg)](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml)

Lokalny framework CLI do oceny autonomii modeli LLM w kontrolowanym środowisku.

`amiagi` służy do prowadzenia powtarzalnych eksperymentów autonomii: wywołania narzędzi, polityki zgód, pełny audyt I/O modeli, ciągłość sesji i nadzór wykonania. Obsługuje zarówno lokalne modele Ollama, jak i zewnętrzne API (OpenAI, OpenRouter, Azure, vLLM) z przypisaniem modeli per rola.

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

### Rekomendowana dla użytkownika GitHub (automatyczne utworzenie virtualenv)

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

Jeśli używasz lokalnego `.venv`, najpierw aktywuj środowisko:

```bash
source .venv/bin/activate
```

Jeśli używasz Conda, najpierw aktywuj swoje środowisko:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate <twoja_nazwa_env>
```

Preferowana komenda:

```bash
amiagi
```

Komenda kompatybilna wstecz:

```bash
amiagi
```

Alternatywnie:

```bash
python -m main
```

Przydatne tryby:

```bash
python -m main --cold_start
python -m main --auto
python -m main --cold_start --auto
```

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
Najnowsze release notes: [RELEASE_NOTES_v0.2.0.md](RELEASE_NOTES_v0.2.0.md).
