"""CLI para gerenciamento de tarefas."""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date

from .armazem import Armazem
from .modelo import PRIORIDADES


def _get_caminho() -> str:
    return os.environ.get("TAREFAS_ARQUIVO", "tarefas.json")


def cmd_add(args: argparse.Namespace) -> int:
    arm = Armazem(_get_caminho())
    prioridade = args.prioridade or "media"
    prazo = date.fromisoformat(args.prazo) if args.prazo else None
    tags = list(args.tag) if args.tag else []
    try:
        tarefa = arm.adicionar(titulo=args.titulo, prioridade=prioridade, prazo=prazo, tags=tags)
    except ValueError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1
    print(f"Criada #{tarefa.id}: {tarefa.titulo}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    arm = Armazem(_get_caminho())
    feita = None
    if args.feitas:
        feita = True
    elif args.pendentes:
        feita = False
    tarefas = arm.listar(feita=feita, prioridade=args.prioridade, tag=args.tag)
    for t in tarefas:
        status = "x" if t.feita else " "
        print(f"#{t.id} [{status}] {t.titulo}")
    return 0


def cmd_done(args: argparse.Namespace) -> int:
    arm = Armazem(_get_caminho())
    try:
        tarefa = arm.concluir(args.id)
    except KeyError:
        print(f"Erro: ID {args.id} não encontrado", file=sys.stderr)
        return 1
    print(f"Concluída #{tarefa.id}")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    arm = Armazem(_get_caminho())
    try:
        arm.remover(args.id)
    except KeyError:
        print(f"Erro: ID {args.id} não encontrado", file=sys.stderr)
        return 1
    print(f"Removida #{args.id}")
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    arm = Armazem(_get_caminho())
    todas = arm.listar()
    feitas = [t for t in todas if t.feita]
    pendentes = [t for t in todas if not t.feita]
    atrasadas = arm.atrasadas(date.today())
    print(f"total: {len(todas)}")
    print(f"feitas: {len(feitas)}")
    print(f"pendentes: {len(pendentes)}")
    print(f"atrasadas: {len(atrasadas)}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    arm = Armazem(_get_caminho())
    tarefas = arm.listar()
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "titulo", "prioridade", "feita", "prazo", "tags"])
        for t in tarefas:
            writer.writerow([t.id, t.titulo, t.prioridade, t.feita,
                             t.prazo.isoformat() if t.prazo else "",
                             ";".join(t.tags)])
    print(f"Exportadas {len(tarefas)} tarefas")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tarefas", description="Gerenciador de tarefas")
    sub = parser.add_subparsers(dest="comando")

    p_add = sub.add_parser("add")
    p_add.add_argument("titulo")
    p_add.add_argument("--prioridade", choices=PRIORIDADES, default=None)
    p_add.add_argument("--prazo", default=None)
    p_add.add_argument("--tag", action="append", default=None)

    p_list = sub.add_parser("list")
    p_list.add_argument("--feitas", action="store_true")
    p_list.add_argument("--pendentes", action="store_true")
    p_list.add_argument("--prioridade", choices=PRIORIDADES, default=None)
    p_list.add_argument("--tag", default=None)

    p_done = sub.add_parser("done")
    p_done.add_argument("id", type=int)

    p_rm = sub.add_parser("rm")
    p_rm.add_argument("id", type=int)

    sub.add_parser("stats")

    p_export = sub.add_parser("export")
    p_export.add_argument("--csv", required=True)

    args = parser.parse_args(argv)

    if not args.comando:
        parser.print_help(sys.stderr)
        return 1

    dispatch = {"add": cmd_add, "list": cmd_list, "done": cmd_done,
                "rm": cmd_rm, "stats": cmd_stats, "export": cmd_export}
    handler = dispatch.get(args.comando)
    if handler is None:
        print(f"Erro: subcomando desconhecido: {args.comando}", file=sys.stderr)
        return 1
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
