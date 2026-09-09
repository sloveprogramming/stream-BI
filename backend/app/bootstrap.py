import sys

from app.db import make_engine, make_session_factory
from app.models import ApiKey, User
from app.security.apikeys import generate_key, hash_secret
from app.security.passwords import hash_password
from app.settings import get_settings


def bootstrap_admin(username: str, password: str) -> tuple[User, str]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    sf = make_session_factory(engine)
    with sf() as session:
        key_id, secret = generate_key()
        user = User(
            username=username,
            password_hash=hash_password(password),
            display_name="管理员",
            is_admin=True,
        )
        session.add(user)
        session.flush()
        session.add(
            ApiKey(
                user_id=user.id,
                key_id=key_id,
                secret_hash=hash_secret(secret),
                name="bootstrap",
            )
        )
        session.commit()
        return user, f"{key_id}.{secret}"


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python -m app.bootstrap <用户名> <密码>")
        sys.exit(1)
    user, key = bootstrap_admin(sys.argv[1], sys.argv[2])
    print(f"管理员 {user.username} 已创建，API Key（仅显示一次）: {key}")
