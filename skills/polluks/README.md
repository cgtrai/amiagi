# Skills — Polluks (wykonawca)

Ten folder zawiera pliki Markdown opisujące dodatkowe umiejętności
dla aktora **Polluks** (model wykonawczy).

Skills są ładowane automatycznie **tylko** gdy Polluks działa przez API
(np. OpenAI gpt-5.3-codex). Modele lokalne Ollama mają ograniczony
kontekst, więc dodatkowe instrukcje nie są do nich dołączane.

## Format pliku

Każdy plik `.md` to osobny skill. Nazwa pliku (bez rozszerzenia) staje
się identyfikatorem skilla, np. `web_research.md` → skill `web_research`.

## Przykład

```markdown
# Web Research

Gdy otrzymujesz polecenie wyszukania informacji w internecie:
1. Użyj narzędzia search_web z precyzyjnym zapytaniem
2. Przeanalizuj wyniki i zidentyfikuj najistotniejsze źródła
3. Użyj fetch_web aby pobrać pełną treść kluczowych stron
```
