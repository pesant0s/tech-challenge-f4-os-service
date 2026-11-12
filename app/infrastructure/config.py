from datetime import timedelta

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.saga import PrazosSaga


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin123"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]
    LOG_FORMAT: str = "console"
    LOG_LEVEL: str = "INFO"
    # Sem RABBITMQ_URL a mensageria não sobe: a API atende e a Saga fica parada no outbox.
    RABBITMQ_URL: str | None = None
    SAGA_PRAZO_RESPOSTA_SEGUNDOS: int = 300
    SAGA_PRAZO_APROVACAO_HORAS: int = 24
    SAGA_VARREDURA_SEGUNDOS: int = 30
    TOKEN_INTERNO: str | None = None


settings = Settings()


def prazos_da_saga() -> PrazosSaga:
    return PrazosSaga(
        resposta=timedelta(seconds=settings.SAGA_PRAZO_RESPOSTA_SEGUNDOS),
        aprovacao=timedelta(hours=settings.SAGA_PRAZO_APROVACAO_HORAS),
    )
