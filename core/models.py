# STL
from typing import Any, Optional, Union, List

# PDM
import pydantic
from pydantic import Field


class ScrapeSelector(pydantic.BaseModel):
    id: str
    parentSelectors: List[str]
    type: str
    selector: str
    extractAttribute: Optional[str] = None
    multiple: Optional[bool] = False
    regex: Optional[str] = None
    paginationType: Optional[str] = None
    extraReplace:  Optional[str] = None
    # clickAction
    clickActionType: Optional[str] = None
    clickElementSelector: Optional[str] = None
    clickElementUniquenessType:  Optional[str] = None
    clickType: Optional[str] = None
    delay: Optional[int] = None
    discardInitialElements: Optional[str] = None
    scroll:  Optional[bool] = None
    elementLimit:  Optional[int] = None


class ScrapeJob(pydantic.BaseModel):
    id: str = Field(alias="_id")
    startUrl: List[str]
    selectors: List[ScrapeSelector]


class Element(pydantic.BaseModel):
    name: str
    selector: str
    url: Optional[str] = None


class CapturedElement(pydantic.BaseModel):
    selector: str
    text: str
    name: str
