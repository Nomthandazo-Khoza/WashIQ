from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import auth_template_context
from app.database import get_db

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    context = {"request": request}
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse("index.html", context)


@router.get("/contact")
def contact(request: Request, db: Session = Depends(get_db)):
    context = {"request": request}
    context.update(auth_template_context(request, db))
    return templates.TemplateResponse("contact.html", context)
