"""Reuse the project's LangChain Groq client for the future MCP editor."""


def build_editor_llm(model_name=None, api_key=None):
    from common.llm_client import get_validated_chat_groq

    return get_validated_chat_groq(
        model_name=model_name, api_key=api_key, temperature=0.0
    )
