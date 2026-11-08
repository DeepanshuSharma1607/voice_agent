from langchain_mcp_adapters.client import MultiServerMCPClient

mcp_client = MultiServerMCPClient({
    "gmail":{
        "transport":"stdio",
        "command":"npx",
        "args":["-y","@gongrzhe/server-gmail-autoauth-mcp"]
    },
    "notion":{
        "transport":"stdio",
        "command":"npx",
        "args":["-y","mcp-rempte","https://mcp.notion.com/mcp"]
    }
})