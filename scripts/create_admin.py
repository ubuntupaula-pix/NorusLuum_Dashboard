"""Create the first administrator interactively; never seeds a known password."""
from getpass import getpass
import re

from app.auth import hash_password, validate_password_strength
from app.database import SessionLocal
from app.models import Usuario


def main() -> None:
    username = input("Usuario administrador nuevo: ").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,100}", username):
        raise SystemExit("Usa 3–100 caracteres: letras, números, punto, guion, _ o @.")
    password = getpass("Contraseña (mínimo 12 caracteres, 3 tipos): ")
    repeat = getpass("Repite la contraseña: ")
    if password != repeat:
        raise SystemExit("Las contraseñas no coinciden.")
    try:
        validate_password_strength(password)
    except Exception as exc:
        raise SystemExit(getattr(exc, "detail", str(exc))) from exc

    db = SessionLocal()
    try:
        if db.query(Usuario).filter_by(username=username).first():
            raise SystemExit("Ese usuario ya existe.")
        db.add(Usuario(
            username=username,
            password_hash=hash_password(password),
            rol="admin",
            activo=1,
            acceso_todas_propiedades=1,
        ))
        db.commit()
        print(f"Administrador {username} creado. Guarda la contraseña en un gestor seguro.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
