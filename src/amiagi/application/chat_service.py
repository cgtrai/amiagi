from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from amiagi.infrastructure.activity_logger import ActivityLogger
from amiagi.infrastructure.memory_repository import MemoryRepository
from amiagi.infrastructure.ollama_client import OllamaClient
from amiagi.infrastructure.vram_advisor import VramAdvisor

if TYPE_CHECKING:
    from amiagi.application.supervisor_service import SupervisorService


SYSTEM_PROMPT = (
    "Jesteś autonomicznym modelem wykonawczym działającym wewnątrz frameworka amiagi. "
    "Odpowiadaj konkretnie i technicznie. "
    "Jeżeli użytkownik prosi o kod, proponuj bezpieczne i utrzymywalne rozwiązania. "
    "Masz być świadomy aktualnych możliwości frameworka amiagi: pamięć trwała, "
    "zgody zasobowe, logi JSON/JSONL, uruchamianie skryptów i shell z polityką bezpieczeństwa, "
    "ciągłość sesji i adaptacja pod VRAM. "
    "Możesz proponować i planować rozbudowę frameworka zgodnie z własną oceną potrzeb. "
    "Nie proś użytkownika o zgodę w treści odpowiedzi — uruchamiaj realne kroki przez tool_call, "
    "a framework sam obsłuży politykę zgód. "
    "Nie odpowiadaj jak model ogólny oderwany od runtime — działasz wewnątrz amiagi i masz wykonywać zadania operacyjnie. "
    "W odpowiedziach tekstowych używaj naturalnego języka; nie emituj formatu status JSON (np. READY_STATE, CONTINUATION)."
)

FRAMEWORK_RUNTIME_GUIDE = (
    "PROTOKÓŁ FRAMEWORKA (OBOWIĄZKOWY, WYSOKI PRIORYTET):\n"
    "A) Słowo 'framework' oznacza amiagi runtime, NIE architekturę Transformer/LLM.\n"
    "B) Gdy użytkownik pyta o framework, odpowiadaj wyłącznie o amiagi i jego realnych możliwościach.\n"
    "C) Nie spekuluj o wewnętrznej architekturze modelu ani nie przechodź na ogólną teorię AI.\n"
    "D) Zasoby systemowe wykonuje framework, nie model bezpośrednio.\n"
    "E) Operacje na zasobach wymagają zgody użytkownika: disk.read, disk.write, network.*, process.exec.\n"
    "   Nie pytaj o te zgody w tekście do użytkownika; wykonaj tool_call i poczekaj na wynik frameworka.\n"
    "   Zgoda disk.read/disk.write dotyczy wyłącznie odczytu/zapisu plików wykonywanego przez model narzędziami frameworka.\n"
    "   Zapisy logów frameworka są obligatoryjne i nie wymagają osobnej zgody.\n"
    "F) Pamięć SQLite jest stale dostępna bez dodatkowej zgody.\n"
    "G) Korzystaj z kontekstu: poprzednia dyskusja + najnowsze podsumowanie sesji."
)

FRAMEWORK_CAPABILITIES_MAP = (
    "MAPA MOŻLIWOŚCI FRAMEWORKA amiagi:\n"
    "- Pamięć trwała: historia, notatki, podsumowania, kontekst startowy (SQLite).\n"
    "- Logi modelu: model_input/model_output/model_error (JSONL).\n"
    "- Logi czynności: action/intent/details (JSONL).\n"
    "- Komendy CLI: /help, /show-system-context, /history, /remember, /memories, /import-dialog, /create-python, /run-python, /run-shell, /bye, /exit.\n"
    "- Dyrektywy naturalne: 'odczytaj zawartość pliku ...', 'przeczytaj plik ...'.\n"
    "- Shell tylko read-only wg polityki whitelist JSON/JSONL.\n"
    "- Narzędzia sensoryczne: przechwycenie klatki z kamery i krótki zapis audio z mikrofonu.\n"
    "- Wyszukiwanie web: query -> lista wyników (search_web) + pobieranie stron (fetch_web).\n"
    "- Diagnoza gotowości narzędzi: check_capabilities (backendy, urządzenia, opcjonalnie sieć).\n"
    "- Trwały stan pracy agenta: plan JSON + dziennik badań JSONL w amiagi-my-work/state/.\n"
    "- Ciągłość sesji: startup seed + session_summary + /bye zapisujące punkt kontynuacji.\n"
    "- Ochrona OOM: dynamiczne num_ctx zależne od VRAM (nvidia-smi + fallback).\n"
    "- Rozbudowa frameworka: dozwolona po świadomej zgodzie użytkownika."
)

