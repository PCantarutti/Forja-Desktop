import os
from datetime import datetime, timedelta

from app import pc

PEDIDOS = {
    "abre o Spotify": ("abrir", "spotify"),
    "pode abrir o VS Code?": ("abrir", "vs code"),
    "inicia o aplicativo do WhatsApp": ("abrir", "whatsapp"),
    "fecha o Chrome": ("fechar", "chrome"),
    "fecha essa janela": ("fechar", ""),
    "encerra o Discord.": ("fechar", "discord"),
    "volume 30": ("volume", "30"),
    "coloca o volume em 45%": ("volume", "45"),
    "deixa o som no 20": ("volume", "20"),
    "volume no máximo": ("volume", "100"),
    "aumenta o volume": ("volume", "+10"),
    "abaixa um pouco o som": ("volume", "-10"),
    "tira o som": ("mudo", "1"),
    "silencia o pc": ("mudo", "1"),
    "volta o som": ("mudo", "0"),
    "tira o mudo pra mim": ("mudo", "0"),
    "desliga o mudo do computador": ("mudo", "0"),
    "baixa o volume pra trinta por cento": ("volume", "30"),
    "volume em trinta e cinco": ("volume", "35"),
    "tira o som do pc e joga pra caixa": ("saida", "caixa"),
    "muda para o fone": ("saida", "fone"),
    "passa o som pra caixa": ("saida", "caixa"),
    "troca a saída de áudio para o headset game": ("saida", "headset game"),
    "trava o PC": ("travar", ""),
    "bloqueia a tela": ("travar", ""),
    "modo não perturbe": ("nao_perturbe", ""),
    "joga essa janela para o outro monitor": ("outro_monitor", ""),
    "manda o chrome pro outro monitor": ("outro_monitor", "chrome"),
    "maximiza o VS Code": ("maximizar", "vs code"),
    "minimiza essa janela": ("minimizar", ""),
    "minimiza tudo": ("minimizar_tudo", ""),
    "mostra a área de trabalho": ("minimizar_tudo", ""),
    "pausa a música": ("midia", "pausa"),
    "pausa": ("midia", "pausa"),
    "para essa música um segundo": ("midia", "pausa"),
    "para de tocar": ("midia", "pausa"),
    "volta a anterior, por favor": ("midia", "anterior"),
    "bloqueia essa tela aqui rapidinho": ("travar", ""),
    "continua a música": ("midia", "pausa"),
    "próxima música": ("midia", "proxima"),
    "pula essa música": ("midia", "proxima"),
    "volta a música": ("midia", "anterior"),
    "o que está tocando?": ("tocando", ""),
    "que música é essa?": ("tocando", ""),
    "abre a pasta de downloads": ("pasta", "downloads"),
    "abre meus documentos": ("pasta", "documentos"),
    "abre a área de trabalho": ("pasta", "area de trabalho"),
    "acha o PDF do contrato de ontem": ("arquivo", "pdf do contrato de ontem"),
    "procura a planilha de gastos": ("arquivo", "planilha de gastos"),
    "onde está o print que eu tirei hoje?": ("arquivo", "print que eu tirei hoje"),
}

NAO_SAO = ["Procura vídeos de yoga para iniciantes", "quanto é 15% de 200?", "que horas são?", "procura o preço da placa de vídeo", "me conta uma piada",
           "qual a capital da França?", "passa para a próxima pergunta"]


def test_entende_os_pedidos_do_pc():
    for frase, esperado in PEDIDOS.items():
        assert pc.entende(frase) == esperado, frase
    for frase in NAO_SAO:
        assert pc.entende(frase) is None, frase


SAIDAS = [{"id": "1", "nome": "Alto-falantes (Steam Streaming Microphone)", "padrao": False},
          {"id": "2", "nome": "Fone de ouvido do headset (usb audio voice)", "padrao": True},
          {"id": "3", "nome": "Fones de ouvido (usb audio game)", "padrao": False},
          {"id": "4", "nome": "Áudio Digital (HDMI) (2- High Definition Audio Device)", "padrao": False},
          {"id": "5", "nome": "Alto-falantes (High Definition Audio Device)", "padrao": False}]


