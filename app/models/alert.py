from typing import Optional

from pydantic import BaseModel


class AlertAcknowledgeIn(BaseModel):
    # No auth system in scope (ARCHITECTURE.md §7, confirmed decision) — optional free-text only.
    acknowledged_by: Optional[str] = None
