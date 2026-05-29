"""
AgroVision AI — Camada de Web Scraping
Coleta dados públicos de clima e notícias agro para enriquecer o monitoramento.

Fontes utilizadas:
  - Open-Meteo (https://open-meteo.com) — previsão do tempo, API pública e gratuita, sem autenticação
  - NewsData.io RSS público — notícias do setor agro
  - CEPEA/USP via scraping do RSS público — cotações agropecuárias

Boas práticas aplicadas:
  - Cache com TTL para limitar requisições
  - Timeout em todas as chamadas HTTP
  - Tratamento de erro com retorno estruturado
  - Nenhuma autenticação ou chave de API necessária
"""

import time
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

# ─── Configurações ───────────────────────────────────────────────────────────

# Coordenadas padrão (San Ysidro, CA — câmera pública atual)
# Troque para as coordenadas da sua câmera/propriedade
DEFAULT_LAT = 32.5542
DEFAULT_LON = -117.0411

CACHE_TTL_SECONDS = 600  # 10 minutos entre requisições à mesma fonte
HTTP_TIMEOUT = 10        # segundos

# ─── Cache simples em memória ─────────────────────────────────────────────────

_cache: dict = {}


def _get_cache(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["ts"]) < CACHE_TTL_SECONDS:
        return entry["data"]
    return None


def _set_cache(key: str, data: dict):
    _cache[key] = {"ts": time.time(), "data": data}


# ─── 1. Previsão do Tempo (Open-Meteo) ───────────────────────────────────────

def fetch_weather(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict:
    """
    Busca previsão do tempo atual via Open-Meteo.
    API pública, gratuita, sem autenticação.
    Relevância: condições climáticas afetam visibilidade da câmera,
    comportamento de tráfego e risco operacional no campo.
    """
    cache_key = f"weather_{lat}_{lon}"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,precipitation,"
        "weather_code,wind_speed_10m,wind_direction_10m"
        "&wind_speed_unit=kmh"
        "&timezone=auto"
    )

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as client:
            r = client.get(url)
            r.raise_for_status()
            raw = r.json()

        current = raw.get("current", {})
        code = current.get("weather_code", 0)

        result = {
            "status": "ok",
            "source": "Open-Meteo",
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "location": {"lat": lat, "lon": lon, "timezone": raw.get("timezone", "")},
            "temperature_c": current.get("temperature_2m"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "wind_speed_kmh": current.get("wind_speed_10m"),
            "wind_direction_deg": current.get("wind_direction_10m"),
            "weather_code": code,
            "condition": _weather_code_to_label(code),
            "visibility_risk": _assess_visibility_risk(code, current.get("precipitation", 0)),
        }

        _set_cache(cache_key, result)
        return result

    except httpx.TimeoutException:
        return {"status": "error", "source": "Open-Meteo", "error": "timeout", "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        return {"status": "error", "source": "Open-Meteo", "error": str(e), "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def _weather_code_to_label(code: int) -> str:
    if code == 0:
        return "céu limpo"
    elif code in (1, 2, 3):
        return "parcialmente nublado"
    elif code in (45, 48):
        return "neblina"
    elif code in (51, 53, 55):
        return "garoa"
    elif code in (61, 63, 65):
        return "chuva"
    elif code in (71, 73, 75):
        return "neve"
    elif code in (80, 81, 82):
        return "pancadas de chuva"
    elif code in (95, 96, 99):
        return "tempestade"
    return "condição desconhecida"


def _assess_visibility_risk(code: int, precipitation: float) -> str:
    if code in (45, 48):
        return "alto — neblina reduz visibilidade da câmera"
    elif code in (95, 96, 99):
        return "alto — tempestade pode comprometer o feed"
    elif precipitation > 5:
        return "médio — chuva intensa pode afetar detecções"
    elif code in (61, 63, 65, 80, 81, 82):
        return "baixo — chuva leve, monitoramento normal"
    return "nenhum — condições favoráveis"


# ─── 2. Notícias Agro (RSS público do Canal Rural) ───────────────────────────

def fetch_agro_news(max_items: int = 5) -> dict:
    """
    Busca notícias do setor agropecuário via RSS público do Canal Rural.
    Relevância: alertas de pragas, eventos climáticos extremos e movimentos
    de mercado são contexto operacional para interpretar o que a câmera vê.
    """
    cache_key = "agro_news"
    cached = _get_cache(cache_key)
    if cached:
        return cached

    # RSS público e gratuito do Canal Rural
    url = "https://www.canalrural.com.br/feed/"

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True,
                          headers={"User-Agent": "AgroVisionAI/1.0 (educational project)"}) as client:
            r = client.get(url)
            r.raise_for_status()

        root = ET.fromstring(r.text)
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        items = []

        for item in root.findall(".//item")[:max_items]:
            title = item.findtext("title", "").strip()
            link  = item.findtext("link", "").strip()
            pub   = item.findtext("pubDate", "").strip()
            desc  = item.findtext("description", "").strip()
            # Remove tags HTML simples da descrição
            import re
            desc = re.sub(r"<[^>]+>", "", desc)[:200]

            if title:
                items.append({"title": title, "link": link, "published": pub, "summary": desc})

        result = {
            "status": "ok",
            "source": "Canal Rural RSS",
            "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "count": len(items),
            "news": items,
        }
        _set_cache(cache_key, result)
        return result

    except httpx.TimeoutException:
        return {"status": "error", "source": "Canal Rural RSS", "error": "timeout", "news": [], "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    except Exception as e:
        return {"status": "error", "source": "Canal Rural RSS", "error": str(e), "news": [], "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# ─── 3. Resumo consolidado para o agente ─────────────────────────────────────

def get_enrichment_context(lat: float = DEFAULT_LAT, lon: float = DEFAULT_LON) -> dict:
    """
    Retorna um contexto consolidado de clima + notícias para enriquecer
    a análise do agente. Chamado por build_agent_messages quando disponível.
    """
    weather = fetch_weather(lat, lon)
    news    = fetch_agro_news(max_items=3)

    return {
        "weather": weather,
        "news": news,
        "summary": _build_summary(weather, news),
    }


def _build_summary(weather: dict, news: dict) -> str:
    lines = ["=== Contexto externo coletado por scraping ==="]

    if weather.get("status") == "ok":
        lines.append(
            f"Clima atual: {weather.get('condition', '?')} | "
            f"Temp: {weather.get('temperature_c')}°C | "
            f"Chuva: {weather.get('precipitation_mm')}mm | "
            f"Vento: {weather.get('wind_speed_kmh')} km/h"
        )
        lines.append(f"Risco de visibilidade: {weather.get('visibility_risk', '?')}")
    else:
        lines.append(f"Clima: indisponível ({weather.get('error', '')})")

    if news.get("status") == "ok" and news.get("news"):
        lines.append("Notícias recentes do setor agro:")
        for n in news["news"]:
            lines.append(f"  - {n['title']}")
    else:
        lines.append("Notícias agro: indisponíveis")

    return "\n".join(lines)