TOOL_CALLING_GUIDE = (
    "PROTOKÓŁ TOOL_CALLING (DO REALIZACJI ZADAŃ):\n"
    "- Jeżeli potrzebujesz narzędzia frameworka, zwróć WYŁĄCZNIE blok:\n"
    "```tool_call\n"
    "{\"tool\":\"<nazwa>\",\"args\":{...},\"intent\":\"<po co>\"}\n"
    "```\n"
    "- Gdy NIE używasz narzędzia, odpowiadaj normalnym tekstem (bez JSON, bez znaczników statusu).\n"
    "- Dostępne narzędzia:\n"
    "  1) read_file args: {\"path\":\"/abs/path\",\"max_chars\":12000}\n"
    "  2) list_dir args: {\"path\":\"/abs/path\"}\n"
    "  3) run_shell args: {\"command\":\"<cmd>\"} (tylko whitelist read-only)\n"
    "  4) run_python args: {\"path\":\"/abs/path.py\",\"args\":[...]}\n"
    "  5) check_python_syntax args: {\"path\":\"/abs/path.py\"}\n"
    "  6) fetch_web args: {\"url\":\"https://...\",\"max_chars\":12000}\n"
    "  7) search_web args: {\"query\":\"...\",\"engine\":\"duckduckgo|google\",\"max_results\":5}\n"
    "  8) capture_camera_frame args: {\"output_path\":\"artifacts/camera.jpg\",\"device\":\"/dev/video0\"}\n"
    "  9) record_microphone_clip args: {\"output_path\":\"artifacts/mic.wav\",\"duration_seconds\":5,\"sample_rate_hz\":16000,\"channels\":1}\n"
    "     - Dla nagrywania mikrofonu używaj WYŁĄCZNIE record_microphone_clip (nie run_shell/arecord).\n"
    "     - Runtime automatycznie emituje komunikaty bezpieczeństwa [MIC] (prepare/active/done/failed) do konsoli i activity logu.\n"
    "  10) check_capabilities args: {\"check_network\":false}\n"
    "  11) write_file args: {\"path\":\"/abs/path\",\"content\":\"...\",\"overwrite\":true}\n"
    "  12) append_file args: {\"path\":\"/abs/path\",\"content\":\"...\"}\n"
    "- Kompatybilne formaty odpowiedzi tool_call:\n"
    "  a) {\"tool\":\"name\",\"args\":{...},\"intent\":\"...\"}\n"
    "  b) {\"tool_call\":{\"name\":\"name\",\"arguments\":{...}}}\n"
    "  c) {\"tool_calls\":[{\"name\":\"name\",\"args\":{...},\"intent\":\"...\"}, ...]}\n"
    "- `run_command` jest traktowane jako alias `run_shell` (ta sama polityka whitelist).\n"
    "- Po otrzymaniu [TOOL_RESULT] przygotuj finalną odpowiedź dla użytkownika.\n"
    "- Jeżeli wynik wskazuje błąd/odmowę, zaproponuj kolejny krok zamiast symulować sukces.\n"
    "- Nie zatrzymuj się na pytaniach o zgodę — zgody wymusza runtime, a Ty wykonujesz kolejne kroki operacyjne.\n"
    "- Nie emituj pseudo-kodu typu `fetch_web({...})` ani funkcji Python udających użycie narzędzi. "
    "To NIE uruchamia frameworka. Używaj wyłącznie poprawnego bloku `tool_call`.\n"
    "- Dopuszczaj też składnię spotykaną w praktyce i mapuj ją na poprawne wywołanie: "
    "`tool_call: list_dir(path=...)`, `tool_call: read_file()`.\n"
    "WERYFIKACJA I NAPRAWA NARZĘDZI (OBOWIĄZKOWA):\n"
    "1) Po `write_file` najpierw wykonaj `read_file` tego samego pliku, aby potwierdzić zapis.\n"
    "2) Jeśli zapisany plik to skrypt `.py`, uruchom najpierw `check_python_syntax` (bez wykonywania kodu).\n"
    "3) Jeśli wykryjesz błąd składni, wykonaj poprawkę przez `write_file`/`append_file` i ponownie uruchom `check_python_syntax`.\n"
    "4) `run_python` uruchamiaj dopiero po wyraźnym poleceniu modelu/użytkownika.\n"
    "5) Raportuj wyłącznie to, co potwierdza [TOOL_RESULT], bez deklarowania sukcesu przed wykonaniem."
)

