from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_core.tools import tool
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import logging
from tools.researcher_tool import research,_llm
import config

_logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are orchestrator agent that can perform various tasks using tools.

You have access to following tools :

1. Researcher Tool : This tool allows you to research a topic using TavilySearch and summarize the results

For tasks that require specific tools, you will use the provided tools to accomplish the task.
For all generic tasks, you will use your own capabilities to accomplish the task."""



def build_agent() :
    """
    Build an agent with the provided tools.
    
    Args : 
        tools (list) : A list of tools to be used by the agent.

    Returns : 
        create_agent : An instance of the created agent
    """
    tools = [research]

    agent = create_agent(
        system_prompt = SYSTEM_PROMPT,
        tools = tools,
        model = _llm
    )

    return agent

if __name__ == "__main__":

    print("Testing orchestrator LLM...", flush=True)

    response = _llm.invoke(
        "Reply with exactly: Hello"
    )

    print("LLM response:", response.content, flush=True)

    print("Building agent...", flush=True)

    agent = build_agent()

    print("Invoking agent...", flush=True)

    response = agent.invoke({
        "messages": [
            HumanMessage(
                content="Research the latest advancements in AI."
            )
        ]
    })

    print(response["messages"][-1].content)