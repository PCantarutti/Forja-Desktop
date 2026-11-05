; Tema do instalador do Forja: as páginas do assistente do electron-builder (MUI2) com as cores do
; tema padrão do app (frontend/src/index.css): fundo #171717, campos #1f1f1f, texto #ececec,
; destaque #4f8ff7. Incluído antes do MUI2, então aqui só entram !define, funções e macros; as
; macros custom* são chamadas pelos templates do electron-builder (assistedInstaller.nsh).
;
; Os MUI_PAGE_CUSTOMFUNCTION_SHOW definidos nas macros custom* "vazam" de propósito para a próxima
; página MUI inserida pelo template. A página de modo de instalação (multiUserUi.nsh) consome um SHOW
; mesmo pulada, por isso a de pasta e o progresso da desinstalação não recebem por esse caminho:
;   customWelcomePage       -> boas-vindas (própria); a de pasta vai pelo .onVerifyInstDir
;   customPageAfterChangeDir-> progresso
;   customFinishPage        -> conclusão (própria, igual à do template)
;   customUnWelcomePage     -> boas-vindas da desinstalação
;   customUninstallPage     -> conclusão da desinstalação

!include LogicLib.nsh
!include WinMessages.nsh

!define MUI_BGCOLOR "171717"
!define MUI_TEXTCOLOR "ECECEC"
!define MUI_FORCECLASSICCONTROLS ; caixas de seleção sem tema visual, senão o texto sai preto no fundo escuro
!define MUI_HEADERIMAGE_BITMAP_NOSTRETCH

!ifndef BUILD_UNINSTALLER ; o MUI2 usa os mesmos nomes nas páginas do desinstalador
  !define MUI_WELCOMEPAGE_TITLE "Boas-vindas ao ${PRODUCT_NAME}"
  !define MUI_WELCOMEPAGE_TEXT "Este assistente instala o ${PRODUCT_NAME} ${VERSION} no seu computador.$\r$\n$\r$\nSuas conversas, projetos e configurações ficam guardados à parte e não são alterados pela instalação.$\r$\n$\r$\nClique em Próximo para continuar."
  !define MUI_FINISHPAGE_TITLE "${PRODUCT_NAME} instalado"
  !define MUI_FINISHPAGE_TEXT "O ${PRODUCT_NAME} ${VERSION} está pronto para usar.$\r$\n$\r$\nClique em Concluir para fechar o assistente."
  !define MUI_FINISHPAGE_RUN_TEXT "Abrir o ${PRODUCT_NAME} agora"
!endif

!ifdef BUILD_UNINSTALLER
  !define FORJA_FN "un."
  !define MUI_CUSTOMFUNCTION_UNGUIINIT un.forjaGuiInit
!else
  !define FORJA_FN ""
  !define MUI_CUSTOMFUNCTION_GUIINIT forjaGuiInit
!endif

Var forjaFonte
Var forjaFontePadrao

; Pinta a janela $R9 e todos os filhos diretos. Funciona para a janela externa e para o diálogo
; interno de cada página.
!macro FORJA_PINTA_FN PFX
Function ${PFX}forjaPinta
  Push $0
  Push $1
  Push $2
  Push $3
  SetCtlColors $R9 "ECECEC" "171717"
  FindWindow $0 "" "" $R9
  ${Do}
    ${If} $0 = 0
      ${Break}
    ${EndIf}
    System::Call 'user32::GetClassNameW(p r0, w .r1, i 64)'
    SendMessage $0 ${WM_GETFONT} 0 0 $2
    System::Call 'user32::GetDlgCtrlID(p r0) i .r3'
    ${If} $2 = $forjaFontePadrao
    ${AndIf} $3 != 1038 ; subtítulo do cabeçalho: espaço estreito ao lado da imagem

      SendMessage $0 ${WM_SETFONT} $forjaFonte 0
    ${EndIf}
    ${If} $1 == "Button"
      System::Call 'user32::GetWindowLongW(p r0, i -16) i .r2'
      IntOp $2 $2 & 0xF
      ${If} $2 = 7 ; BS_GROUPBOX "Pasta de Destino": sem tema a borda sai branca em relevo, com o
        ShowWindow $0 ${SW_HIDE} ; tema escuro o rótulo some; o texto da página já explica a pasta
      ${ElseIf} $2 <= 1 ; BS_PUSHBUTTON / BS_DEFPUSHBUTTON
        System::Call 'uxtheme::SetWindowTheme(p r0, w "DarkMode_Explorer", p 0)'
        SetCtlColors $0 "ECECEC" "171717"
      ${Else}
        System::Call 'uxtheme::SetWindowTheme(p r0, w " ", w " ")'
        SetCtlColors $0 "ECECEC" "171717"
      ${EndIf}
    ${ElseIf} $1 == "Edit"
      SetCtlColors $0 "ECECEC" "1F1F1F"
    ${ElseIf} $1 == "msctls_progress32"
      System::Call 'uxtheme::SetWindowTheme(p r0, w " ", w " ")'
      SendMessage $0 0x0409 0 0xF78F4F ; PBM_SETBARCOLOR #4f8ff7 (BGR)
      SendMessage $0 0x2001 0 0x2A2A2A ; PBM_SETBKCOLOR  #2a2a2a
    ${ElseIf} $1 == "SysListView32"
      System::Call 'uxtheme::SetWindowTheme(p r0, w "DarkMode_Explorer", p 0)'
      SendMessage $0 0x1001 0 0x0D0D0D ; LVM_SETBKCOLOR     #0d0d0d (--color-code)
      SendMessage $0 0x1026 0 0x0D0D0D ; LVM_SETTEXTBKCOLOR
      SendMessage $0 0x1024 0 0xD4D4D4 ; LVM_SETTEXTCOLOR   #d4d4d4
    ${ElseIf} $3 = 1028 ; texto da marca no rodapé, apagado como o --color-faint (desabilitado, o
      EnableWindow $0 1 ; Windows desenha em relevo e ignora a cor)
      SetCtlColors $0 "737373" "171717"
    ${ElseIf} $3 = 1256 ; sombra da marca e linhas em relevo: no escuro viram riscos claros. Tamanho
    ${OrIf} $3 = 1035  ; zero em vez de esconder, porque o MUI os reexibe a cada página
    ${OrIf} $3 = 1036
      System::Call 'user32::SetWindowPos(p r0, p 0, i 0, i 0, i 0, i 0, i 0x16)'
    ${ElseIf} $1 != "#32770" ; o diálogo da página é pintado no SHOW dela
      SetCtlColors $0 "ECECEC" "171717"
    ${EndIf}
    FindWindow $0 "" "" $R9 $0
  ${Loop}
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function ${PFX}forjaGuiInit
  ; barra de título escura e da cor do fundo (Windows 11; no 10 só o modo escuro pega)
  System::Call '*(i 1) p .r0'
  System::Call 'dwmapi::DwmSetWindowAttribute(p $HWNDPARENT, i 20, p r0, i 4)'
  System::Call '*$0(i 0x171717)'
  System::Call 'dwmapi::DwmSetWindowAttribute(p $HWNDPARENT, i 35, p r0, i 4)'
  System::Free $0
  SendMessage $HWNDPARENT ${WM_GETFONT} 0 0 $forjaFontePadrao
  CreateFont $forjaFonte "Segoe UI" 9
  Push $R9
  StrCpy $R9 $HWNDPARENT
  Call ${PFX}forjaPinta
  Pop $R9
