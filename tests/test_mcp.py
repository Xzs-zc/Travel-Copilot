import pytest

from backend.app.mcp.client import McpToolClient, McpToolRegistry


@pytest.mark.asyncio
async def test_mcp_server_exposes_and_calls_tools_over_stdio():
    client = McpToolClient()
    try:
        await client.start()
        tools = await client.list_tools()
        names = {tool.name for tool in tools}
        assert {"search_amap", "route_estimate", "resolve_transport_hubs", "get_weather", "query_train", "verify_official_page", "get_attraction_detail", "search_web"}.issubset(names)

        health = await client.call_tool("health_check", {})
        assert health["ok"] is True
        assert health["server"] == "travel-tools"
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_mcp_tool_registry_converts_discovered_schema():
    client = McpToolClient()
    registry = McpToolRegistry(client, ["search_amap", "get_weather"])
    try:
        await registry.initialize()
        schemas = registry.schemas(["search_amap", "get_weather"])
        assert [item["function"]["name"] for item in schemas] == ["search_amap", "get_weather"]
        assert schemas[0]["function"]["parameters"]["properties"]["keywords"]["type"] == "string"
    finally:
        await client.stop()
