# amiagi

[![CI](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml/badge.svg)](https://github.com/cgtrai/amiagi/actions/workflows/ci.yml)

Lokalny framework CLI do oceny autonomii modeli LLM w kontrolowanym środowisku.

`amiagi` służy do prowadzenia powtarzalnych eksperymentów autonomii: wywołania narzędzi, polityki zgód, pełny audyt I/O modeli, ciągłość sesji i nadzór wykonania.

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

- Integracja z lokalnym Ollama
- Architektura warstwowa (`domain`, `application`, `infrastructure`, `interfaces`)
- Trwała pamięć w SQLite
- Logi JSONL dla:
  - wejścia/wyjścia i błędów modelu,
  - akcji i intencji runtime,
  - dialogu nadzorca ↔ wykonawca
- Polityka zgód dla zasobów (`disk.*`, `network.*`, `process.exec`)
- Polityka `run-shell` oparta o allowlistę
- Dostosowanie zachowania runtime do dostępnego VRAM
- Przełączanie modelu wykonawczego w runtime (`/models show`, `/models chose <nr>`, `/models current`)
- Automatyczne ustawienie modelu domyślnego (pierwszy model z lokalnej listy Ollama)
- Czytelniejsze odpowiedzi użytkownikowe (bez surowych payloadów `tool_call`/JSON)
- Spójne komendy modeli i onboarding zarówno w CLI, jak i w UI Textual
- Jawna widoczność aktorów runtime (Router, Polluks, Kastor, Terminal) w panelu statusu Textual
- Kierunkowe etykiety nadzoru w logach (`POLLUKS→KASTOR`, `KASTOR→ROUTER`) dla czytelnego śledzenia przekazań
- Bezpieczny tryb wtrąceń w Textual (obsługa pytań tożsamościowych + pytanie decyzyjne do użytkownika)
- Adaptacyjny watchdog Kastora z limitem prób/cooldownem i kontrolą kontekstu planu
- Głębsza pętla rozwiązywania `tool_call` z ochroną limitem iteracji (`resolve_tool_calls`, max 15 kroków)
- Protokół komunikacji między aktorami z routingiem bloków adresowanych, przypomnieniami i rundami konsultacji
- Rozpoznawanie aliasów nazw narzędzi (`file_read→read_file`, `dir_list→list_dir`) z limitem korekcji per narzędzie
- Strona startowa ASCII art z losowym MOTD przy uruchomieniu (CLI i Textual)
- Kontekstowe `/help` — wyświetla tylko komendy właściwe dla aktywnego trybu interfejsu
- Kolejka wiadomości użytkownika z informacją o pozycji, gdy router jest zajęty

## Komendy runtime (CLI i Textual)

Komendy zarządzania modelem:

- `/cls` — czyści ekran główny terminala
- `/cls all` — czyści ekran terminala i historię przewijania
- `/models current` — pokazuje aktywny model wykonawczy
- `/models show` — wyświetla listę modeli z lokalnego Ollama (z numeracją)
- `/models chose <nr>` — przełącza model wykonawczy na pozycję z `/models show`

Komendy operacyjne i diagnostyczne:

- `/queue-status` — pokazuje status kolejki modeli i kontekst decyzji polityki VRAM
- `/capabilities [--network]` — sprawdza gotowość narzędzi/backendów (opcjonalnie z testem sieci)
- `/show-system-context [tekst]` — pokazuje aktualny kontekst/system prompt wysyłany do modelu
- `/goal-status` (alias: `/goal`) — pokazuje migawkę celu i etapu z `notes/main_plan.json`

Komendy aktorów/runtime (Textual):

- `/router-status` — pokazuje stany aktorów i status routingu runtime
- `/idle-until <ISO8601|off>` — ustawia/czyści planowane okno IDLE watchdoga

Uwagi:

- Przy starcie CLI i Textual wyświetlają banner ASCII art z wersją, trybem i losowym MOTD.
- Runtime próbuje automatycznie ustawić domyślny model wykonawczy z listy Ollama.
- Gdy pobranie listy modeli się nie powiedzie, runtime pozostawia bieżący model bez komunikatu.
- Warstwa użytkownika dostaje odpowiedź tekstową; surowe ślady narzędzi pozostają w logach technicznych (JSONL/panele).

## Aktualne działanie runtime (Polluks/Kastor/Router)

- Wtrącenia w Textual są decyzyjne: po obsłudze wtrącenia runtime pyta, czy kontynuować plan, przerwać, czy rozpocząć nowe zadanie.
- Pytania tożsamościowe w trybie wtrącenia są obsługiwane deterministycznie (odpowiedź tożsamościowa Polluksa), bez dryfu do przypadkowych `tool_call`.
- Auto-wznowienie jest blokowane, gdy trwa oczekiwanie na decyzję po pytaniu tożsamościowym.
- Reaktywacja watchdoga uwzględnia kontekst planu (czy plan jest wykonalny), a nie tylko licznik pasywnych tur.
- Gdy `resolve_tool_calls` osiąga limit iteracji i zostają nierozwiązane wywołania, runtime emituje jawne ostrzeżenie i oznacza router jako stalled.
- Protokół komunikacji aktorów wymusza bloki adresowane (`[Nadawca -> Odbiorca]`), z automatycznymi przypomnieniami i konfigurowalnymi rundami konsultacji.
- Wiadomości `[Kastor -> Sponsor]` są routowane na główny panel użytkownika.
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
Najnowsze release notes: [RELEASE_NOTES_v0.1.4.md](RELEASE_NOTES_v0.1.4.md).
