# Skills — Kastor (nadzorca)

Ten folder zawiera pliki Markdown opisujące dodatkowe umiejętności
dla aktora **Kastor** (nadzorca odpowiedzi modelu wykonawczego).

Skills są ładowane automatycznie **tylko** gdy Kastor działa przez API
(np. OpenAI gpt-5.3-codex). Modele lokalne Ollama mają ograniczony
kontekst, więc dodatkowe instrukcje nie są do nich dołączane.

## Format pliku

Każdy plik `.md` to osobny skill. Nazwa pliku (bez rozszerzenia) staje
się identyfikatorem skilla, np. `code_review.md` → skill `code_review`.

## Przykład

```markdown
# Code Review

Gdy oceniasz odpowiedź Polluksa zawierającą kod:
1. Sprawdź poprawność składniową
2. Zweryfikuj obsługę wyjątków
3. Oceń czytelność i idiomatyczność
```
