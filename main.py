#!/usr/bin/env python3
"""Briefing pessoal de notícias e exposições de carteira via feeds RSS."""
from __future__ import annotations

import argparse
import concurrent.futures
import email.utils
import html
import json
import os
import re
import smtplib
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOCAL_TZ = ZoneInfo("America/Sao_Paulo")
USER_AGENT = "BriefingMercadoPessoal/2.0 (RSS reader)"

# Google News RSS é usado como mecanismo de descoberta. Os domínios pedidos
# recebem feeds próprios; fontes em português têm preferência na classificação.
FEEDS = [
    ("Brasil", "site:infomoney.com.br (economia OR política OR finanças OR empresas OR mercado)", "pt-BR", "BR", "BR:pt-419"),
    ("Brasil", "site:moneytimes.com.br (economia OR política OR finanças OR empresas OR mercado)", "pt-BR", "BR", "BR:pt-419"),
    ("Brasil", "site:cnnbrasil.com.br (economia OR política OR finanças OR empresas OR mercado)", "pt-BR", "BR", "BR:pt-419"),
    ("Brasil", "(economia OR política OR finanças) Brasil (juros OR empresas OR mercado OR orçamento)", "pt-BR", "BR", "BR:pt-419"),
    ("Global", "site:cnn.com/business (economy OR markets OR finance OR policy)", "en-US", "US", "US:en"),
    ("Global", "site:reuters.com (economy OR finance OR markets OR central bank)", "en-US", "US", "US:en"),
    ("Global", "site:ft.com (economy OR markets OR finance OR policy)", "en-US", "US", "US:en"),
    ("Global", "site:cnbc.com (economy OR markets OR finance OR policy)", "en-US", "US", "US:en"),
    ("Global", "site:apnews.com business economy finance markets", "en-US", "US", "US:en"),
    ("Global", "site:bbc.com/news/business economy markets finance", "en-US", "GB", "GB:en"),
    ("Global", "site:bloomberg.com (economy OR markets OR finance OR policy)", "en-US", "US", "US:en"),
]

CATEGORY_KEYWORDS = {
    "Ações e empresas": ("ações", "ibovespa", "bolsa", "stock", "stocks", "shares", "empresa", "company", "earnings", "lucro", "b3", "nasdaq", "s&p 500", "dow jones", "balanço", "resultado", "aquisição", "fusão"),
    "Macroeconomia e política": ("juros", "inflação", "copom", "banco central", "fed", "federal reserve", "economia", "economy", "câmbio", "dólar", "dollar", "pib", "gdp", "desemprego", "fiscal", "governo", "congresso", "eleição", "tarifa", "sanção"),
    "Commodities": ("petróleo", "oil", "ouro", "gold", "minério", "iron ore", "soja", "milho", "wheat", "commodity", "commodities", "agro", "sugar", "copper"),
    "Cripto": ("bitcoin", "crypto", "cripto", "ethereum", "blockchain", "token", "stablecoin"),
}

# Sinais de acontecimento com potencial de mudar fundamentos, política econômica,
# custo de capital, crédito, regulação ou oferta. Movimento percentual isolado não basta.
CATALYST_TERMS = (
    "decisão", "decide", "anuncia", "anunciou", "aprova", "aprovou", "rejeita", "rejeitou",
    "proposta", "projeto de lei", "lei", "regulação", "regulador", "investigação", "sanção", "tarifa",
    "resultado", "balanço", "lucro", "prejuízo", "guidance", "projeção", "previsão", "corte de juros",
    "aumento de juros", "taxa de juros", "inflação", "copom", "banco central", "federal reserve", "fed",
    "default", "calote", "recuperação judicial", "falência", "rebaixamento", "rating", "dívida", "emissão",
    "aquisição", "fusão", "compra", "venda", "demissão", "greve", "concessão", "privatização", "mudança",
    "crise", "guerra", "ataque", "bloqueio", "interrupção", "escassez", "supply", "deal", "earnings",
    "results", "guidance", "acquisition", "merger", "bankruptcy", "default", "downgrade", "upgrade",
    "rate decision", "interest rate", "inflation", "central bank", "regulation", "law", "bill", "election",
    "sanction", "tariff", "lawsuit", "investigation", "strike", "layoff", "outage", "disruption", "war",
    "attack", "ceasefire", "debt", "bond", "credit", "oil supply", "production cut", "deal", "approval",
)

