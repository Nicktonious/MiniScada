"""
Адаптер контекста данных pymodbus (ManagerServerContext).

Маршрутизирует все входящие Modbus PDU запросы чтения и записи (FC 01, 02, 03, 04, 05, 06, 15, 16)
напрямую в методы ModbusSlaveManager (get_values / set_values).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Sequence, Union

if TYPE_CHECKING:
    from ModbusSlaveEmu.manager import ModbusSlaveManager

logger = logging.getLogger("ModbusSlaveEmu.Bridge.Context")

try:
    from pymodbus.datastore import ModbusServerContext
    try:
        from pymodbus.datastore.simulator import ExcCodes
    except ImportError:
        try:
            from pymodbus.pdu import ExcCodes
        except ImportError:
            class ExcCodes:
                ILLEGAL_FUNCTION = 0x01
                ILLEGAL_DATA_ADDRESS = 0x02
                ILLEGAL_DATA_VALUE = 0x03
                DEVICE_BUSY = 0x06
    PYMODBUS_AVAILABLE = True
except ImportError:
    PYMODBUS_AVAILABLE = False

    class ModbusServerContext:
        def __init__(self, *args, **kwargs):
            self.old_simulator = True
            self.simdevices = []

    class ExcCodes:
        ILLEGAL_FUNCTION = 0x01
        ILLEGAL_DATA_ADDRESS = 0x02
        ILLEGAL_DATA_VALUE = 0x03
        DEVICE_BUSY = 0x06


class ManagerServerContext(ModbusServerContext):
    """
    Кастомный контекст сервера pymodbus, связывающий обработчик Modbus PDU с ModbusSlaveManager.

    Принимает запросы от pymodbus сервера и делегирует чтение/запись в соответствующее
    slave-устройство по slave_id.
    """

    def __init__(
        self,
        manager: "ModbusSlaveManager",
        allowed_slave_ids: Optional[List[int]] = None,
        on_request: Optional[Callable[[str, int, int, int, Any], None]] = None,
    ) -> None:
        """
        :param manager: Экземпляр ModbusSlaveManager.
        :param allowed_slave_ids: Ограничение списка Slave ID, доступных на данной конкретной шине.
                                  Если None, разрешены все устройства менеджера.
        :param on_request: Опциональный callback аудита: cb(action, slave_id, fc, address, data).
        """
        if PYMODBUS_AVAILABLE:
            # Инициализация ModbusServerContext с флагом old_simulator=True
            # Это указывает pymodbus вызывать напрямую async_getValues/async_setValues
            self.old_simulator = True
            self.simdevices = []
            self._devices = {}
        else:
            super().__init__()

        self.manager = manager
        self.allowed_slave_ids = set(allowed_slave_ids) if allowed_slave_ids else None
        self.on_request = on_request

    @property
    def device_ids(self) -> List[int]:
        """Возвращает список поддерживаемых Modbus Slave ID."""
        all_ids = self.manager.list_slave_ids()
        if self.allowed_slave_ids is not None:
            return [sid for sid in all_ids if sid in self.allowed_slave_ids]
        return all_ids

    def _is_device_allowed(self, device_id: int) -> bool:
        if self.allowed_slave_ids is not None:
            return device_id in self.allowed_slave_ids
        return device_id in self.manager.list_slave_ids()

    async def async_getValues(
        self,
        device_id: int,
        func_code: int,
        address: int,
        count: int = 1,
    ) -> Union[List[int], List[bool], Any]:
        """
        Вызывается pymodbus при поступлении запроса на чтение данных:
        FC 01 (Read Coils), FC 02 (Read Discrete Inputs),
        FC 03 (Read Holding Registers), FC 04 (Read Input Registers).
        """
        if not self._is_device_allowed(device_id):
            logger.debug("Запрос чтения к неизвестному или запрещенному Slave ID: %d", device_id)
            return getattr(ExcCodes, "ILLEGAL_ADDRESS", 0x02)

        try:
            values = self.manager.get_values(
                slave_id=device_id,
                fc=func_code,
                address=address,
                count=count,
            )

            if self.on_request:
                self.on_request("READ", device_id, func_code, address, values)

            return values

        except KeyError:
            logger.warning("Устройство Slave %d не найдено в менеджере", device_id)
            return getattr(ExcCodes, "ILLEGAL_ADDRESS", 0x02)
        except Exception as e:
            logger.error("Ошибка при чтении Modbus PDU (slave=%d, fc=%d, addr=%d): %s", device_id, func_code, address, e)
            return ExcCodes.DEVICE_BUSY

    async def async_setValues(
        self,
        device_id: int,
        func_code: int,
        address: int,
        values: Sequence[Union[int, bool]],
    ) -> Optional[Any]:
        """
        Вызывается pymodbus при поступлении команды на запись данных:
        FC 05 (Write Single Coil), FC 06 (Write Single Register),
        FC 15 (Write Multiple Coils), FC 16 (Write Multiple Registers).
        """
        if not self._is_device_allowed(device_id):
            logger.debug("Запрос записи к неизвестному или запрещенному Slave ID: %d", device_id)
            return getattr(ExcCodes, "ILLEGAL_ADDRESS", 0x02)

        try:
            val_list = list(values)
            self.manager.set_values(
                slave_id=device_id,
                fc=func_code,
                address=address,
                values=val_list,
            )

            if self.on_request:
                self.on_request("WRITE", device_id, func_code, address, val_list)

            return None  # None сигнализирует pymodbus об успешном выполнении

        except KeyError:
            logger.warning("Устройство Slave %d не найдено в менеджере", device_id)
            return getattr(ExcCodes, "ILLEGAL_ADDRESS", 0x02)
        except Exception as e:
            logger.error("Ошибка при записи Modbus PDU (slave=%d, fc=%d, addr=%d): %s", device_id, func_code, address, e)
            return ExcCodes.DEVICE_BUSY
