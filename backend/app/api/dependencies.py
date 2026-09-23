from typing import Annotated
from fastapi import Depends, Query
from sqlalchemy.orm import Session
from app.db.session import get_db

Database = Annotated[Session, Depends(get_db)]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
