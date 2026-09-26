from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


PRIORIDADES = ("baixa", "media", "alta")


@dataclass
class Tarefa:
    id: int
    titulo: str
    prioridade: str = "media"
    feita: bool = False
    prazo: date | None = None
    tags: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.titulo or not self.titulo.strip():
            raise ValueError("título não pode ser vazio ou só espaços")
        if self.prioridade not in PRIORIDADES:
            raise ValueError(
                f"prioridade inválida: {self.prioridade!r}. "
                f"Esperadas: {PRIORIDADES}"
            )

    def para_dict(self) -> dict[str, Any]:
        """Retorna um dict serializável (prazo como str 'AAAA-MM-DD' ou None)."""
        d = asdict(self)
        if isinstance(d.get("prazo"), date):
            d["prazo"] = d["prazo"].isoformat()
        return d

    @classmethod
    def de_dict(cls, d: dict[str, Any]) -> Tarefa:
        """Reconstrói uma Tarefa a partir de um dict."""
        prazo = d.get("prazo")
        if prazo is not None:
            prazo = date.fromisoformat(prazo)
        return cls(
            id=d["id"],
            titulo=d["titulo"],
            feita=d.get("feita", False),
            prioridade=d.get("prioridade", "media"),
            prazo=prazo,
            tags=list(d.get("tags", [])),
        )
