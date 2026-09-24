from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, ForeignKey, LargeBinary, String, Text, create_engine, event
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
    kind: Mapped[str] = mapped_column(String(10), default="agent")  # chat|agent|maestro|imagem|comparar|pesquisa
    workspace: Mapped[str | None] = mapped_column(String(1000), nullable=True)  # pasta do Windows; None = padrão
    pinned: Mapped[bool] = mapped_column(default=False)    # fixada no topo da lista
    archived: Mapped[bool] = mapped_column(default=False)  # fora da lista principal
    # Conversa de Imagens aberta pela IA (skill gerar-imagens): {conv_id, message_id} do chat e da
    # chamada imagens_pendentes que pediu. Só gera os slots dela; None = conversa comum.
    origem: Mapped[dict | None] = mapped_column(JSON, nullable=True)
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
        return {**{k: getattr(self, k) for k in (
            "id", "role", "content", "thinking", "tool_calls", "tool_call_id", "name", "status", "meta")},
                "created_at": self.created_at.isoformat() if self.created_at else None}  # aba Trajetória


class ModelSetting(Base):
    __tablename__ = "model_settings"
    model: Mapped[str] = mapped_column(String(300), primary_key=True)
    tool_mode: Mapped[str] = mapped_column(String(10), default="auto")  # native | text | auto
    vision: Mapped[str] = mapped_column(String(5), default="auto")  # auto (detectar) | yes | no
    # Amostragem deste modelo: só as chaves que o usuário mudou. O resto fica com o padrão do servidor.
    inference: Mapped[dict] = mapped_column(JSON, default=dict)


