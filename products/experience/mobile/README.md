# Mobile Experience API

One screen, one round trip ([ADR-0030](../../../docs/adr/0030-api-led-layering.md)).

```bash
PORT=8091 PROCESS_API_URL=http://localhost:8090 python main.py
curl 'http://localhost:8091/v1/home?account_id=acct-001'
```

The web SPA makes three system calls for the equivalent view (`balance`, `summary`,
`transactions`) plus one per loan. This makes one.

| Variable | Default | Effect |
|---|---|---|
| `PROCESS_API_URL` | unset | **required** — the only backend this service knows |
| `MOBILE_ACTIVITY_ROWS` | `3` | activity rows the home screen draws |

## The rule that keeps this honest

It may aggregate, reshape, format and paginate. **It may not decide.** Every business
judgement — including which action comes next — belongs to the process API. If the mobile
and web channels can disagree about whether a customer is in trouble, the logic is in the
wrong layer.

`test_home.py` asserts structurally that no business vocabulary appears here, and
`scripts/test_api_layering.py` (LAYER-1) fails the build if this service names a system
API. There is deliberately no fallback to one: the absence is the layering.