NOISE_RE = re.compile(r"(?:\b\d+(?:[,.]\d+)?\s*%|\b(?:sobe|cai|avança|recua|alta|baixa|up|down|gains?|falls?)\b)", re.I)
EVENT_OVERRIDE_TERMS = ("decisão", "anuncia", "anunciou", "aprov", "rejeit", "resultado", "balanço", "default", "calote", "rating", "dívida", "aquisição", "fusão", "regulação", "lei", "sanção", "tarifa", "guerra", "crise", "earnings", "results", "deal", "default", "bankruptcy", "rate decision", "regulation", "law", "sanction", "tariff", "war", "crisis", "approval")

@dataclass(frozen=True)
class Story:
    title: str
    link: str
    source: str
    summary: str
    published: datetime | None
    region: str
    category: str


def load_env(path: Path = ROOT / ".env") -> None:
    """Carrega KEY=VALUE local; variáveis já exportadas mantêm prioridade."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", " ".join(element.itertext())).strip()


def _published(item: ET.Element) -> datetime | None:
    raw = _text(item.find("{*}pubDate")) or _text(item.find("{*}published"))
    if not raw:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _category(text: str) -> str:
    lowered = text.casefold()
    scores = {name: sum(word in lowered for word in words) for name, words in CATEGORY_KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "Economia e mercados"


def build_feed_url(query: str, region: str, lang: str | None = None, country: str | None = None, edition: str | None = None) -> str:
    if region == "Brasil":
        params = {"q": query, "hl": lang or "pt-BR", "gl": country or "BR", "ceid": edition or "BR:pt-419"}
    else:
        params = {"q": query, "hl": lang or "en-US", "gl": country or "US", "ceid": edition or "US:en"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def fetch_feed(url: str, region: str, timeout: int = 15) -> list[Story]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read(3_000_000)
    root = ET.fromstring(payload)
    stories: list[Story] = []
    for item in root.findall(".//item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        if not title or not link or not link.startswith(("https://", "http://")):
            continue
        source = _text(item.find("{*}source")) or "Google News"
        summary = _text(item.find("{*}description"))
        summary = html.unescape(re.sub(r"<[^>]+>", " ", summary))
        summary = re.sub(r"\s+", " ", summary).strip()
        title = re.sub(r"\s+-\s+[^-]{2,80}$", "", title).strip() or title
        stories.append(Story(title, link, source, summary, _published(item), region, _category(title + " " + summary)))
    return stories


def collect_news() -> tuple[list[Story], list[str]]:
    def one(feed: tuple[str, str, str, str, str]) -> tuple[list[Story], str | None]:
        region, query, lang, country, edition = feed
        try:
            return fetch_feed(build_feed_url(query, region, lang, country, edition), region), None
        except Exception as exc:
            return [], f"{region} — {query}: {type(exc).__name__}: {exc}"

    collected: list[Story] = []
    errors: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for stories, error in pool.map(one, FEEDS):
            collected.extend(stories)
            if error:
                errors.append(error)
    return collected, errors


def load_portfolio(path: Path = ROOT / "portfolio.json") -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("holdings"), list) or not data["holdings"]:
        raise ValueError("portfolio.json precisa conter uma lista não vazia de posições.")
    for holding in data["holdings"]:
        if not holding.get("id") or not holding.get("name") or float(holding.get("value_brl", 0)) <= 0:
            raise ValueError("Cada posição deve ter id, name e value_brl positivo.")
    return data


def _portfolio_index(portfolio: dict) -> dict[str, dict]:
    return {holding["id"]: holding for holding in portfolio["holdings"]}


def risk_matrix(portfolio: dict) -> list[dict]:
    """Matriz indicativa de exposições sobrepostas; não estima perda ou VaR."""
    risks = portfolio.get("risk_factors", [])
    holdings = _portfolio_index(portfolio)
    total = sum(float(h["value_brl"]) for h in holdings.values())
    matrix = []
    for risk in risks:
        members = [holdings[item] for item in risk["holding_ids"] if item in holdings]
        amount = sum(float(h["value_brl"]) for h in members)
        matrix.append({**risk, "amount_brl": amount, "share_pct": amount / total * 100 if total else 0,
                       "holdings": [h["name"] for h in members]})
    return matrix


def _source_score(source: str) -> float:
    source = source.casefold()
    if any(term in source for term in ("infomoney", "money times", "moneytimes", "cnn brasil")):
        return 3.0
    if any(term in source for term in ("reuters", "financial times", "the financial times", "cnbc", "associated press", "ap news", "bbc", "bloomberg", "cnn")):
        return 1.8
    return 0.6


def _risk_hits(text: str, portfolio: dict) -> list[str]:
    lowered = text.casefold()
    hits = []
    for risk in portfolio.get("risk_factors", []):
        if any(term.casefold() in lowered for term in risk.get("keywords", [])):
            hits.append(risk["name"])
    return hits


def _relevance(story: Story, portfolio: dict) -> float:
    text = (story.title + " " + story.summary).casefold()
    hits = sum(term in text for term in CATALYST_TERMS)
    portfolio_hits = _risk_hits(text, portfolio)
    event_override = any(term in text for term in EVENT_OVERRIDE_TERMS)
    # Elimina boletins de variação percentual/fechamento sem acontecimento explicativo.
    if NOISE_RE.search(story.title) and not event_override:
        return 0.0
    if hits == 0 and not portfolio_hits:
        return 0.0
    return min(hits, 5) * 1.25 + min(len(portfolio_hits), 3) * 1.4 + _source_score(story.source) + (0.3 if story.region == "Brasil" else 0.0)


def yesterday_local(now: datetime | None = None) -> date:
    local_now = (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ)
    return local_now.date() - timedelta(days=1)


def select_top(stories: list[Story], limit: int = 10, now: datetime | None = None,
               target_date: date | None = None, portfolio: dict | None = None) -> list[Story]:
    now = now or datetime.now(timezone.utc)
    target_date = target_date or yesterday_local(now)
    portfolio = portfolio or {"risk_factors": []}
    unique: dict[str, Story] = {}
    for story in stories:
        if story.published is None or story.published.astimezone(LOCAL_TZ).date() != target_date:
            continue
        key = re.sub(r"\W+", " ", story.title.casefold()).strip()
        if key and (key not in unique or (story.published or datetime.min.replace(tzinfo=timezone.utc)) > (unique[key].published or datetime.min.replace(tzinfo=timezone.utc))):
            unique[key] = story
    candidates = [s for s in unique.values() if _relevance(s, portfolio) > 0]
    candidates.sort(key=lambda s: (_relevance(s, portfolio), s.published or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    selected: list[Story] = []
    source_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    global_count = 0
    for story in candidates:
        source_key = story.source.casefold()
        is_global = story.region != "Brasil"
        if source_counts.get(source_key, 0) >= 2 or (is_global and global_count >= 3) or category_counts.get(story.category, 0) >= 2:
            continue
        selected.append(story)
        source_counts[source_key] = source_counts.get(source_key, 0) + 1
        category_counts[story.category] = category_counts.get(story.category, 0) + 1
        global_count += int(is_global)
        if len(selected) >= limit:
            return selected
    # Se a primeira passada não atingir a meta, relaxa diversidade de fonte/tema/região.
    # Nunca relaxa data, deduplicação ou critérios de relevância.
    for story in candidates:
        if story not in selected:
            selected.append(story)
            if len(selected) >= limit:
                break
    return selected


def concise_summary(story: Story) -> str:
    summary = story.summary
    if not summary or summary.casefold() == story.title.casefold():
        return "O feed não forneceu descrição; consulte a matéria original no link."
    if len(summary) > 300:
        summary = summary[:297].rsplit(" ", 1)[0] + "..."
    return summary


def _impact_line(story: Story, matrix: list[dict]) -> str | None:
    matches = [risk["name"] for risk in matrix if any(term.casefold() in (story.title + " " + story.summary).casefold() for term in risk.get("keywords", []))]
    return "Possível canal de impacto: " + "; ".join(matches[:3]) + ". (Inferência para monitoramento.)" if matches else None


def render_email(stories: list[Story], errors: list[str], portfolio: dict | None = None,
                 now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(LOCAL_TZ)
    report_date = yesterday_local(now)
    portfolio = portfolio or {"as_of": "não informado", "holdings": [], "risk_factors": []}
    matrix = risk_matrix(portfolio) if portfolio.get("holdings") else []
    subject = f"Briefing de mercado | notícias de {report_date:%d/%m/%Y}"
    plain = [f"BRIEFING DE MERCADO — {report_date:%d/%m/%Y}", "", "Pelo menos dez notícias relevantes do dia anterior, quando houver resultados | Brasil e exterior", ""]
    cards = []
    for index, story in enumerate(stories, 1):
        published = story.published.astimezone(LOCAL_TZ).strftime("%d/%m %H:%M BRT") if story.published else "Horário não informado"
        impact = _impact_line(story, matrix) if matrix else None
        plain.extend([f"{index}. {story.title}", f"{story.category} · {story.region} · {story.source} · {published}", concise_summary(story)])
        if impact:
            plain.append(impact)
        plain.extend([story.link, ""])
        impact_html = f'<p style="font-size:13px;color:#78520a;margin:8px 0 0"><b>{html.escape(impact)}</b></p>' if impact else ""
        cards.append(f'''<li style="margin:0 0 22px;padding:0 0 18px;border-bottom:1px solid #e6e8ec"><h2 style="font-size:18px;line-height:1.4;margin:0 0 8px"><a style="color:#16324f" href="{html.escape(story.link, quote=True)}">{html.escape(story.title)}</a></h2><div style="color:#536273;font-size:13px;margin-bottom:8px">{html.escape(story.category)} · {html.escape(story.region)} · {html.escape(story.source)} · {html.escape(published)}</div><p style="font-size:15px;line-height:1.55;margin:0">{html.escape(concise_summary(story))}</p>{impact_html}</li>''')
    if not stories:
        plain.append("Nenhuma notícia que atendesse aos critérios de data e relevância foi selecionada; nenhum e-mail de briefing é enviado.")
        cards.append("<li>Nenhuma notícia relevante foi selecionada para a data de referência.</li>")
    plain.extend(["", "MATRIZ INDICATIVA DE EXPOSIÇÃO DA CARTEIRA", f"Posições extraídas do extrato com data de referência {portfolio.get('as_of', 'não informada')}. Exposições entre fatores se sobrepõem e não devem ser somadas."])
    matrix_rows = []
    if matrix:
        for risk in matrix:
            instruments = ", ".join(risk["holdings"])
            plain.append(f"- {risk['name']}: R$ {risk['amount_brl']:,.2f} ({risk['share_pct']:.1f}%) — {instruments}. Monitorar: {risk.get('triggers', '')}")
            matrix_rows.append(f'<tr><td style="padding:9px;border-bottom:1px solid #e6e8ec"><b>{html.escape(risk["name"])}</b><br><span style="color:#536273">{html.escape(instruments)}</span></td><td style="padding:9px;border-bottom:1px solid #e6e8ec;white-space:nowrap">R$ {risk["amount_brl"]:,.2f}<br>{risk["share_pct"]:.1f}%</td><td style="padding:9px;border-bottom:1px solid #e6e8ec">{html.escape(risk.get("triggers", ""))}</td></tr>')
    else:
        plain.append("Carteira não carregada. Verifique portfolio.json.")
    if errors:
        plain.extend(["", f"Aviso: {len(errors)} feed(s) não responderam; o resumo usa as fontes disponíveis."])
    plain.extend(["", "A matriz é uma leitura descritiva de exposição, não mede probabilidade/perda (VaR), não confirma garantias e não constitui recomendação de investimento. Os valores podem ficar desatualizados; atualize portfolio.json após movimentações.", "As descrições vêm dos feeds e podem estar incompletas. Consulte a fonte original."])
    notice = f"<p style='color:#8a5a00;font-size:13px'>Aviso: {len(errors)} feed(s) não responderam; o resumo usa as fontes disponíveis.</p>" if errors else ""
    matrix_html = (f'''<h2 style="font-size:19px;margin:28px 0 8px">Matriz indicativa de exposição</h2><p style="font-size:12px;color:#536273">Extrato de {html.escape(str(portfolio.get('as_of', 'data não informada')))}. Exposições sobrepostas; percentuais não somam 100%.</p><div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;font-size:13px"><thead><tr><th align="left" style="padding:8px;border-bottom:2px solid #ccd3da">Fator de risco / posições</th><th align="left" style="padding:8px;border-bottom:2px solid #ccd3da">Exposição</th><th align="left" style="padding:8px;border-bottom:2px solid #ccd3da">Gatilhos a acompanhar</th></tr></thead><tbody>{''.join(matrix_rows)}</tbody></table></div>''') if matrix_rows else ""
    disclaimer = "A matriz é uma leitura descritiva de exposições, não estima perda, probabilidade ou VaR; não confirma garantias e não constitui recomendação. Os valores podem estar desatualizados."
    body = f'''<!doctype html><html><body style="margin:0;background:#f3f5f7;font-family:Arial,sans-serif;color:#18212b"><main style="max-width:760px;margin:24px auto;background:#fff;padding:28px 32px;border-radius:12px"><div style="font-size:12px;letter-spacing:1px;color:#536273">BRIEFING PESSOAL</div><h1 style="font-size:25px;margin:8px 0 4px">Mercados em foco</h1><p style="color:#536273;margin-top:0">Notícias de {report_date:%d/%m/%Y} · enviado {local_now:%d/%m/%Y} · Brasil e exterior</p>{notice}<ol style="padding-left:22px">{''.join(cards)}</ol>{matrix_html}<p style="font-size:12px;line-height:1.5;color:#66717d;border-top:1px solid #e6e8ec;padding-top:14px">{html.escape(disclaimer)} Atualize portfolio.json quando sua carteira mudar. As descrições podem estar incompletas; consulte as fontes originais. Conteúdo informativo.</p></main></body></html>'''
    return subject, "\n".join(plain) + "\n\n" + body


def send_email(subject: str, body: str) -> None:
    required = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Configure no .env ou nos Secrets do Colab: " + ", ".join(missing))
    sender = os.environ["GMAIL_ADDRESS"]
    recipient = os.environ.get("EMAIL_TO", sender)
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    plain, separator, html_body = body.partition("<!doctype html>")
    message.set_content(plain)
    if separator:
        message.add_alternative("<!doctype html>" + html_body, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(sender, os.environ["GMAIL_APP_PASSWORD"].replace(" ", ""))
        smtp.send_message(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Envia até 10 notícias relevantes do dia anterior e uma matriz indicativa da carteira.")
    parser.add_argument("--dry-run", action="store_true", help="Exibe o briefing sem enviar e-mail")
    parser.add_argument("--date", help="Data de referência YYYY-MM-DD; padrão: ontem em horário de Brasília")
    args = parser.parse_args()
    load_env()
    try:
        portfolio = load_portfolio()
        target_date = date.fromisoformat(args.date) if args.date else None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Erro na configuração da carteira ou data: {exc}", file=sys.stderr)
        return 2
    stories, errors = collect_news()
    top = select_top(stories, now=datetime.now(timezone.utc), target_date=target_date, portfolio=portfolio)
    subject, body = render_email(top, errors, portfolio)
    plain = body.split("<!doctype html>", 1)[0]
    if args.dry_run:
        print(plain)
        print(f"\nNotícias coletadas: {len(stories)} | relevantes selecionadas: {len(top)} | feeds com falha: {len(errors)}")
        return 0 if top else 1
    if not top:
        print("Não foi encontrada notícia relevante datada do dia de referência; o e-mail não foi enviado.", file=sys.stderr)
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    send_email(subject, body)
    print(f"Briefing enviado para {os.environ.get('EMAIL_TO') or os.environ.get('GMAIL_ADDRESS')} ({len(top)} notícias).")
    if errors:
        print(f"Aviso: {len(errors)} feed(s) falharam.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
