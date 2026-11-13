# Anotador de reuniões local — design

Data: 2026-11-13

## Para que serve

A Anita quer anotações das reuniões de Google Meet **só para ela**, sem bot na chamada e
sem que os outros participantes percebam. O áudio nunca sai do computador; apenas o
texto da transcrição é enviado ao Claude para gerar o resumo.

## Objetivos
- Começar a gravar sozinho quando uma reunião do Meet começa no **Opera**, e parar sozinho quando ela termina.
- Depois da reunião: transcrever localmente (português) e gerar resumo com o Claude.
- Entregar um arquivo por reunião com resumo, decisões, pendências e transcrição.
- Funcionar sem a Anita lembrar de nada (abre junto com o Windows).

## Fora do escopo
- Transcrição ao vivo durante a reunião.
- Identificar cada pessoa pelo nome (só "Eu" e "Outros").
- Outros navegadores ou apps (Teams, Zoom, Edge).
- Modelo de IA local para resumir.

## O que faria isso dar errado (mesmo funcionando)
- Qualquer coisa visível para os participantes: bot, entrada na chamada, aviso do Meet.
- Um aviso que aparece ou reaparece **enquanto ela compartilha a tela**.
- O áudio sair do computador.

## Restrições
- Notebook: i7-1360P, 16 GB RAM, sem GPU NVIDIA → transcrição só na CPU, depois da reunião.
- Python 3.14 instalado; algumas bibliotecas podem exigir Python 3.12 (instalar via `py`/winget se preciso).
- Claude CLI já instalado (`claude.exe`) → resumo via `claude -p`, usando a conta atual, sem chave de API.

## Abordagens consideradas (para o start)
1. **Automático com aviso** — detecta o Opera usando o microfone + aba do Meet e grava sozinho. **Escolhida**: não depende de lembrar.
2. Manual com botão — previsível, mas fácil esquecer de ligar/desligar. Fica como reserva no menu do ícone.
3. Pela agenda do Google — reuniões raramente seguem o horário; mais configuração. Descartada.

## Como funciona

### 1. Programa em segundo plano
- Ícone pequeno na bandeja do Windows (perto do relógio), aberto automaticamente ao ligar o PC
  (atalho na pasta Inicializar, rodando sem janela de console).
- Menu do ícone: Iniciar gravação manual · Parar gravação · Abrir pasta de reuniões · Sair.

### 2. Detecção da reunião
- A cada ~3 s, consulta o registro do Windows que diz se o `opera.exe` está usando o microfone
  (`CapabilityAccessManager\ConsentStore\microphone\NonPackaged`, `LastUsedTimeStop = 0` = em uso).
- **Começa** quando o Opera passa a usar o microfone **e** existe uma janela do Opera com título
  contendo "Meet" (verificado por até 30 s após o microfone ligar).
- **Para** quando o Opera libera o microfone por mais de 15 s seguidos (tolerância para quedas rápidas).
- Mutar no Meet não libera o microfone, então mutar não interrompe a gravação.

### 3. Aviso de gravação
- Ao começar, aparece uma janelinha discreta no canto da tela: **"Gravando reunião"** com os botões
  **[OK]** e **[Cancelar gravação]**.
- Fecha ao clicar em OK, ou sozinha após 15 s. **Não reaparece** durante aquela reunião.
- Nenhum outro aviso aparece durante a reunião.
- Cancelar descarta o que foi gravado.

### 4. Gravação
- Duas faixas em WAV numa pasta temporária: som do sistema (WASAPI loopback = os outros) e microfone (= ela).
- Bibliotecas sugeridas: `pyaudiowpatch` (ou `soundcard`).

### 5. Transcrição (depois da reunião)
- `faster-whisper`, idioma português, na CPU (int8). Modelo inicial `small`; trocar para `medium` se a
  qualidade não bastar (mais lento — estimativa 15–40 min por hora de reunião, a confirmar em teste).
- Cada faixa transcrita separadamente e intercalada por horário, marcando "Eu" / "Outros".

### 6. Resumo
- Envia a transcrição ao `claude -p` com um prompt em português pedindo: resumo, decisões,
  pendências (com responsável, quando citado) e pontos em aberto.
- Se o Claude falhar (sem internet etc.), salva só a transcrição e tenta o resumo depois.

### 7. Resultado
- Arquivo `Documentos\Reuniões\AAAA-MM-DD HHhMM - <título da aba do Meet>.md` com resumo no topo e
  transcrição completa embaixo.
- Quando fica pronto: notificação **silenciosa** (vai direto para a central de notificações, sem pop-up),
  para não aparecer durante um compartilhamento de tela em outra reunião.

## Decisões minhas (para a Anita conferir)
- Áudio WAV é **apagado** depois que a transcrição dá certo (economiza espaço e reduz risco). Transcrição e resumo ficam.
- O aviso some sozinho após 15 s, além do botão OK.
- Apenas Opera é monitorado (configurável num arquivo de configuração, caso mude no futuro).
- Pasta de saída: `Documentos\Reuniões`.

## Ponto de atenção
- Se ela compartilhar a **tela inteira**, o ícone na bandeja fica visível (ele não muda de aparência ao gravar,
  para não chamar atenção). Compartilhando só uma aba ou janela, nada aparece.

## Deixado para quem for construir
- Escolha exata das bibliotecas de áudio e ajuste de sincronia entre as duas faixas.
- Texto do prompt de resumo.
- Tratamento de reuniões muito longas (dividir transcrição se exceder o limite do Claude).
