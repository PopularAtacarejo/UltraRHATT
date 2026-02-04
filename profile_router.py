import hashlib
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from profile_store import (
    find_user,
    remove_profile_photo,
    save_auth_users,
    save_profile_photo,
    strip_user_sensitive,
    update_user,
    update_photos_index,
    remove_from_photos_index,
)

router = APIRouter()

MAX_PHOTO_SIZE = 2 * 1024 * 1024


def hash_password(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@router.get("/api/profile")
async def get_profile(email: str):
    if not email:
        raise HTTPException(status_code=400, detail="Email obrigatorio.")
    user, _ = find_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")
    return {"ok": True, "user": strip_user_sensitive(user)}


@router.put("/api/profile")
async def update_profile(payload: dict):
    email = (payload.get("email") or "").strip().lower()
    nome = (payload.get("nome") or "").strip()
    novo_email = (payload.get("novo_email") or payload.get("email_novo") or "").strip().lower()
    senha_atual = payload.get("senha_atual") or ""
    nova_senha = payload.get("nova_senha") or ""

    if not email:
        raise HTTPException(status_code=400, detail="Email obrigatorio.")

    user, users = find_user(email)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    updates = {}
    if nome:
        updates["nome"] = nome

    if novo_email and novo_email != email:
        if any((u.get("email") or "").lower() == novo_email for u in users):
            raise HTTPException(status_code=409, detail="Email ja esta em uso.")
        updates["email"] = novo_email

    if senha_atual or nova_senha:
        if not senha_atual or not nova_senha:
            raise HTTPException(status_code=400, detail="Senha atual e nova senha sao obrigatorias.")
        if user.get("senha") != hash_password(senha_atual):
            raise HTTPException(status_code=401, detail="Senha atual invalida.")
        updates["senha"] = hash_password(nova_senha)

    updated, users = update_user(email, updates)
    if not updated:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    if not save_auth_users(users, f"Atualizar perfil {email}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar perfil.")

    photo_url = updated.get("foto") or updated.get("avatar") or updated.get("avatar_url")
    if photo_url:
        update_photos_index(updated.get("email") or email, updated.get("nome") or "", photo_url)

    return {"ok": True, "user": strip_user_sensitive(updated)}


@router.post("/api/profile/photo")
async def upload_profile_photo(email: str = Form(...), photo: UploadFile = File(...)):
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email obrigatorio.")
    if not photo:
        raise HTTPException(status_code=400, detail="Arquivo nao enviado.")

    if photo.content_type and not photo.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Arquivo invalido. Envie uma imagem.")

    content = await photo.read()
    if len(content) > MAX_PHOTO_SIZE:
        raise HTTPException(status_code=413, detail="Arquivo maior que 2MB.")

    user, users = find_user(email_norm)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    old_photo = user.get("foto") or user.get("avatar") or user.get("avatar_url")
    photo_url = save_profile_photo(email_norm, content, photo.filename)
    user["foto"] = photo_url
    if old_photo and old_photo != photo_url:
        remove_profile_photo(old_photo)

    if not save_auth_users(users, f"Atualizar foto do perfil {email_norm}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar foto.")

    update_photos_index(email_norm, user.get("nome") or "", photo_url)

    return {"ok": True, "user": strip_user_sensitive(user)}


@router.delete("/api/profile/photo")
async def delete_profile_photo(email: str):
    email_norm = (email or "").strip().lower()
    if not email_norm:
        raise HTTPException(status_code=400, detail="Email obrigatorio.")

    user, users = find_user(email_norm)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario nao encontrado.")

    photo = user.get("foto") or user.get("avatar") or user.get("avatar_url")
    if photo:
        remove_profile_photo(photo)
    user.pop("foto", None)
    user.pop("avatar", None)
    user.pop("avatar_url", None)

    if not save_auth_users(users, f"Remover foto do perfil {email_norm}"):
        raise HTTPException(status_code=500, detail="Erro ao salvar perfil.")

    remove_from_photos_index(email_norm)

    return {"ok": True, "user": strip_user_sensitive(user)}
