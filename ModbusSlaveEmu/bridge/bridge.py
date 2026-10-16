"""
Модуль ModbusBridge: высокоуровневый шлюз-мост между ModbusSlaveManager и pymodbus.

Оркестрирует:
1. Автоматический сбор всех шин (TCP, RTU) из конфигураций устройств через BusCollector.
2. Подключение кастомного контекста ManagerServerContext к pymodbus.
3. Запуск сетевых и последовательных слушателей серверов pymodbus.
4. Прозрачную обработку запросов от сторонних процессов и SCADA-систем.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ModbusSlaveEmu.manager import ModbusSlaveManager

from ModbusSlaveEmu.bridge.bus_collector import BusCollector, BusInfo
from ModbusSlaveEmu.bridge.context import ManagerServerContext
from ModbusSlaveEmu.bridge.listener import BaseBusListener, create_bus_listener

logger = logging.getLogger("ModbusSlaveEmu.Bridge")


class ModbusBridge:
    """
    Высокоуровневый шлюз, слушающий шины Modbus через pymodbus и передающий
    данные в/из ModbusSlaveManager.
    """

    def __init__(
        self,
        manager: "ModbusSlaveManager",
        auto_start: bool = False,
        on_request: Optional[Callable[[str, int, int, int, Any], None]] = None,
    ) -> None:
        """
        :param manager: Экземпляр ModbusSlaveManager с загруженными устройствами.
        :param auto_start: Если True, сразу запускает слушатели шин.
        :param on_request: Опциональный callback для трассировки запросов/ответов.
        """
        self.manager = manager
        self.on_request = on_request
        self._lock = threading.RLock()
        self._is_running = False

        # 1. Извлекаем все уникальные шины через BusCollector
        self.buses: List[BusInfo] = BusCollector.collect_buses(self.manager)

        # 2. Создаем слушатели для каждой шины
        self.listeners: Dict[str, BaseBusListener] = {}
        self._init_listeners()

        if auto_start:
            self.start()

    def _init_listeners(self) -> None:
        """Инициализирует слушатели шин и связывает их с ManagerServerContext."""
        for bus in self.buses:
            context = ManagerServerContext(
                manager=self.manager,
                allowed_slave_ids=bus.slave_ids,
                on_request=self.on_request,
            )
            listener = create_bus_listener(bus_info=bus, context=context)
            self.listeners[bus.bus_id] = listener

    def start(self) -> None:
        """
        Запускает слушатели всех шин Modbus и симуляцию устройств в менеджере.
        """
        with self._lock:
            if self._is_running:
                logger.warning("ModbusBridge уже запущен")
                return

            # Запускаем генерацию данных в менеджере (если еще не запущена)
            if not self.manager.is_running:
                self.manager.start()

            # Запускаем слушатели всех обнаруженных шин
            for bus_id, listener in self.listeners.items():
                try:
                    listener.start()
                    logger.info("Шина '%s' успешно запущена", bus_id)
                except Exception as e:
                    logger.error("Ошибка при запуске слушателя шины '%s': %s", bus_id, e)

            self._is_running = True
            logger.info("ModbusBridge успешно запущен. Активных шин: %d", len(self.listeners))

    def Run(self, blocking: bool = False) -> None:
        """Алиас для запуска (с поддержкой блокирующего режима)."""
        self.start()
        if blocking:
            stop_event = threading.Event()
            try:
                stop_event.wait()
            except KeyboardInterrupt:
                self.stop()

    def stop(self, timeout: float = 2.0) -> None:
        """Останавливает все слушатели шин и освобождает порты."""
        with self._lock:
            if not self._is_running:
                return

            for bus_id, listener in self.listeners.items():
                try:
                    listener.stop(timeout=timeout)
                except Exception as e:
                    logger.debug("Ошибка при остановке слушателя шины '%s': %s", bus_id, e)

            self._is_running = False
            logger.info("ModbusBridge остановлен")

    def Stop(self, timeout: float = 2.0) -> None:
        """Алиас для stop()."""
        self.stop(timeout=timeout)

    @property
    def is_running(self) -> bool:
        return self._is_running

    def list_endpoints(self) -> List[str]:
        """Возвращает список описаний всех обслуживаемых шин и портов."""
        return [bus.endpoint_description for bus in self.buses]

    def __enter__(self) -> ModbusBridge:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