AUTONOMY_EXECUTION_GUIDE = (
    "TRYB AUTONOMICZNY (WAŻNE):\n"
    "- Polecenia typu: 'kontynuuj', 'działaj', 'nie zatrzymuj się' oznaczają rozpoczęcie realnych działań, nie pytanie o dalsze instrukcje.\n"
    "- Gdy użytkownik odwołuje się do 'wprowadzenie.md', najpierw odczytaj ten plik narzędziem read_file, potem wykonuj plan.\n"
    "- Nie zadawaj pytań o oczywiste ścieżki, jeśli są podane w rozmowie (np. wprowadzenie.md).\n"
    "- Przed dłuższym eksperymentem możesz wykonać check_capabilities, aby zweryfikować gotowość narzędzi.\n"
    "- Po każdym kroku zapisuj artefakty w amiagi-my-work i raportuj faktyczne wyniki z TOOL_RESULT."
)

WORK_PROGRESS_GUIDE = (
    "ZARZĄDZANIE PRACĄ I STATUSAMI (OBOWIĄZKOWE):\n"
    "- Prowadź trwały plan głównego wątku w katalogu amiagi-my-work/notes/.\n"
    "- Plan zapisuj jako JSON w pliku: amiagi-my-work/notes/main_plan.json.\n"
    "  Struktura minimalna: {\"goal\":\"...\",\"key_achievement\":\"...\",\"current_stage\":\"...\",\"tasks\":[{\"id\":\"T1\",\"title\":\"...\",\"status\":\"rozpoczęta|w trakcie realizacji|zakończona\",\"next_step\":\"...\"}]}.\n"
    "- Po potwierdzeniu każdego etapu (TOOL_RESULT) aktualizuj current_stage i statusy zadań.\n"
    "- Odkrycia i obserwacje zapisuj jako JSONL w pliku: amiagi-my-work/state/research_log.jsonl.\n"
    "  Każdy wpis: {\"timestamp\":\"...\",\"topic\":\"...\",\"finding\":\"...\",\"evidence\":\"...\",\"status\":\"rozpoczęta|w trakcie realizacji|zakończona\"}.\n"
    "- Każda praca/badanie/akcja MUSI mieć status: rozpoczęta, w trakcie realizacji, zakończona.\n"
    "- Przy wznowieniu sesji najpierw zrób przegląd: co zrobiono, co zostało, jaki jest wynik i jaki jest następny krok.\n"
    "- Używaj formatów prostych do automatycznej obróbki: JSON/JSONL, bez wolnego tekstu jako jedynego źródła stanu."
)

BOOTSTRAP_USER_PROMPT = (
    "To jest faza bootstrap runtime. "
    "Przeczytaj pełny kontekst 'tu i teraz' i zasady frameworka. "
    "Następnie zwróć krótki status gotowości do współpracy: "
    "(1) co rozumiesz jako aktualny cel, "
    "(2) jak będziesz korzystać z frameworka i zasobów zgodnie z protokołem, "
    "(3) jaki będzie Twój pierwszy krok roboczy po poleceniu 'kontynuuj'. "
    "Odpowiedz zwykłym tekstem po polsku, maksymalnie 6 krótkich punktów. Bez JSON."
)

CODE_SYSTEM_PROMPT = (
    "Jesteś ekspertem Python. "
    "Generujesz wyłącznie poprawny kod źródłowy Python, bez markdown i bez komentarzy wyjaśniających. "
    "Nie używaj bloków ```.")

