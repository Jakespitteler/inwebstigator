from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.core import get_db_session

SessionDep = Annotated[Session, Depends(get_db_session)]
