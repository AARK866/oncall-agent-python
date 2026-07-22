# Knowledge access control

Knowledge ACLs are enforced before retrieved content reaches LlamaIndex,
reranking, or the LLM prompt.

## Identity

The current API authentication model has one configured API token. Its trusted
identity and roles are configured server-side:

```env
API_TOKEN_SUBJECT=api-client
API_TOKEN_ROLES=oncall,sre
KNOWLEDGE_ACL_ENABLED=true
KNOWLEDGE_SYSTEM_SUBJECT=oncall-agent
KNOWLEDGE_SYSTEM_ROLES=oncall,sre
```

Clients cannot submit roles in the search or chat request body. In production,
the existing API token requirement applies to knowledge, chat, and incident
endpoints. Internal alert processing uses the configured system identity.

## Policy

- `public`: readable without a role match;
- `internal`: requires an authenticated identity and an allowed role when roles
  are configured;
- `restricted`: requires an authenticated identity and an allowed role;
- unknown scopes: denied.

Legacy vector records without ACL metadata use
`KNOWLEDGE_DEFAULT_ALLOWED_ROLES` as a conservative compatibility policy.

## Enforcement points

ACL checks run inside keyword, in-memory vector, and Milvus result selection,
before Top-K and reranking. Document list, detail, statistics, knowledge search,
Chat Agent, and Ops Graph all carry the same access context. LangGraph snapshots
persist the subject and roles so resume cannot silently elevate access.

Milvus currently over-fetches candidates and enforces ACL in the application
process because governance metadata is stored as JSON. A later schema migration
can promote ACL fields to indexed Milvus scalar fields for server-side filtering.
