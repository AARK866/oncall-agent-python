# Alert Deduplication

This step adds alert grouping so repeated notifications for the same incident do not
create unlimited diagnosis tasks and incidents.

## Why

Alertmanager may send the same firing alert repeatedly while an incident is still active.
Without deduplication, each webhook would create a new task and then a new incident record.

The project now groups repeated alerts by a stable `dedupe_key`.

## Dedupe Rules

For normalized alerts:

```text
api_alert:alert_id:{alert_id}
```

For Alertmanager:

```text
alertmanager:fingerprint:{fingerprint}
```

If `fingerprint` is missing, the fallback uses `groupKey` and alert index. If both are
missing, it uses sorted labels.

## Runtime Behavior

First firing alert:

```text
scheduled=1
deduplicated=0
```

Repeated firing alert with the same dedupe key:

```text
scheduled=0
deduplicated=1
```

The repeated alert returns the existing latest task in the same alert group. This keeps
the diagnosis history linked to one active alert group instead of creating a new incident
for every repeated webhook.

Resolved alert:

```text
processed=0
resolved=1
```

The alert group status changes from `active` to `resolved`.

## APIs

List alert groups:

```http
GET /api/alerts/groups?limit=20
```

Get one alert group:

```http
GET /api/alerts/groups/{group_id}
```

Each diagnosis task also includes:

```json
{
  "alert_group_id": "ag_..."
}
```

## Local Check

Run one unique alert:

```powershell
.\.venv\Scripts\python.exe scripts\check_alert_webhook.py --in-process --mock-llm
```

Run the same fingerprint twice to observe deduplication:

```powershell
.\.venv\Scripts\python.exe scripts\check_alert_webhook.py --in-process --mock-llm --fingerprint demo-repeat
.\.venv\Scripts\python.exe scripts\check_alert_webhook.py --in-process --mock-llm --fingerprint demo-repeat
```

On the second run, the script should print `scheduled: 0` and `deduplicated: 1`.
