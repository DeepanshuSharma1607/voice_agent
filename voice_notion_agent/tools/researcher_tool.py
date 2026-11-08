from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_tavily import TavilySearch
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import config

_logger = logging.getLogger(__name__)

_llm = ChatGoogleGenerativeAI(
    model=config.CHAT_MODEL,
    temperature=0.3
)

_fallback_llm = ChatGoogleGenerativeAI(
    model="gemini-3.5-flash-lite",
    temperature=0.3
)

_tavily = TavilySearch(api_key = config.TAVILY_API_KEY , max_results = 3)

@tool
def research(topic : str) -> str:
    """
    Reaserch the topic using tavily search

    Args : 
        topic (str) : the topic to research

    Returns : 
        str : A summary of the research results.
    """
    _logger.info(f"Researching topic: {topic}")
    search_results_raw = _tavily.invoke({"query": topic})

    results_text = "\n\n".join(result["content"] for result in search_results_raw.get("results", []))
    _logger.info(f"raw search results: {results_text}")

    PROMPT = f"""
        You are a research assistant. You will be provided with topic of interests and search results from TavilySearch.
        Your task is to summarize the information in a concise manner, highlighting the most important points.
        Topic : {topic}
        Search Results:
        {results_text}
        Please provide a summary of the above search results.
        """

    try:
        summary = _llm.invoke(PROMPT)

    except Exception as e:
        error_message = str(e).upper()

        retryable_errors = [
            "503",
            "UNAVAILABLE",
            "429",
            "RESOURCE_EXHAUSTED",
            "500",
            "502",
            "504"
        ]

        if not any(error in error_message for error in retryable_errors):
            raise

        _logger.warning(
            "Primary model unavailable. Using fallback model."
        )

        summary = _fallback_llm.invoke(PROMPT)

    return summary.content

if __name__=="__main__":
    print(research.invoke({"topic":"small pox"}))