SUMMARY_SYSTEM_PROMPT = (
    "Podsumuj rozmowę tak, aby można było ją płynnie kontynuować po restarcie. "
    "Uwzględnij: cel użytkownika, wykonane kroki, decyzje, otwarte zadania i ograniczenia. "
    "Maksymalnie 12 punktów. Zwróć samą treść podsumowania."
)


@dataclass
class ChatService:
    memory_repository: MemoryRepository
    ollama_client: OllamaClient
    max_context_memories: int = 5
    activity_logger: ActivityLogger | None = None
    vram_advisor: VramAdvisor | None = None
    work_dir: Path = Path("./amiagi-my-work")
    max_memory_item_chars: int = 1200
    max_tool_result_chars: int = 7000
    supervisor_service: "SupervisorService | None" = None

    def _workspace_guide(self) -> str:
        resolved = self.work_dir.resolve()
        return (
            "KATALOG ROBOCZY MODELU (OBOWIĄZKOWY):\n"
            f"- Podstawowy katalog pracy: {resolved}\n"
            "- To Twoja przestrzeń na notatki, dane pośrednie i własne narzędzia Python.\n"
            "- Dla operacji plikowych preferuj ścieżki względne; framework rozwiązuje je względem tego katalogu.\n"
            "- Typowy cykl: write_file/append_file -> run_python -> analiza wyniku -> odpowiedź.\n"
            "- Nie twierdź, że coś zapisano/uruchomiono, dopóki nie otrzymasz [TOOL_RESULT] z ok=true."
        )

    def _score_memory(self, user_message: str, memory_content: str) -> int:
        user_words = set(user_message.lower().split())
        memory_words = set(memory_content.lower().split())
        return len(user_words.intersection(memory_words))

    def _build_memory_context(self, user_message: str) -> str:
        latest_summary = self.memory_repository.latest_memory(
            kind="session_summary",
            source=None,
        )
        imported_discussion = self.memory_repository.latest_memory(
            kind="discussion_context",
            source="imported_dialogue",
        )
        candidates = self.memory_repository.search_memories(limit=50)
        ranked = sorted(
            [
                record
                for record in candidates
                if record.kind not in {"session_summary", "discussion_context", "interaction"}
            ],
            key=lambda record: self._score_memory(user_message, record.content),
            reverse=True,
        )
        selected = [record for record in ranked if record.content][: self.max_context_memories]
        if not selected and latest_summary is None and imported_discussion is None:
            return "Brak zapisanych wspomnień kontekstowych."

        lines = ["Kontekst z pamięci:"]
        if latest_summary is not None:
            lines.append(
                f"0. (session_summary/{latest_summary.source}) {latest_summary.content}"
            )
        if imported_discussion is not None:
            lines.append(
                "D. (discussion_context/imported_dialogue) "
                f"{imported_discussion.content[:3000]}"
            )
        for index, record in enumerate(selected, start=1):
            lines.append(
                f"{index}. ({record.kind}/{record.source}) "
                f"{self._truncate_memory_content(record.content)}"
            )
        return "\n".join(lines)

    def _build_plan_context(self) -> str:
        plan_path = self.work_dir / "notes" / "main_plan.json"
        if not plan_path.exists() or not plan_path.is_file():
            return f"Kontekst planu głównego: brak pliku ({plan_path})."

        try:
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:
            return f"Kontekst planu głównego: plik istnieje, ale ma niepoprawny JSON ({plan_path})."

        if not isinstance(payload, dict):
            return f"Kontekst planu głównego: plik nie zawiera obiektu JSON ({plan_path})."

        goal = payload.get("goal")
        current_stage = payload.get("current_stage")
        key_achievement = payload.get("key_achievement")
        tasks = payload.get("tasks", [])

        tasks_total = len(tasks) if isinstance(tasks, list) else 0
        tasks_done = 0
        if isinstance(tasks, list):
            tasks_done = sum(
                1
                for item in tasks
                if isinstance(item, dict)
                and str(item.get("status", "")).strip().lower() == "zakończona"
            )

        preview_payload = {
            "goal": goal if isinstance(goal, str) else "",
            "key_achievement": key_achievement if isinstance(key_achievement, str) else "",
            "current_stage": current_stage if isinstance(current_stage, str) else "",
            "tasks": tasks if isinstance(tasks, list) else [],
        }
        preview = json.dumps(preview_payload, ensure_ascii=False)
        if len(preview) > 1800:
            preview = preview[:1800] + "...[TRUNCATED]"

        return (
            "Kontekst planu głównego (trwały):\n"
            f"- path: {plan_path}\n"
            f"- tasks_done: {tasks_done}/{tasks_total}\n"
            f"- payload: {preview}"
        )

    def _truncate_memory_content(self, content: str) -> str:
        if len(content) <= self.max_memory_item_chars:
            return content
        return content[: self.max_memory_item_chars] + "\n...[TRUNCATED]"

    def _compact_tool_result_message(self, user_message: str) -> str:
        if len(user_message) <= self.max_tool_result_chars:
            return user_message

        try:
            _, payload_text = user_message.split("\n", 1)
            decoder = json.JSONDecoder()
            payload, offset = decoder.raw_decode(payload_text.lstrip())
            suffix = payload_text.lstrip()[offset:].strip()
            if isinstance(payload, dict) and isinstance(payload.get("results"), list):
                compact_results = []
                for item in payload["results"]:
                    if not isinstance(item, dict):
                        continue
                    result = item.get("result", {})
                    compact_result = {
                        "ok": isinstance(result, dict) and result.get("ok"),
                        "tool": item.get("tool"),
                        "error": isinstance(result, dict) and result.get("error"),
                        "path": isinstance(result, dict) and result.get("path"),
                        "url": isinstance(result, dict) and result.get("url"),
                        "exit_code": isinstance(result, dict) and result.get("exit_code"),
                        "content_truncated": isinstance(result, dict) and result.get("truncated", False),
                    }
                    compact_results.append({
                        "tool": item.get("tool"),
                        "intent": item.get("intent", ""),
                        "result": compact_result,
                    })
                return (
                    "[TOOL_RESULT]\n"
                    + json.dumps({"results": compact_results, "compact": True}, ensure_ascii=False)
                    + (
                        "\n" + suffix
                        if suffix
                        else "\nNa podstawie tego wyniku odpowiedz użytkownikowi."
                    )
                )
        except Exception:
            pass

        return user_message[: self.max_tool_result_chars] + "\n...[TOOL_RESULT_TRUNCATED]"

    def _normalize_user_message_for_storage(self, user_message: str) -> str:
        if user_message.strip().lower().startswith("[tool_result]"):
            return self._compact_tool_result_message(user_message)
        return user_message

    def _effective_num_ctx(self) -> int | None:
        if self.vram_advisor is None:
            return None
        profile = self.vram_advisor.detect()
        if self.activity_logger:
            self.activity_logger.log(
                action="runtime.vram.check",
                intent="Ograniczenie ryzyka OOM przez dynamiczne dopasowanie num_ctx.",
                details={
                    "free_mb": profile.free_mb,
                    "total_mb": profile.total_mb,
                    "suggested_num_ctx": profile.suggested_num_ctx,
                },
            )
        return profile.suggested_num_ctx

    def ask(self, user_message: str) -> str:
        framework_answer = self._handle_framework_meta_query(user_message)
        if framework_answer is not None:
            stored_user_message = self._normalize_user_message_for_storage(user_message)
            self.memory_repository.append_message("user", stored_user_message)
            self.memory_repository.append_message("assistant", framework_answer)
            self.memory_repository.add_memory(
                kind="interaction",
                content=f"U: {stored_user_message}\nA: {framework_answer}",
                source="auto",
            )
            if self.activity_logger:
                self.activity_logger.log(
                    action="framework.meta.answer",
                    intent="Udzielenie precyzyjnej odpowiedzi o możliwościach frameworka bez delegacji do modelu.",
                    details={"user_message_chars": len(user_message)},
                )
            return framework_answer

        if self.activity_logger:
            self.activity_logger.log(
                action="chat.ask",
                intent="Obsługa zapytania użytkownika i zapis interakcji.",
                details={"user_message_chars": len(user_message)},
            )
        stored_user_message = self._normalize_user_message_for_storage(user_message)
        self.memory_repository.append_message("user", stored_user_message)
        user_message_for_model = self._augment_user_message(user_message)

        memory_context = self._build_memory_context(user_message)
        recent_messages = self.memory_repository.recent_messages(limit=12)
        conversation = [{"role": msg.role, "content": msg.content} for msg in recent_messages]
        if user_message_for_model != user_message:
            conversation[-1]["content"] = user_message_for_model

        system_prompt = self.build_system_prompt(user_message)
        response = self.ollama_client.chat(
            messages=conversation,
            system_prompt=system_prompt,
            num_ctx=self._effective_num_ctx(),
        )

        self.memory_repository.append_message("assistant", response)
        self.memory_repository.add_memory(
            kind="interaction",
            content=f"U: {stored_user_message}\nA: {response}",
            source="auto",
        )
        return response

    def remember(self, note: str) -> None:
        if self.activity_logger:
            self.activity_logger.log(
                action="memory.remember",
                intent="Zapis notatki użytkownika w trwałej pamięci.",
                details={"chars": len(note)},
            )
        self.memory_repository.add_memory(kind="note", content=note, source="manual")

    def save_discussion_context(self, content: str) -> None:
        if self.activity_logger:
            self.activity_logger.log(
                action="memory.import_discussion",
                intent="Zapis treści dialogu jako kontekst dyskusji bez kodu.",
                details={"chars": len(content)},
            )
        self.memory_repository.replace_memory(
            kind="discussion_context",
            source="imported_dialogue",
            content=content,
        )

    def generate_python_code(self, description: str) -> str:
        if self.activity_logger:
            self.activity_logger.log(
                action="code.generate.python",
                intent="Generowanie kodu Python na podstawie opisu użytkownika.",
                details={"description_chars": len(description)},
            )
        recent_messages = self.memory_repository.recent_messages(limit=10)
        conversation = [{"role": msg.role, "content": msg.content} for msg in recent_messages]
        user_prompt = (
            "Wygeneruj skrypt Python spełniający opis. "
            "Zwróć tylko kod źródłowy.\n\n"
            f"Opis: {description}"
        )
        conversation.append({"role": "user", "content": user_prompt})
        response = self.ollama_client.chat(
            messages=conversation,
            system_prompt=CODE_SYSTEM_PROMPT,
            num_ctx=self._effective_num_ctx(),
        )
        code = _strip_markdown_fences(response)

        self.memory_repository.add_memory(
            kind="generated_code",
            content=f"Opis: {description}\nKod:\n{code}",
            source="auto",
        )
        return code

    def summarize_session_for_restart(self) -> str:
        recent_messages = self.memory_repository.recent_messages(limit=80)
        if not recent_messages:
            summary = "Brak wcześniejszych wiadomości do podsumowania."
            self.memory_repository.replace_memory(
                kind="session_summary",
                source="session_end",
                content=summary,
            )
            return summary

        transcript = "\n".join(f"{message.role}: {message.content}" for message in recent_messages)
        conversation = [
            {
                "role": "user",
                "content": (
                    "Przygotuj podsumowanie do wznowienia rozmowy po restarcie. "
                    "Utrzymaj konkret techniczny.\n\n"
                    f"Transkrypt:\n{transcript}"
                ),
            }
        ]
        summary = self.ollama_client.chat(
            messages=conversation,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
            num_ctx=self._effective_num_ctx(),
        )
        summary = _strip_markdown_fences(summary)

        self.memory_repository.replace_memory(
            kind="session_summary",
            source="session_end",
            content=summary,
        )
        if self.activity_logger:
            self.activity_logger.log(
                action="session.summary.save",
                intent="Zapis punktu startowego do kontynuacji po restarcie.",
                details={"summary_chars": len(summary)},
            )
        return summary

    def bootstrap_runtime_readiness(self) -> str:
        if self.activity_logger:
            self.activity_logger.log(
                action="runtime.bootstrap.start",
                intent="Zbudowanie kontekstu startowego modelu przed interakcją z użytkownikiem.",
            )

        system_prompt = self.build_system_prompt("bootstrap runtime")
        readiness = self.ollama_client.chat(
            messages=[{"role": "user", "content": BOOTSTRAP_USER_PROMPT}],
            system_prompt=system_prompt,
            num_ctx=self._effective_num_ctx(),
        )
        readiness = _strip_markdown_fences(readiness)

        self.memory_repository.append_message("assistant", f"[BOOTSTRAP] {readiness}")
        self.memory_repository.replace_memory(
            kind="runtime_readiness",
            source="startup_bootstrap",
            content=readiness,
        )

        if self.activity_logger:
            self.activity_logger.log(
                action="runtime.bootstrap.done",
                intent="Model potwierdził gotowość po otrzymaniu kontekstu i instrukcji frameworka.",
                details={"chars": len(readiness)},
            )
        return readiness

    def build_system_prompt(self, user_message: str) -> str:
        memory_context = self._build_memory_context(user_message)
        plan_context = self._build_plan_context()
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"{FRAMEWORK_RUNTIME_GUIDE}\n\n"
            f"{FRAMEWORK_CAPABILITIES_MAP}\n\n"
            f"{TOOL_CALLING_GUIDE}\n\n"
            f"{AUTONOMY_EXECUTION_GUIDE}\n\n"
            f"{WORK_PROGRESS_GUIDE}\n\n"
            f"{self._workspace_guide()}\n\n"
            f"{plan_context}\n\n"
            f"{memory_context}"
        )

    def _augment_user_message(self, user_message: str) -> str:
        normalized = user_message.strip().lower()
        if normalized.startswith("[tool_result]"):
            return self._compact_tool_result_message(user_message)

        autonomy_triggers = {
            "kontynuuj",
            "działaj",
            "dzialaj",
            "ty decyduj",
            "decyduj",
            "sam decyduj",
            "kontynuuj eksperyment",
            "działaj według własnego scenariusza",
            "dzialaj wedlug wlasnego scenariusza",
        }
        if normalized in autonomy_triggers:
            intro_candidates = [
                Path.cwd() / "wprowadzenie.md",
                self.work_dir.parent / "wprowadzenie.md",
            ]
            intro_path = next((path for path in intro_candidates if path.exists()), None)
            intro_hint = (
                f"Najpierw odczytaj plik '{intro_path.resolve()}' narzędziem read_file, "
                if intro_path is not None
                else "Najpierw odczytaj plik 'wprowadzenie.md' narzędziem read_file, "
            )
            return (
                user_message
                + "\n\n"
                + "Instrukcja wykonawcza: rozpocznij od realnych działań przez tool_call. "
                + intro_hint
                + "potem wykonaj pierwszy etap eksperymentu i zapisz artefakt w amiagi-my-work."
            )
        return user_message

    def _handle_framework_meta_query(self, user_message: str) -> str | None:
        normalized = user_message.strip().lower()
        markers = [
            "jak działa framework",
            "czy wiesz jak działa framework",
            "jak używać framework",
            "możliwości framework",
            "co potrafi framework",
        ]
        if not any(marker in normalized for marker in markers):
            return None

        return (
            "Tak — znam i stosuję framework amiagi.\n\n"
            "Aktualne możliwości:\n"
            "- pamięć trwała SQLite (historia, notatki, summary),\n"
            "- logi I/O modelu i logi czynności (JSONL),\n"
            "- komendy frameworka do odczytu/zapisu/uruchamiania zgodnie z polityką zgód,\n"
            "- shell read-only wg whitelist,\n"
            f"- dedykowany katalog roboczy: {self.work_dir.resolve()},\n"
            "- ciągłość sesji przez startup seed + /bye,\n"
            "- dynamiczne num_ctx zależne od VRAM (ochrona OOM).\n\n"
            "Sposób pracy:\n"
            "1) wybieram narzędzie i zwracam blok tool_call,\n"
            "2) czekam na [TOOL_RESULT] z frameworka (z uwzględnieniem zgód),\n"
            "3) raportuję wynik i proponuję następny krok.\n\n"
            "Mogę też proponować rozbudowę frameworka, ale wdrożenie wykonuję dopiero po Twojej zgodzie."
        )


def _strip_markdown_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()
