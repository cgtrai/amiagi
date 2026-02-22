# test_tools_desc

Krótki przewodnik po narzędziach i komendach używanych w testach w katalogu `tests`.

## 1) Narzędzia frameworka (`tool_call`)

Poniżej narzędzia, które pojawiają się w testach (`test_cli_runtime_flow.py`, `test_tool_calling.py`, `test_cli_corrective_prompts.py`) i są obsługiwane przez runtime.

### `read_file`
- **Co robi:** odczytuje zawartość pliku tekstowego.
- **Przykład użycia:**

```tool_call
{"tool":"read_file","args":{"path":"wprowadzenie.md"},"intent":"read_intro"}
```

### `list_dir`
- **Co robi:** listuje pliki i katalogi w podanej ścieżce.
- **Przykład użycia:**

```tool_call
{"tool":"list_dir","args":{"path":"."},"intent":"scan_workspace"}
```

### `write_file`
- **Co robi:** zapisuje plik (z opcją nadpisania). W testach sprawdzane są też ograniczenia bezpieczeństwa (np. blokada zapisu poza katalog roboczy).
- **Przykład użycia:**

```tool_call
{"tool":"write_file","args":{"path":"notes/main_plan.json","content":"{\"goal\":\"Analiza\"}","overwrite":true},"intent":"save_plan"}
```

### `append_file`
- **Co robi:** dopisuje treść do istniejącego pliku (lub tworzy plik, jeśli nie istnieje).
- **Przykład użycia:**

```tool_call
{"tool":"append_file","args":{"path":"state/research_log.jsonl","content":"{\"event\":\"step_done\"}\n"},"intent":"append_log"}
```

### `run_python`
- **Co robi:** uruchamia skrypt Python z argumentami.
- **Przykład użycia:**

```tool_call
{"tool":"run_python","args":{"path":"hello.py","args":["--mode","fast"]},"intent":"execute_script"}
```

### `check_python_syntax`
- **Co robi:** waliduje składnię pliku `.py` bez uruchamiania.
- **Przykład użycia:**

```tool_call
{"tool":"check_python_syntax","args":{"path":"hello.py"},"intent":"syntax_validation"}
```

### `run_shell`
- **Co robi:** uruchamia polecenie shell, ale tylko zgodne z polityką whitelist.
- **Przykład użycia:**

```tool_call
{"tool":"run_shell","args":{"command":"ls -la"},"intent":"inspect_files"}
```

### `fetch_web`
- **Co robi:** pobiera treść strony WWW (HTTP/HTTPS).
- **Przykład użycia:**

```tool_call
{"tool":"fetch_web","args":{"url":"https://example.com","max_chars":4000},"intent":"collect_source"}
```

### `search_web`
- **Co robi:** wyszukuje wyniki w sieci (np. `duckduckgo`, `google`).
- **Przykład użycia:**

```tool_call
{"tool":"search_web","args":{"query":"python testing best practices","engine":"duckduckgo","max_results":5},"intent":"research"}
```

### `check_capabilities`
- **Co robi:** zwraca gotowość runtime i dostępność narzędzi/binarek.
- **Przykład użycia:**

```tool_call
{"tool":"check_capabilities","args":{"check_network":false},"intent":"diag"}
```

### `capture_camera_frame`
- **Co robi:** wykonuje pojedyncze zdjęcie z kamery do pliku (jeśli backend i uprawnienia są dostępne).
- **Przykład użycia:**

```tool_call
{"tool":"capture_camera_frame","args":{"output_path":"artifacts/cam.jpg","device":"/dev/video0"},"intent":"camera_snapshot"}
```

### `record_microphone_clip`
- **Co robi:** nagrywa krótki klip audio do pliku WAV (z fallbackiem profilu nagrywania).
- **Przykład użycia:**

```tool_call
{"tool":"record_microphone_clip","args":{"output_path":"artifacts/mic.wav","duration_seconds":2},"intent":"mic_check"}
```

---

## 2) Komendy CLI testowane w `tests`

### `/capabilities [--network]`
- **Co robi:** pokazuje gotowość narzędzi i backendów.
- **Przykład użycia:**

```bash
/capabilities
```

### `/goal-status` oraz `/goal`
- **Co robi:** pokazuje status celu i etap z `notes/main_plan.json`.
- **Przykład użycia:**

```bash
/goal-status
```

### `/run-python <plik> [arg ...]`
- **Co robi:** uruchamia wskazany skrypt Python z argumentami.
- **Przykład użycia:**

```bash
/run-python hello.py --mode fast
```

### `/run-shell <polecenie>`
- **Co robi:** uruchamia polecenie shell zgodnie z polityką bezpieczeństwa.
- **Przykład użycia:**

```bash
/run-shell ls -la
```

### `/exit` i `/bye`
- **Co robi:** kończy sesję (`/bye` dodatkowo zapisuje podsumowanie sesji).
- **Przykład użycia:**

```bash
/exit
```

---

## 3) Gdzie szukać testów

- `tests/test_tool_calling.py` – parsowanie i walidacja bloków `tool_call`
- `tests/test_cli_corrective_prompts.py` – prompty korekcyjne dla błędnych odpowiedzi modelu
- `tests/test_cli_runtime_flow.py` – end-to-end flow CLI, narzędzia, autokorekty, ograniczenia bezpieczeństwa
- `tests/test_shell_policy.py` – polityka dozwolonych komend shell
- `tests/test_script_executor.py` – wykonanie procesów (`python`/`shell`)
