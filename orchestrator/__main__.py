"""Voice Hub orchestrator entry point."""

import asyncio
import logging
import sys

from .config import Settings, SatelliteConfig
from .satellite import SatelliteConnection
from .esphome_satellite import ESPHomeSatellite
from .session import SessionManager


def main() -> None:
    settings = Settings.from_env()

    # Default satellite if none configured
    if not settings.satellites and not settings.esphome_satellites:
        settings.satellites = [
            SatelliteConfig(name="living_room", host="192.168.1.10", port=10700),
        ]

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stderr,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aioesphomeapi").setLevel(logging.INFO)
    logging.getLogger("zeroconf").setLevel(logging.WARNING)

    system_prompt = settings.load_system_prompt()
    session_manager = SessionManager(
        system_prompt=system_prompt,
        timeout_s=settings.session_timeout_s,
    )

    all_sats = (
        [f"{s.name}@{s.host}:{s.port}(wyoming)" for s in settings.satellites]
        + [f"{s.name}@{s.host}:{s.port}(esphome)" for s in settings.esphome_satellites]
    )
    logging.getLogger(__name__).info(
        "Starting orchestrator with %d satellite(s): %s",
        len(all_sats),
        ", ".join(all_sats),
    )

    asyncio.run(_run(settings, session_manager))


async def _run(settings: Settings, session_manager: SessionManager) -> None:
    tasks: list[asyncio.Task] = []

    for sat_config in settings.satellites:
        conn = SatelliteConnection(sat_config, settings, session_manager)
        tasks.append(
            asyncio.create_task(
                conn.run_forever(), name=f"satellite-{sat_config.name}"
            )
        )

    for esph_config in settings.esphome_satellites:
        conn = ESPHomeSatellite(esph_config, settings, session_manager)
        tasks.append(
            asyncio.create_task(
                conn.run_forever(), name=f"esphome-{esph_config.name}"
            )
        )

    tasks.append(
        asyncio.create_task(
            session_manager.cleanup_loop(), name="session-cleanup"
        )
    )

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    main()
