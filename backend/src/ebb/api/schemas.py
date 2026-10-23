"""Request and response bodies. Each route adds its own here."""

from pydantic import BaseModel


class Health(BaseModel):
    database: str
    tutor: str
