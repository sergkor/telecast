from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from telecast.web import auth

router = APIRouter(dependencies=[Depends(auth.require_login)])


@router.get("/", response_class=HTMLResponse)
def queue(request: Request):
    return HTMLResponse("ok")
