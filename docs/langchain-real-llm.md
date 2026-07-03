# LangChain Real LLM Integration

The project now uses LangChain for production LLM calls.

Official LangChain docs used for this integration:

- ChatOpenAI integration: https://docs.langchain.com/oss/python/integrations/chat/openai
- LangChain model interface: https://docs.langchain.com/oss/python/langchain/models
- Structured output: https://docs.langchain.com/oss/python/langchain/structured-output

## Install

```powershell
python -m pip install -r requirements.txt
```

This installs:

- `langchain`
- `langchain-openai`
- `langgraph`

## Configure `.env`

```env
LLM_PROVIDER=langchain-openai
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=replace-with-your-real-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_TIMEOUT_SECONDS=30
LLM_MAX_RETRIES=6
```

For OpenAI-compatible providers such as DeepSeek or Qwen-compatible endpoints, keep:

```env
LLM_PROVIDER=langchain-openai
```

and change:

```env
LLM_MODEL=provider-model-name
LLM_BASE_URL=provider-compatible-base-url
LLM_API_KEY=provider-api-key
```

## Verify

```powershell
python scripts/check_llm_client.py
```

Then run an incident:

```powershell
python -m uvicorn app.main:app --reload
```

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/incidents/analyze `
  -ContentType "application/json" `
  -Body '{"message":"payment service 5xx error rate is high","session_id":"langchain-real-test","mode":"ops"}'
```

Check:

```text
metadata.llm_tool_selection.source
metadata.llm_summary.source
```

When both are `llm`, LangChain successfully drove tool selection and summary generation.
