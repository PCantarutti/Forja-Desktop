"""E14 partes 2–4: candidata vira regra com N ocorrências, contrato por área, checagem sugerida."""
from app import convencoes, preferencias as pf


def _proj(tmp_path):
    (tmp_path / ".forja" / "knowledge").mkdir(parents=True)
    return tmp_path


def test_candidata_so_vira_regra_depois_de_n_ocorrencias(tmp_path):
    root = _proj(tmp_path)
    saida = "a.py:3:1: F401 'os' imported but unused\nFound 1 error. (ruff)"
    for i in range(pf.PROMOVE - 1):
        pf.do_verify(root, saida, ["a.py"], f"TASK-00{i}")
        assert pf.regras(root) == []
    pf.do_verify(root, saida, ["a.py"], "TASK-009")
    r = pf.regras(root)
    assert len(r) == 1 and "F401" in r[0]["texto"] and r[0]["area"] == "backend" and r[0]["contador"] == pf.PROMOVE
    assert "F401" in (root / pf.MD).read_text(encoding="utf-8")


def test_correcao_no_chat_vira_regra_na_hora_e_conversa_comum_nao(tmp_path):
    root = _proj(tmp_path)
    assert pf.do_chat(root, "Sempre que der, rode os testes. Nunca vi isso.") == []
    novas = pf.do_chat(root, "Não use classes nos componentes, por favor.")
    assert novas and pf.regras(root)[0]["texto"].startswith("Não use classes")


def test_contrato_de_tarefa_py_nao_leva_regra_de_frontend(tmp_path):
    root = _proj(tmp_path)
    for _ in range(pf.PROMOVE):
        pf.sinal(root, "x:front", "Componentes em função, sem classe", "frontend", "e")
        pf.sinal(root, "x:back", "Funções async no serviço", "backend", "e")
    texto_py = pf.para_contrato(root, ["app/servico.py"])
    assert "async" in texto_py and "Componentes" not in texto_py
    assert "Componentes" in pf.para_contrato(root, ["web/Botao.tsx"])
    assert "Componentes" in convencoes.texto_para_prompt(root)          # sem arquivos: todas
    assert "Componentes" not in convencoes.texto_para_prompt(root, ["app/servico.py"])


def test_typescript_strict_sugere_tsc_no_verify_e_regra_sem_checagem_vai_ao_revisor(tmp_path):
    root = _proj(tmp_path)
    for i in range(pf.PROMOVE):
        pf.do_verify(root, "src/a.ts(3,5): error TS2322: Type 'x' is not assignable", ["src/a.ts"], f"T{i}")
        pf.sinal(root, "chat:nomes", "Nomes em português", "frontend", "e")
    assert pf.sugestoes_de_verify(root, ["src/b.ts"], "npm test") == ["npx tsc --noEmit"]
    assert pf.sugestoes_de_verify(root, ["src/b.ts"], "npm test && npx tsc --noEmit") == []
    assert pf.sem_checagem(root, ["src/b.ts"]) == ["(regra do projeto) Nomes em português"]


def test_apagar_a_linha_no_md_esquece_a_regra(tmp_path):
    root = _proj(tmp_path)
    pf.do_chat(root, "Evite any no TypeScript.")
    md = root / pf.MD
    texto = md.read_text(encoding="utf-8")
    md.write_text("\n".join(l for l in texto.splitlines() if "[R1|" not in l), encoding="utf-8")
    assert pf.regras(root) == []


def test_sem_pasta_forja_nao_aprende_nada(tmp_path):
    assert pf.do_chat(tmp_path, "Não use classes.") == []
    assert not (tmp_path / ".forja").exists()
