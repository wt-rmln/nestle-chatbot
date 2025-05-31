from dataclasses import dataclass, field
from typing import Dict, Optional

@dataclass
class Page:
    page_num: int
    offset: int
    text: str
    meta: Dict = field(default_factory=dict)

@dataclass
class SplitPage:
    page_num: int
    text: str
    meta: Dict = field(default_factory=dict)
