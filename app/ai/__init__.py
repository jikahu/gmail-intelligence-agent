"""AI provider layer (CLAUDE.md §3, §4, §11, §16).

**AI suggests. The rules engine decides.**

```python
from app.ai import assist, build_provider

provider = build_provider()          # from config or the workbook
outcome = assist(message, decision, provider)
outcome.classification               # the merged, validated decision
```

Nothing in this package can change Gmail, change a setting, approve a rule, or
mark a VIP. The AI's output schema contains no verb for any of those, and every
answer passes through :mod:`app.ai.validator` before it affects anything.
"""

from app.ai.assist import AssistOutcome, assist, should_consult
from app.ai.base import AIProvider, AIResult, ProviderConfig
from app.ai.costs import AIUsage, CostTracker, estimate_cost
from app.ai.factory import NullProvider, build_provider, describe_provider
from app.ai.prompts import PROMPT_VERSION
from app.ai.schemas import AISuggestion, parse_suggestion
from app.ai.validator import ValidationOutcome, validate

__all__ = (
    "AIProvider",
    "AIResult",
    "AISuggestion",
    "AIUsage",
    "AssistOutcome",
    "CostTracker",
    "NullProvider",
    "PROMPT_VERSION",
    "ProviderConfig",
    "ValidationOutcome",
    "assist",
    "build_provider",
    "describe_provider",
    "estimate_cost",
    "parse_suggestion",
    "should_consult",
    "validate",
)
