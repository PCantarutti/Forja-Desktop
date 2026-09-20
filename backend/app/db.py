from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from . import config


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="Nova conversa")
    kind: Mapped[str] = mapped_column(String(10), default="agent")  # chat | agent (seções separadas)
    workspace: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # pasta do Windows; None = padrão
    pinned: Mapped[bool] = mapped_column(default=False)    # fixada no topo da lista
    archived: Mapped[bool] = mapped_column(default=False)  # fora da lista principal
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)
    messages: Mapped[list["Message"]] = relationship(
        cascade="all, delete-orphan", order_by="Message.id", back_populates="conversation")


class Message(Base):
    """role: user | assistant | tool | event (event = aviso da UI, nunca vai para o modelo)."""
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    thinking: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{id,name,arguments}]
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ok | erro | rejeitada | cancelada
    meta: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # args, preview, warning kind...
    created_at: Mapped[datetime] = mapped_column(default=_now)
    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in (
            "id", "role", "content", "thinking", "tool_calls", "tool_call_id", "name", "status", "meta")}


class ModelSetting(Base):
    __tablename__ = "model_settings"
    model: Mapped[str] = mapped_column(String(300), primary_key=True)
    tool_mode: Mapped[str] = mapped_column(String(10), default="auto")  # native | text | auto
    vision: Mapped[str] = mapped_column(String(5), default="auto")  # auto (detectar) | yes | no
    # Amostragem deste modelo: só as chaves que o usuário mudou. O resto fica com o padrão do servidor.
    inference: Mapped[dict] = mapped_column(JSON, default=dict)


class Checkpoint(Base):
    """Conteúdo de um arquivo ANTES da primeira alteração feita pelo agente num turno.

    turn_id = id da mensagem do usuário que abriu o turno. Restaurar = voltar cada arquivo ao
    estado anterior (ou apagar, se ele não existia). Mudanças feitas via run_command não entram.
    """
    __tablename__ = "checkpoints"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    turn_id: Mapped[int] = mapped_column(index=True)
    path: Mapped[str] = mapped_column(String(2000))  # caminho absoluto no container
    existed: Mapped[bool] = mapped_column(default=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class AppSetting(Base):
    """Configurações editadas na UI. Só existem aqui as chaves que o usuário mudou."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)


def _migrate() -> None:
    """create_all não adiciona colunas em tabelas existentes; bancos antigos ganham `vision` aqui."""
    with engine.begin() as c:
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(model_settings)")}
        if "vision" not in cols:
            c.exec_driver_sql("ALTER TABLE model_settings ADD COLUMN vision VARCHAR(5) DEFAULT 'auto'")
        if "inference" not in cols:
            c.exec_driver_sql("ALTER TABLE model_settings ADD COLUMN inference JSON")
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(conversations)")}
        if "workspace" not in cols:
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN workspace VARCHAR(1000)")
        if "pinned" not in cols:
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN pinned BOOLEAN DEFAULT 0")
        if "archived" not in cols:
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN archived BOOLEAN DEFAULT 0")
        if "kind" not in cols:
            # Conversas antigas: quem usou ferramenta era Agente; o resto vira Chat.
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN kind VARCHAR(10) DEFAULT 'agent'")
            c.exec_driver_sql("""
                UPDATE conversations SET kind = 'chat'
                WHERE id NOT IN (SELECT DISTINCT conversation_id FROM messages WHERE role = 'tool')
                  AND id IN (SELECT DISTINCT conversation_id FROM messages
                             WHERE role = 'assistant' AND json_extract(meta, '$.via') = 'none')""")


_migrate()


def session() -> Session:
    return Session(engine, expire_on_commit=False)


def get_model_setting(model: str) -> dict:
    with session() as s:
        ms = s.get(ModelSetting, model)
        return {"tool_mode": ms.tool_mode if ms else "auto", "vision": (ms.vision if ms else None) or "auto",
                "inference": (ms.inference if ms else None) or {}}


def get_tool_mode(model: str) -> str:
    return get_model_setting(model)["tool_mode"]
