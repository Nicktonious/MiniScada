"""
Модуль сетевых и последовательных слушателей шин Modbus (BusListener).
Запускает серверы pymodbus в фоновых asyncio-циклах с поддержкой неблокирующей работы и корректного останова.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import threading
import time
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ModbusSlaveEmu.bridge.bus_collector import BusInfo
    from ModbusSlaveEmu.bridge.context import ManagerServerContext

logger = logging.getLogger("ModbusSlaveEmu.Bridge.Listener")

try:
    from pymodbus.server import ModbusSerialServer, ModbusTcpServer
    PYMODBUS_AVAILABLE = True
except ImportError:
    PYMODBUS_AVAILABLE = False
    ModbusTcpServer = None
    ModbusSerialServer = None


class BaseBusListener(abc.ABC):
    """
    Базовый абстрактный класс слушателя шины Modbus.
    """

    def __init__(self, bus_info: "BusInfo", context: "ManagerServerContext") -> None:
        self.bus_info = bus_info
        self.context = context
        self._is_running = False
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None
        self._ready_event = threading.Event()

    @property
    def is_running(self) -> bool:
        return self._is_running

    @abc.abstractmethod
    def start(self) -> None:
        """Запуск слушателя шины."""
        pass

    @abc.abstractmethod
    def stop(self, timeout: float = 2.0) -> None:
        """Остановка слушателя шины."""
        pass


class TcpBusListener(BaseBusListener):
    """
    Слушатель шины Modbus TCP на базе ModbusTcpServer из pymodbus.
    """

    def _run_server_loop(self) -> None:
        """Поток выполнения asyncio event loop для Modbus TCP сервера."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        host = self.bus_info.host or "0.0.0.0"
        port = self.bus_info.port or 502

        async def _serve():
            try:
                self._server = ModbusTcpServer(
                    context=self.context,
                    address=(host, port),
                )
                self._is_running = True
                self._ready_event.set()
                logger.info("Modbus TCP сервер слушает на %s:%d (Slaves: %s)", host, port, self.bus_info.slave_ids)
                await self._server.serve_forever()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error("Ошибка в работе Modbus TCP сервера (%s:%d): %s", host, port, e)
                self._ready_event.set()
            finally:
                self._is_running = False

        try:
            self._loop.run_until_complete(_serve())
        finally:
            self._loop.close()
            self._loop = None

    def start(self) -> None:
        """Запускает Modbus TCP сервер в фоновом потоке."""
        if not PYMODBUS_AVAILABLE:
            raise RuntimeError("Библиотека pymodbus не установлена. Запустите: pip install pymodbus")

        if self._is_running:
            return

        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_server_loop,
            name=f"ModbusTcpListener-{self.bus_info.port}",
            daemon=True,
        )
        self._thread.start()
        # Ждем запуска сокета
        self._ready_event.wait(timeout=3.0)

    def stop(self, timeout: float = 2.0) -> None:
        """Корректно останавливает Modbus TCP сервер."""
        if not self._is_running or not self._loop:
            return

        async def _shutdown():
            if self._server:
                await self._server.shutdown()

        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            future.result(timeout=timeout)
        except Exception as e:
            logger.debug("Исключение при остановке Modbus TCP сервера: %s", e)

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None

        self._is_running = False
        logger.info("Modbus TCP сервер на порту %d остановлен", self.bus_info.port)


class RtuBusListener(BaseBusListener):
    """
    Слушатель последовательной шины Modbus RTU на базе ModbusSerialServer.
    """

    def _run_server_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _serve():
            try:
                self._server = ModbusSerialServer(
                    context=self.context,
                    port=self.bus_info.serial_port,
                    baudrate=self.bus_info.baudrate or 9600,
                    bytesize=self.bus_info.data_bits or 8,
                    parity=self.bus_info.parity or "N",
                    stopbits=self.bus_info.stop_bits or 1,
                )
                self._is_running = True
                self._ready_event.set()
                logger.info(
                    "Modbus RTU сервер слушает на %s @ %d бод (Slaves: %s)",
                    self.bus_info.serial_port,
                    self.bus_info.baudrate,
                    self.bus_info.slave_ids,
                )
                await self._server.serve_forever()
            except Exception as e:
                logger.warning(
                    "Не удалось открыть последовательный порт %s (возможно, отсутствует оборудование): %s",
                    self.bus_info.serial_port,
                    e,
                )
                self._ready_event.set()
            finally:
                self._is_running = False

        try:
            self._loop.run_until_complete(_serve())
        finally:
            self._loop.close()
            self._loop = None

    def start(self) -> None:
        if not PYMODBUS_AVAILABLE:
            raise RuntimeError("Библиотека pymodbus не установлена. Запустите: pip install pymodbus")

        if self._is_running:
            return

        self._ready_event.clear()
        self._thread = threading.Thread(
            target=self._run_server_loop,
            name=f"ModbusRtuListener-{self.bus_info.serial_port}",
            daemon=True,
        )
        self._thread.start()
        self._ready_event.wait(timeout=2.0)

    def stop(self, timeout: float = 2.0) -> None:
        if not self._is_running or not self._loop:
            return

        async def _shutdown():
            if self._server:
                await self._server.shutdown()

        try:
            future = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            future.result(timeout=timeout)
        except Exception:
            pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
            self._thread = None
        self._is_running = False


def create_bus_listener(bus_info: "BusInfo", context: "ManagerServerContext") -> BaseBusListener:
    """Фабрика для создания соответствующего слушателя шины."""
    if bus_info.bus_type == "tcp":
        return TcpBusListener(bus_info=bus_info, context=context)
    elif bus_info.bus_type == "rtu":
        return RtuBusListener(bus_info=bus_info, context=context)
    else:
        raise ValueError(f"Неизвестный тип шины: {bus_info.bus_type}")
