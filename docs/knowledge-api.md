# Knowledge API

The knowledge API lets you inspect and test the local runbook knowledge base.

Start the API:

```powershell
python -m uvicorn app.main:app --reload
```

## Stats

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/stats
```

Returns document count, chunk count, retriever mode, services, and incident types.

## Documents

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/documents
```

Get one document:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/knowledge/documents/payment_5xx.md
```

## Search

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/api/knowledge/search `
  -ContentType "application/json" `
  -Body '{"query":"payment service 5xx error rate","top_k":2,"service":"payment-api","incident_type":"5xx"}'
```

The response includes:

- `results`: matched source documents
- `metadata.retrieved_count`: number of returned chunks
- `metadata.knowledge_base`: current knowledge base status
