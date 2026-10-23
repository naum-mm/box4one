from fastapi import APIRouter, HTTPException, Request, Response
from app.schemas import AccessRequest, AccessResponse
from app.security import (DOWNLOAD_ACCESS_WORD, QUEUE_ACCESS_WORD, COOKIE_NAME, ACCESS_SECONDS,
                          word_matches, make_access_token, webapp_mode)

router = APIRouter(prefix="/api/access", tags=["access"])

if not DOWNLOAD_ACCESS_WORD or not QUEUE_ACCESS_WORD or word_matches(DOWNLOAD_ACCESS_WORD, QUEUE_ACCESS_WORD):
    raise RuntimeError("Access words must be configured and different")


@router.post("", response_model=AccessResponse)
def check_access(payload: AccessRequest, request: Request, response: Response):
    word = payload.word.strip()
    if word_matches(word, DOWNLOAD_ACCESS_WORD):
        mode = "download"
    elif word_matches(word, QUEUE_ACCESS_WORD):
        mode = "queue"
    else:
        raise HTTPException(status_code=403, detail="Access denied")
    if request.headers.get("X-Web-App") == "1":
        response.set_cookie(COOKIE_NAME, make_access_token(mode), max_age=ACCESS_SECONDS,
                            secure=True, httponly=True, samesite="strict", path="/")
    response.headers["Cache-Control"] = "no-store"
    return AccessResponse(mode=mode)


@router.get("", response_model=AccessResponse)
def restore_access(request: Request, response: Response):
    mode = webapp_mode(request)
    if mode is None:
        raise HTTPException(status_code=403, detail="Access denied")
    response.headers["Cache-Control"] = "no-store"
    return AccessResponse(mode=mode)


@router.delete("")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
    response.headers["Cache-Control"] = "no-store"
    return {"status": "ok"}
