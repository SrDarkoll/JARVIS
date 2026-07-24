"""LangChain tool adapters for the YouTube service."""

from langchain_core.tools import tool
from modules.youtube import service


@tool
def reproducir_en_youtube(query: str) -> str:
    """Busca en YouTube, analiza los títulos de los resultados y reproduce el video más parecido a la búsqueda."""
    return service.play(query)
