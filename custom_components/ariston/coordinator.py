"""Coordinator class for Ariston module."""

from __future__ import annotations

import asyncio

from collections.abc import Callable
from datetime import timedelta
import logging

from ariston.base_device import AristonBaseDevice
from ariston.ariston_api import ConnectionException
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryAuthFailed

from homeassistant.util import dt as dt_util

from .cloud_governor import CloudPaused, is_block
from .cloud_pacing import backoff_interval, is_overload

from .const import DOMAIN

# Longest time entities keep their last values while the account is paused.
STALE_LIMIT = timedelta(hours=2)

_LOGGER = logging.getLogger(__name__)


class DeviceDataUpdateCoordinator(DataUpdateCoordinator):
    """Manages polling for state changes from the device.

    Polls at the configured interval (or a shorter ``preferred_interval``
    requested by the update method) while the cloud answers. Overload errors
    double the interval up to 30 minutes; the next success restores it.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        device: AristonBaseDevice,
        scan_interval_seconds: int,
        coordinator_name: str,
        async_update_state: Callable,
    ) -> None:
        """Initialize the data update coordinator."""
        self.base_interval = timedelta(seconds=scan_interval_seconds)
        self.preferred_interval: timedelta | None = None
        self.overload_failures = 0

        self.governor = None  # set by the integration (account-wide limiter)
        self.last_fresh = None

        async def async_update_with_errors():
            try:
                async with asyncio.timeout(90):
                    result = await async_update_state()
            except CloudPaused as error:
                return self._keep_last(error)
            except ConnectionException as error:
                status = error.args[0] if error.args else None
                if status in (401, 403):
                    raise ConfigEntryAuthFailed("Ariston authorization failed") from error
                if is_block(error):
                    # The governor has paused the whole account; no own backoff.
                    return self._keep_last(error)
                self._record_failure(error)
                raise UpdateFailed(f"Ariston request failed: {error}") from error
            except Exception as error:
                self._record_failure(error)
                raise
            self.overload_failures = 0
            self.last_fresh = dt_util.utcnow()
            self.update_interval = self.preferred_interval or self.base_interval
            return result

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}-{device.name}-{coordinator_name}",
            update_interval=self.base_interval,
            update_method=async_update_with_errors,
        )

        self.device = device

    def _keep_last(self, error: BaseException):
        """While Ariston blocks the account, keep showing the last data.

        Entities stay available with their last values for up to
        ``STALE_LIMIT``; only a longer outage marks them unavailable.
        """
        self.update_interval = self.preferred_interval or self.base_interval
        if self.last_fresh is not None and dt_util.utcnow() - self.last_fresh <= STALE_LIMIT:
            _LOGGER.debug("%s: cloud paused, keeping last data (%r)", self.name, error)
            return self.data
        raise UpdateFailed(f"Ariston cloud paused: {error}") from error

    def _record_failure(self, error: BaseException) -> None:
        if is_overload(error):
            self.overload_failures += 1
            self.update_interval = backoff_interval(self.base_interval, self.overload_failures)
            _LOGGER.info(
                "%s: Ariston cloud overloaded or unreachable (%r); next poll in %s",
                self.name, error, self.update_interval,
            )