class Goal(Base):
    """Objetivo longo de uma conversa (goals.py): o agente segue em rodadas até provar que terminou."""
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    objective: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="ativa")  # ativa|pausada|completa|bloqueada
    armada: Mapped[bool] = mapped_column(default=True)   # False depois de retomar a conversa, até resume
    rodada: Mapped[int] = mapped_column(default=0)
    revisao: Mapped[int] = mapped_column(default=1)
    bloqueios: Mapped[int] = mapped_column(default=0)    # rodadas seguidas relatando bloqueio
    rodada_bloqueio: Mapped[int] = mapped_column(default=-1)
    motivo: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=_now)


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
    # Tentativa de Worker (Maestro) que fez a alteração: é o que permite desfazer UMA tarefa em vez
    # do turno inteiro da Maestro, que costuma despachar várias.
    attempt_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class AppSetting(Base):
    """Configurações editadas na UI. Só existem aqui as chaves que o usuário mudou."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


# --------------------------------------------------------------------- Maestro
# Estado do projeto do Maestro. Mora aqui, e nao no contexto do modelo: um Worker pode ser morto, o
# modelo local descarregado e outro carregado no lugar sem que uma tarefa se perca. E o que permite
# rodar IA local em maquina apertada (ver maestro.py).


class Feature(Base):
    """Um objetivo do usuario, decomposto em tarefas pelo Maestro."""
    __tablename__ = "features"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="planning")  # planning|active|validating|done|cancelled
    # Copiada para uma sessão nova (conversa): a lista fica aqui para consulta, e o trabalho segue lá.
    copiada_para: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)
    tasks: Mapped[list["Task"]] = relationship(
        cascade="all, delete-orphan", order_by="Task.priority.desc(), Task.id", back_populates="feature")


class Task(Base):
    """Uma tarefa com Implementation Contract. `code` (TASK-001) e o nome estavel: e ele que aparece
    na interface e no prompt do Maestro, entao nao muda nem quando o id do banco muda."""
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    feature_id: Mapped[int] = mapped_column(ForeignKey("features.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String(200))
    contract: Mapped[dict] = mapped_column(JSON, default=dict)  # ver taskdb.CONTRACT_FIELDS
    depends_on: Mapped[list] = mapped_column(JSON, default=list)  # [code] de outras tarefas
    priority: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    model_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)  # rapido|capaz|nuvem
    agent: Mapped[str | None] = mapped_column(String(60), nullable=True)  # persona .forja/agents/*.md
    max_attempts: Mapped[int] = mapped_column(default=5)
    attempt_count: Mapped[int] = mapped_column(default=0)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # ultimo task_result
    blocked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now)
    feature: Mapped[Feature] = relationship(back_populates="tasks")
    attempts: Mapped[list["Attempt"]] = relationship(
        cascade="all, delete-orphan", order_by="Attempt.n", back_populates="task")


class Attempt(Base):
    """Uma execucao da tarefa por um Worker. Gravada ANTES do worker rodar: app fechado no meio deixa
    o rastro, e maestro.reap() sabe o que estava em voo."""
    __tablename__ = "attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|failed|cancelled|error
    worker: Mapped[dict] = mapped_column(JSON, default=dict)  # {level, provider, model, agent}
    strategy: Mapped[str | None] = mapped_column(Text, nullable=True)  # o que mudou nesta tentativa
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    seconds: Mapped[float] = mapped_column(default=0.0)
    tokens: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # A conversa do Worker nesta tentativa, no formato das mensagens do chat (briefing, raciocínio,
    # chamadas com diff, resultados, estatísticas). Gravada a cada rodada, não só no fim: sobrevive a
    # F5, a queda do app e a um Parar no meio.
    transcript: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # {caminho relativo: sha1 | None} dos arquivos que a tentativa deixou. Na tarefa seguinte, o que
    # não bate mais foi mexido por fora (usuário, outro programa) e a Maestro precisa saber.
    estado: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    task: Mapped[Task] = relationship(back_populates="attempts")


Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(f"sqlite:///{config.DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _pragmas(dbapi_conn, _record):
    """O SQLite abre no modo mais conservador que existe; aqui ele vira o que este app precisa.

    Escrevem no banco: o loop do agente, os lotes de imagem, os downloads e toda ferramenta que
    roda em thread. No journal padrão, uma escrita bloqueia qualquer leitura — e sem `busy_timeout`
    a segunda escrita nem espera, devolve "database is locked" na hora, que aparecia como 500 ou
    como execução morta no meio. WAL deixa leitor e escritor conviverem, e o timeout faz a
    concorrência virar espera. `foreign_keys` liga o ON DELETE CASCADE que os modelos declaram e o
    SQLite ignora por padrão.
    """
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA synchronous=NORMAL")  # com WAL, durável o bastante para dado de app local
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


Base.metadata.create_all(engine)


def _migrate() -> None:
    """create_all não adiciona colunas em tabelas existentes; bancos antigos ganham `vision` aqui."""
    with engine.begin() as c:
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(model_settings)")}
        if "vision" not in cols:
            c.exec_driver_sql("ALTER TABLE model_settings ADD COLUMN vision VARCHAR(5) DEFAULT 'auto'")
        if "inference" not in cols:
            c.exec_driver_sql("ALTER TABLE model_settings ADD COLUMN inference JSON")
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(attempts)")}
        if cols and "transcript" not in cols:
            c.exec_driver_sql("ALTER TABLE attempts ADD COLUMN transcript JSON")
        if cols and "estado" not in cols:
            c.exec_driver_sql("ALTER TABLE attempts ADD COLUMN estado JSON")
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(features)")}
        if cols and "copiada_para" not in cols:
            c.exec_driver_sql("ALTER TABLE features ADD COLUMN copiada_para INTEGER")
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(checkpoints)")}
        if cols and "attempt_id" not in cols:
            c.exec_driver_sql("ALTER TABLE checkpoints ADD COLUMN attempt_id INTEGER")
        cols = {row[1] for row in c.exec_driver_sql("PRAGMA table_info(conversations)")}
        if "workspace" not in cols:
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN workspace VARCHAR(1000)")
        if "pinned" not in cols:
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN pinned BOOLEAN DEFAULT 0")
        if "archived" not in cols:
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN archived BOOLEAN DEFAULT 0")
        if "origem" not in cols:
            c.exec_driver_sql("ALTER TABLE conversations ADD COLUMN origem JSON")
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
