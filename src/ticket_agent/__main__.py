"""python -m ticket_agent starts the web UI."""

from ticket_agent.config import get_settings


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run("ticket_agent.app:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
