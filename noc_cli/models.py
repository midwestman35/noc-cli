from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    file_name: str
    content_url: str
    size: int = 0


class Comment(BaseModel):
    id: int
    author_id: int | None = None
    public: bool = True
    body: str = ""
    created_at: datetime | None = None
    attachments: list[Attachment] = Field(default_factory=list)


class Ticket(BaseModel):
    id: int
    subject: str = ""
    description: str = ""
    requester_org: str | None = None
    requester_email: str | None = None
    status: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    comments: list[Comment] = Field(default_factory=list)
