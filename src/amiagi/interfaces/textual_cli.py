from __future__ import annotations

import json
from pathlib import Path

from amiagi.application.chat_service import ChatService

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Input, RichLog, Static
except Exception as error:  # pragma: no cover - runtime import guard
    raise RuntimeError(
        "Tryb textual wymaga biblioteki 'textual'. Zainstaluj zależności runtime."
    ) from error


class _AmiagiTextualApp(App[None]):
    CSS = """
    Screen { layout: horizontal; }
    #main_column { width: 60%; height: 100%; layout: vertical; }
    #tech_column { width: 40%; height: 100%; layout: vertical; }
    #user_model_log { height: 1fr; border: round #4ea1ff; }
    #input_box { dock: bottom; }
    #supervisor_log { height: 1fr; border: round #47c26b; }
    #executor_log { height: 1fr; border: round #f5a623; }
    .title { padding: 0 1; }
    """

    def __init__(
        self,
        *,
        chat_service: ChatService,
        supervisor_dialogue_log_path: Path,
    ) -> None:
        super().__init__()
        self._chat_service = chat_service
        self._supervisor_dialogue_log_path = supervisor_dialogue_log_path
        self._dialogue_log_offset = 0

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="main_column"):
                yield Static("Użytkownik ↔ Model badany", classes="title")
                yield RichLog(id="user_model_log", wrap=True, highlight=True, markup=True)
                yield Input(placeholder="Wpisz polecenie i Enter (/quit aby wyjść)", id="input_box")
            with Vertical(id="tech_column"):
                yield Static("Nadzorca", classes="title")
                yield RichLog(id="supervisor_log", wrap=True, highlight=True, markup=True)
                yield Static("Model badany → Nadzorca", classes="title")
                yield RichLog(id="executor_log", wrap=True, highlight=True, markup=True)

    def on_mount(self) -> None:
        self.query_one("#user_model_log", RichLog).write("[bold]Tryb Textual aktywny[/bold]")
        self.set_interval(0.75, self._poll_supervision_dialogue)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        user_model_log = self.query_one("#user_model_log", RichLog)
        text = event.value.strip()
        event.input.value = ""
        if not text:
            return
        if text.lower() in {"/quit", "/exit"}:
            self.exit()
            return

        user_model_log.write(f"[bold cyan]Użytkownik:[/bold cyan] {text}")
        try:
            answer = self._chat_service.ask(text)
        except Exception as error:  # pragma: no cover - defensive UI handling
            user_model_log.write(f"[bold red]Błąd:[/bold red] {error}")
            return

        user_model_log.write(f"[bold magenta]Model:[/bold magenta] {answer}")
        self._poll_supervision_dialogue()

    def _poll_supervision_dialogue(self) -> None:
        if not self._supervisor_dialogue_log_path.exists():
            return
        try:
            with self._supervisor_dialogue_log_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._dialogue_log_offset)
                lines = handle.readlines()
                self._dialogue_log_offset = handle.tell()
        except Exception:
            return

        if not lines:
            return

        supervisor_log = self.query_one("#supervisor_log", RichLog)
        executor_log = self.query_one("#executor_log", RichLog)

        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue

            kind = str(payload.get("type", ""))
            stage = str(payload.get("stage", ""))

            executor_answer = str(payload.get("executor_answer", "")).strip()
            if executor_answer:
                executor_log.write(
                    f"[orange3][{stage}:{kind}][/orange3] {executor_answer}"
                )

            supervisor_output = str(payload.get("supervisor_raw_output", "")).strip()
            if supervisor_output:
                supervisor_log.write(
                    f"[green][{stage}:{kind}][/green] {supervisor_output}"
                )
                continue

            status = str(payload.get("status", "")).strip()
            reason = str(payload.get("reason_code", "")).strip()
            repaired = str(payload.get("repaired_answer", "")).strip()
            if status:
                summary = f"status={status}"
                if reason:
                    summary += f", reason={reason}"
                if repaired:
                    summary += f", repaired={repaired}"
                supervisor_log.write(f"[green][{stage}:{kind}][/green] {summary}")


def run_textual_cli(*, chat_service: ChatService, supervisor_dialogue_log_path: Path) -> None:
    _AmiagiTextualApp(
        chat_service=chat_service,
        supervisor_dialogue_log_path=supervisor_dialogue_log_path,
    ).run()