FunctionEnd

Function ${PFX}forjaPaginaShow
  ${If} $HWNDPARENT = 0
    Return
  ${EndIf}
  Push $R9
  FindWindow $R9 "#32770" "" $HWNDPARENT
  ; O diálogo nativo das páginas (pasta, progresso) ignora o SetCtlColors no próprio fundo: um
  ; static escuro do tamanho da página, no fundo da pilha e com WS_CLIPSIBLINGS, faz esse papel.
  Push $0
  System::Call 'user32::CreateWindowExW(i 0, w "STATIC", w "", i 0x54000000, i 0, i 0, i 4000, i 4000, p $R9, p 0, p 0, p 0) p .r0'
  System::Call 'user32::SetWindowPos(p r0, p 1, i 0, i 0, i 0, i 0, i 0x13)'
  Pop $0
  Call ${PFX}forjaPinta
  Pop $R9
FunctionEnd
!macroend

!insertmacro FORJA_PINTA_FN "${FORJA_FN}"

!ifndef BUILD_UNINSTALLER
  !macro customInstallMode
    ; sempre por usuário (perMachine: false, sem elevação): pula a página "para quem instalar?"
    StrCpy $isForceCurrentInstall "1"
  !macroend

  !macro customWelcomePage
    !insertmacro skipPageIfUpdated
    !define MUI_PAGE_CUSTOMFUNCTION_SHOW forjaPaginaShow
    !insertmacro MUI_PAGE_WELCOME
  !macroend

  ; A página de modo de instalação (multiUserUi.nsh) consome o SHOW que vazaria para a de pasta, mesmo
  ; pulada. A de pasta chama .onVerifyInstDir ao abrir (e a cada tecla): pinta uma vez por diálogo.
  Var forjaPastaPintada
  Function .onVerifyInstDir
    Push $R8
    ${If} $HWNDPARENT <> 0
      FindWindow $R8 "#32770" "" $HWNDPARENT
      ${If} $R8 <> $forjaPastaPintada
        StrCpy $forjaPastaPintada $R8
        Call forjaPaginaShow
      ${EndIf}
    ${EndIf}
    Pop $R8
  FunctionEnd

  !macro customPageAfterChangeDir
    !define MUI_PAGE_CUSTOMFUNCTION_SHOW forjaPaginaShow ; progresso
  !macroend

  !macro customFinishPage
    !ifndef HIDE_RUN_AFTER_FINISH
      Function StartApp
        ${if} ${isUpdated}
          StrCpy $1 "--updated"
        ${else}
          StrCpy $1 ""
        ${endif}
        ${StdUtils.ExecShellAsUser} $0 "$launchLink" "open" "$1"
      FunctionEnd
      !define MUI_FINISHPAGE_RUN
      !define MUI_FINISHPAGE_RUN_FUNCTION "StartApp"
    !endif
    !define MUI_PAGE_CUSTOMFUNCTION_SHOW forjaPaginaShow
    !insertmacro MUI_PAGE_FINISH
  !macroend
!else
  !macro customUnWelcomePage
    !define MUI_PAGE_CUSTOMFUNCTION_SHOW un.forjaPaginaShow
    !insertmacro MUI_UNPAGE_WELCOME
    ; o progresso da desinstalação fica sem fundo escuro no diálogo interno: a página de modo de
    ; instalação consome o SHOW, e não há outro gancho na thread da interface antes dela
  !macroend

  !macro customUninstallPage
    !define MUI_PAGE_CUSTOMFUNCTION_SHOW un.forjaPaginaShow ; conclusão
  !macroend
!endif
