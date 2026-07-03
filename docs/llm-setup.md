# LLM Setup Guide

This project supports two LLM modes:

- `mock`: local deterministic mode. No API key is required.
- `openai-compatible`: real remote model mode using a Chat Completions compatible API.

The code path is the same for both modes. Only `.env` changes.

## 1. Keep Local Mock Mode

Use this mode while learning, testing, and committing normal code changes.

```env
LLM_PROVIDER=mock
LLM_MODEL=mock-oncall-agent
LLM_API_KEY=
LLM_BASE_URL=https://api.openai.com/v1
LLM_TIMEOUT_SECONDS=30
```

Run the checker:

```powershell
python scripts/check_llm_client.py
```

Expected result:

- provider is `mock`
- `api_key_set` is `False`
- the script prints a mock response

## 2. Switch To A Real OpenAI-Compatible Provider

Create or edit `.env` in the project root. Do not commit `.env`.

Example:

```env
LLM_PROVIDER=openai-compatible
LLM_MODEL=gpt-4o-mini
LLM_API_KEY=replace-with-your-real-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_TIMEOUT_SECONDS=60
```

Then run:

```powershell
python scripts/check_llm_client.py
```

If it works, start the API:

```powershell
python -m uvicorn app.main:app --reload
```

Send an ops request:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/incidents/analyze `
  -ContentType "application/json" `
  -Body '{"message":"payment service 5xx error rate is high","session_id":"real-llm-test","mode":"ops"}'
```

In the response metadata, check:

```text
llm_tool_selection.source
llm_summary.source
```

If they are `llm`, the model generated that part. If they are `fallback`, the system safely used the deterministic local logic.

## 3. Common Provider Shapes

OpenAI-compatible:

```env
LLM_PROVIDER=openai-compatible
LLM_MODEL=gpt-4o-mini
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=your-key
```

DeepSeek-compatible:

```env
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=your-key
```

Qwen-compatible:

```env
LLM_PROVIDER=qwen
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=your-key
```

## 4. Troubleshooting

`LLM_API_KEY is required when LLM_PROVIDER is not mock.`

The provider is real, but `.env` has no key.

`Failed to call LLM provider.`

Usually means network, proxy, key, model name, or base URL is wrong.

`llm_tool_selection.source = fallback`

The model did not return valid structured JSON for tool selection, so the Agent used the default tool plan.

`llm_summary.source = fallback`

The model did not return a usable diagnosis summary, so the Agent used the deterministic report builder.
