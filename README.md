# Briefing diário de notícias e carteira

Projeto pessoal em Python, sem interface. Às 07:00 (horário de Brasília), coleta notícias publicadas no **dia anterior**, seleciona dez acontecimentos relevantes quando houver quantidade suficiente de matérias que atendam aos critérios e envia o briefing por Gmail. O e-mail inclui links/fontes, descrições disponíveis nos feeds e uma matriz indicativa que cruza fatores de risco com as posições informadas.

## Escopo e critérios

- **Fontes em português priorizadas:** InfoMoney, CNN Brasil e Money Times. A busca internacional inclui CNN Business (a antiga marca CNN Money), Reuters, Financial Times, CNBC, Associated Press, BBC e Bloomberg, quando houver resultados nos feeds.
- **Assuntos:** economia, política econômica, finanças, empresas e os ativos/fatores identificados na carteira.
- **Relevância:** favorece decisões de bancos centrais e governos, inflação/fiscal, resultados, mudanças regulatórias, crédito, eventos corporativos, oferta e riscos geopolíticos. Manchetes de mera oscilação percentual/fechamento, sem notícia explicativa, são filtradas.
- **Janela:** data civil anterior em `America/Sao_Paulo`; matérias sem horário/publicação reconhecível são descartadas para não misturar datas.
- **Formato:** dez notícias concisas por edição sempre que houver dez matérias distintas que atendam aos critérios de data e relevância; inclui fonte, horário, link e possível canal de impacto quando houver correspondência com um fator de risco. Se houver menos matérias qualificadas, não completa a lista com notícias irrelevantes. O canal é uma associação indicativa, não previsão.
- **Carteira:** `portfolio.json` foi preenchido com os saldos a mercado extraídos do extrato enviado, atualizado em 30/09/2026. A matriz agrega exposições que podem se sobrepor; percentuais não somam 100%. Ela não calcula VaR, probabilidade de perda ou cobertura/garantia de ativos.
- Os resumos vêm das descrições dos feeds e podem estar incompletos. Google News RSS é uma camada de descoberta e não garante cobertura exaustiva nem disponibilidade permanente. Conteúdo informativo, não recomendação de investimento.

## Requisitos

Python 3.10 ou superior. Sem dependências externas; usa a biblioteca padrão. A máquina/ambiente precisa de internet para consultar feeds.

## Colocar no Google Colab

1. Baixe o projeto completo e envie **todos os arquivos**, mantendo `main.py` e `portfolio.json` juntos no ambiente do Colab. Se enviar um ZIP, extraia-o e entre na pasta que contém ambos.
2. Na lateral do Colab, abra **Secrets** (ícone de chave) e crie `GMAIL_ADDRESS` (seu Gmail) e `GMAIL_APP_PASSWORD` (senha de app do Google). Ative o acesso desses segredos para o notebook. Nunca use nem compartilhe a senha normal da conta.
3. Em uma célula, carregue os segredos:

   ```python
   from google.colab import userdata
   import os
   os.environ["GMAIL_ADDRESS"] = userdata.get("GMAIL_ADDRESS")
   os.environ["GMAIL_APP_PASSWORD"] = userdata.get("GMAIL_APP_PASSWORD").replace(" ", "")
   ```

   Opcionalmente, crie o Secret `EMAIL_TO` para enviar a outro destinatário e carregue-o com `os.environ["EMAIL_TO"] = userdata.get("EMAIL_TO")`. Sem esse segredo, o envio vai para o próprio Gmail remetente.
4. Teste sem envio:

   ```python
   !python main.py --dry-run
   ```

   A busca tenta selecionar pelo menos dez notícias da data de referência (ontem em Brasília), sem preencher a lista com matérias fora dos critérios apenas para atingir a quantidade. Para testar outra data, use `!python main.py --dry-run --date AAAA-MM-DD`.
5. Depois de conferir o conteúdo, envie manualmente com `!python main.py`.

Uma sessão comum do Colab não garante execução automática diária: precisa estar ativa na hora da execução. Para automatizar às 07:00, use um computador sempre ligado com o agendador do sistema ou um serviço de execução agendada. Não coloque credenciais diretamente em células nem em arquivos compartilhados.

## Executar localmente e configurar Gmail

1. Ative a verificação em duas etapas na Conta Google e gere uma **senha de app** para este programa (a opção pode não estar disponível em contas gerenciadas).
2. Copie `.env.example` para `.env` e informe `GMAIL_ADDRESS` e `GMAIL_APP_PASSWORD`. Opcionalmente, configure `EMAIL_TO`. O `.env` já está excluído pelo `.gitignore`; mantenha-o privado.
3. No diretório do projeto, rode `python main.py --dry-run` para conferir e `python main.py` para enviar.

## Agendar às 07:00 de Brasília

### Linux/macOS com cron

Edite o crontab (`crontab -e`) e use o local real do projeto e do Python:

```cron
CRON_TZ=America/Sao_Paulo
0 7 * * * cd /caminho/para/briefing-mercado && /usr/bin/python3 main.py >> briefing.log 2>&1
```

O computador precisa estar ligado e conectado à internet. Se `CRON_TZ` não for aceito pelo sistema, ajuste o fuso do sistema para Brasília ou use o agendador nativo.

### Windows

No Agendador de Tarefas, crie uma tarefa diária para 07:00; a ação executa `python` e recebe o caminho completo de `main.py` como argumento. Defina a pasta do projeto como diretório inicial. O computador precisa estar ligado/conectado, e o fuso do Windows deve ser Brasília.

### GitHub Actions

O workflow está em `.github/workflows/briefing.yml` e agenda 10:00 UTC (07:00 em Brasília). Para habilitá-lo, envie o projeto atualizado ao branch padrão do repositório e cadastre em **Settings → Secrets and variables → Actions → New repository secret** os três segredos com estes nomes exatos:

- `GMAIL_ADDRESS`: conta Gmail remetente;
- `GMAIL_APP_PASSWORD`: senha de app da conta remetente;
- `EMAIL_TO`: endereço que deve receber o briefing.

O workflow valida os três antes de executar; se `EMAIL_TO` estiver ausente, a execução falha sem mandar a mensagem para o remetente por engano. A ação manual em **Actions → Briefing diário → Run workflow** envia um e-mail real ao endereço `EMAIL_TO`.

## Manter a carteira atualizada

Após compras, vendas ou alterações relevantes, atualize em `portfolio.json` os valores `value_brl`, os nomes/identificadores e, se necessário, os fatores/termos de risco. A fotografia atual é apenas a data do extrato analisado, não uma consulta automática à XP. Não compartilhe o extrato ou credenciais em repositórios públicos.

## Diagnóstico

- Se não houver notícias que atendam à data e relevância, o programa **não envia e-mail vazio**; confira o `--dry-run` e os feeds disponíveis.
- Se houver erros parciais, as fontes que responderam ainda podem contribuir e o e-mail indicará a falha.
- Erros de envio Gmail normalmente indicam endereço incorreto, ausência de senha de app ou restrição da conta.
