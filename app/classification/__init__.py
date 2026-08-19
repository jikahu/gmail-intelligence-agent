"""Deterministic classification (CLAUDE.md §6, §7, §8, §9, §11).

Public surface:

```python
from app.classification import classify, ClassificationContext, from_gmail

message = from_gmail(gmail_api_message, user_email="you@example.com")
decision = classify(message, context)
```

The engine decides; it never touches Gmail. Applying a decision is Phase 11.
"""

from app.classification.context import ClassificationContext, Rule, build_rule
from app.classification.engine import Classification, classify, classify_all
from app.classification.labels import Label, Priority
from app.classification.message import EmailMessage, from_gmail, from_gmail_thread
from app.classification.protection import Protection
from app.classification.signals import Signals, detect

__all__ = (
    "Classification",
    "ClassificationContext",
    "EmailMessage",
    "Label",
    "Priority",
    "Protection",
    "Rule",
    "Signals",
    "build_rule",
    "classify",
    "classify_all",
    "detect",
    "from_gmail",
    "from_gmail_thread",
)
