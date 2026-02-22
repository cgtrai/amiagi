from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


@dataclass
class PermissionManager:
    input_fn: InputFn = input
    output_fn: OutputFn = print
    allow_all: bool = False
    granted_once: set[str] = field(default_factory=set)

    def _ask(self, resource: str, reason: str) -> bool:
        prompt = (
            f"Wymagana zgoda na zasób '{resource}'. {reason}\n"
            "Wybierz: [t]ak (zapamiętaj dla tego zasobu) / [n]ie / [w]szystko (zgoda globalna): "
        )
        answer = self.input_fn(prompt).strip().lower()

        if answer in {"a", "all", "w", "wszystko"}:
            self.allow_all = True
            self.output_fn("Włączono tryb: zgoda globalna na wszystkie zasoby.")
            return True

        if answer in {"t", "tak", "y", "yes"}:
            self.granted_once.add(resource)
            return True

        self.output_fn(f"Odmowa dostępu do zasobu: {resource}")
        return False

    def request(self, resource: str, reason: str) -> bool:
        if self.allow_all:
            return True

        if resource in self.granted_once:
            return True

        return self._ask(resource=resource, reason=reason)

    def request_disk_read(self, reason: str) -> bool:
        return self.request("disk.read", reason)

    def request_disk_write(self, reason: str) -> bool:
        return self.request("disk.write", reason)

    def request_local_network(self, reason: str) -> bool:
        return self.request("network.local", reason)

    def request_internet(self, reason: str) -> bool:
        return self.request("network.internet", reason)

    def request_camera_access(self, reason: str = "Dostęp do kamery.") -> bool:
        return self.request("camera", reason)

    def request_microphone_access(self, reason: str = "Dostęp do mikrofonu.") -> bool:
        return self.request("microphone", reason)

    def request_clipboard_read(self, reason: str = "Odczyt schowka systemowego.") -> bool:
        return self.request("clipboard.read", reason)

    def request_clipboard_write(self, reason: str = "Zapis schowka systemowego.") -> bool:
        return self.request("clipboard.write", reason)

    def request_process_exec(self, reason: str = "Uruchomienie procesu systemowego.") -> bool:
        return self.request("process.exec", reason)
