# Semantic Portal State Graph

- **Module**: `app/runtime/portal_state.py`
- **Commit A**: Phase 4

## 1. What it is

A structured, *semantic* page state replaces raw screenshots/CSS selectors as
the unit of observation. Each browser observation is reduced to:

```python
SemanticPortalState(
    state_type: StateType,          # LOGIN / DASHBOARD / SEARCH / RESULTS /
                                    # QUOTE_DETAIL / SESSION_EXPIRED / ERROR / UNKNOWN
    canonical_url: str,
    page_template_id: str,
    portal_version: str,
    session_status: str,
    product_context: str,
    visible_controls: [...],
    available_actions: [...],       # from the Safe Action registry only
    required_fields: [...],
    extracted_quote_fields: {...},
    state_signature: str,           # stable semantic hash
    observation_confidence: float,
    observation_timestamp: datetime,
    source_snapshot_ref: str,
)
```

## 2. State recognition (`classify_state`)

Evidence-driven, priority-ordered:

1. Session-expired markers (title/body) → `SESSION_EXPIRED` (a session-expired
   page can otherwise masquerade as LOGIN).
2. Error markers (404, server error, 页面不存在) → `ERROR` (unless quote
   context).
3. Login form / login hints → `LOGIN`.
4. URL path with search/results/list + quote-like table headers → `RESULTS`.
5. Quote/product-detail hints + quote-like headers → `QUOTE_DETAIL`.
6. Search hints + product context → `SEARCH`.
7. Otherwise → `UNKNOWN` (safe stop, no blind replay).

## 3. State signature (`state_signature`)

A SHA-256 over **semantic components only**:

- **Included**: state_type, URL path (query params stripped), title,
  product_context, session_status, tenant_id, portal_version, quote fields,
  semantic control fingerprints (kind+placeholder+label+name+input_type),
  semantic table fingerprints (headers + row cells).
- **Excluded (deliberately)**: random css classes, generated element ids,
  timestamps, element ordinals, dynamic DOM hashes, query parameters.

Stability: two renders of the same semantic page with churned classes/ids/
order/timestamps → **same signature**. Sensitivity: different product id,
quote version, tenant, session status → **different signature** (these change
execution correctness and must not be folded away).

## 4. State transitions

Every executed action records a `SemanticTransition`:

```
before_state | selected_action | preconditions_ok | expected_post |
observed_after | result (SUCCESS/FAILED/SAFE_STOP) | recovery_reason |
actual_cost | trace_ref | at
```

The `StateTransitionLog` is append-only and JSON-persisted. The state graph
does NOT blindly replay historical actions on similar states — recovery
decisions are made by the State-Aware Recovery Controller (Commit B) using
current state + budget + fault type.

## 5. What it is NOT

- Not a full DOM hash (that would churn on every random class).
- Not a screenshot classifier (semantics come from accessibility roles,
  field labels, header semantics, session markers — not pixels).
- Not a replay oracle (similar past state ≠ same current action).

## 6. Verified

Unit tests (`tests/test_phase4_state_actions.py`):
- classification priority (SESSION_EXPIRED > LOGIN, ERROR, RESULTS headers,
  QUOTE_DETAIL, UNKNOWN);
- signature stable under random DOM churn (classes/ids/order/query params);
- signature sensitive to product sku, session status.
17 passed in Commit A.
