# Phase 105: Explicit discipline-category matching

## Why this phase exists

The official CNOOC and GuoPin detail captures contain requirements such as
`地质类`, `地质学类`, and `地质资源与地质工程类`. These are Ministry-style
discipline categories, not the vague phrase `相关专业`. The previous gate only
accepted named majors and therefore left some genuinely eligible undergraduate
and graduate rows in `pending_evidence`.

This phase adds a separate `category_terms` field to the versioned major
taxonomy. A category can support publication only when all existing gates still
pass:

- the category appears in the same official job-level professional field;
- the official degree field covers the student's degree;
- the evidence is bound to the displayed job title and official detail/row;
- no student-blocking work-experience condition is present;
- location/deadline/freshness gates remain unchanged.

The rule does not map `地球物理学类`, `石油工程`, `资源与环境类`, or generic
`相关专业` to a CUPB profile. Those remain review-only unless the official
requirement names a supported major or an explicitly mapped category.

## Taxonomy changes

| Profile | Explicit category | Reason |
| --- | --- | --- |
| 本科 · 资源勘查工程 | 地质类 | The undergraduate program belongs to the Ministry's 地质类 category. |
| 硕士/博士 · 地质学 | 地质学类 | The graduate profile is within the 0709 first-level category. |
| 硕士/博士 · 地质资源与地质工程 | 地质资源与地质工程类 | The graduate profile is within the 0818 first-level category. |
| 硕士/博士 · 地质工程 | none | A broad category is not added without a defensible one-to-one mapping. |

Category terms are intentionally separate from `exact_terms`; this preserves
the distinction between an exact named major and a broader eligibility
category in the audit trail and UI explanation.

## Verification

```text
python -m pytest -q
455 passed, 1 skipped
python -m compileall -q job_hub tests
git diff --check
```

The GuoPin snapshot regression changed from 21 to 22 explicit student rows and
from 9 to 8 pending rows. This is a rule-level gain, not a fabricated source:
the underlying official row, detail URL, deadline and evidence remain the same.

## Production rollout

After deploying the commit, run one controlled worker cycle and inspect the
coverage report. Compare:

1. `student_eligible` rows for `cnooc-career-browser` and
   `cmgb-iguopin-browser`;
2. the exact `matched_profile_ids` and `专业范围` evidence for every newly
   published row;
3. withdrawn rows and source freshness, which must remain unchanged;
4. the public count and top-source concentration.

Only rows whose official capture is current and complete may reopen. A larger
match count without a current successful capture is not an expansion result.