def test_acha_a_saida_de_som():
    s = lambda d: (pc.acha_saida(d, SAIDAS) or {}).get("id")
    assert s("fone") == "3"            # dois fones: o que ainda não está no ar
    assert s("fone game") == "3"
    assert s("headset voice") == "2"
    assert s("caixa") == "5"           # o do Steam é virtual
    assert s("monitor") == "4"
    assert s("headphone") == "3"
    assert s("geladeira") is None


APPS = [{"Name": "Spotify", "AppID": "SpotifyAB.Spotify"}, {"Name": "Visual Studio Code", "AppID": "vscode"},
        {"Name": "Uninstall Visual Studio Code", "AppID": "x"}, {"Name": "Google Chrome", "AppID": "Chrome"},
        {"Name": "WhatsApp", "AppID": "wa"}, {"Name": "Calculadora", "AppID": "calc"}]


def test_acha_o_programa():
    a = lambda d: (pc.acha_app(d, APPS) or {}).get("AppID")
    assert a("spotify") == "SpotifyAB.Spotify"
    assert a("vs code") == "vscode"
    assert a("chrome") == "Chrome"
    assert a("zap") == "wa"
    assert a("calculadora") == "calc"
    assert a("photoshop") is None


def test_acha_a_janela():
    js = [{"hwnd": 1, "titulo": "Forja", "exe": r"C:\x\Forja.exe", "pid": 10},
          {"hwnd": 2, "titulo": "Nova guia - Google Chrome", "exe": r"C:\g\chrome.exe", "pid": 20},
          {"hwnd": 3, "titulo": "YouTube - Google Chrome", "exe": r"C:\g\chrome.exe", "pid": 20},
          {"hwnd": 4, "titulo": "pc.py - forja-desktop - Visual Studio Code", "exe": r"C:\c\Code.exe", "pid": 30}]
    assert [j["hwnd"] for j in pc.acha_janela("chrome", js)] == [2, 3]
    assert [j["hwnd"] for j in pc.acha_janela("vs code", js)] == [4]
    assert pc.acha_janela("discord", js) == []
    assert [j["hwnd"] for j in pc.acha_janela("navegador", js)] == [2, 3]


def test_busca_arquivo(tmp_path):
    agora = datetime(2026, 10, 23, 15, 0)
    def cria(nome, dias):
        p = tmp_path / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        t = (agora - timedelta(days=dias)).timestamp()
        os.utime(p, (t, t))
    cria("Contrato-Aluguel.pdf", 1)
    cria("contrato antigo.pdf", 30)
    cria("sub/contrato aluguel.docx", 1)
    cria("gastos 2026.xlsx", 3)
    p, _ = pc.busca_arquivo("pdf do contrato de ontem", [tmp_path], agora)
    assert p.name == "Contrato-Aluguel.pdf"
    p, _ = pc.busca_arquivo("planilha de gastos", [tmp_path], agora)
    assert p.name == "gastos 2026.xlsx"
    p, filtro = pc.busca_arquivo("pdf do imposto de hoje", [tmp_path], agora)
    assert p is None and "imposto" in filtro


def test_nao_rouba_pedido_do_forja_nem_pergunta():
    from tests.test_assistente import ROTAS
    from app import assistente, lembretes
    for item in ROTAS:
        frase = item[0] if isinstance(item, tuple) else item
        if assistente.rota(frase)[0] == "chat":
            assert pc.entende(frase) is None and lembretes.entende(frase) is None, frase


def test_valida_o_que_o_modelo_pede():
    assert pc.valida("volume", "30%") == ("volume", "30")
    assert pc.valida("volume", "+10") == ("volume", "+10")
    assert pc.valida("volume", "máximo") == ("volume", "100")
    assert pc.valida("midia", "próxima") == ("midia", "proxima")
    assert pc.valida("pasta", "area_de_trabalho") == ("pasta", "area de trabalho")
    assert pc.valida("fechar", "") is None          # nunca "fecha a janela da frente" por palpite do modelo
    assert pc.valida("fechar", "chrome") == ("fechar", "chrome")
    assert pc.valida("formatar", "c:") is None      # fora da lista
    assert pc.valida("pasta", "C:\\Windows") is None
