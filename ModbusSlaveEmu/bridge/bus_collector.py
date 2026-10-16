"""
Модуль для извлечения и группировки физических и сетевых шин (Modbus TCP / RTU)
из конфигураций ведомых устройств ModbusSlaveManager.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, Dict, List, Literal, Optional, Tuple, Union

if TYPE_CHECKING:
    from ModbusSlaveEmu.device import EmuModbusSlaveDevice
    from ModbusSlaveEmu.manager import ModbusSlaveManager

logger = logging.getLogger("ModbusSlaveEmu.Bridge.BusCollector")


@dataclasses.dataclass
class BusInfo:
    """
    Описание единичной шины связи Modbus (TCP сервер или последовательный порт RTU).
    Объединяет несколько slave-устройств с разными Slave ID на одном сетевом сокете или COM-порту.
    """
    bus_id: str
    bus_type: Literal["tcp", "rtu"]
    # Параметры TCP
    host: Optional[str] = None
    port: Optional[int] = None
    # Параметры RTU
    serial_port: Optional[str] = None
    baudrate: Optional[int] = None
    parity: Optional[str] = None
    stop_bits: Optional[int] = None
    data_bits: Optional[int] = None
    # Список ведомых устройств на этой шине
    slave_ids: List[int] = dataclasses.field(default_factory=list)
    devices: List["EmuModbusSlaveDevice"] = dataclasses.field(default_factory=list)

    @property
    def endpoint_description(self) -> str:
        if self.bus_type == "tcp":
            return f"TCP {self.host}:{self.port} (Slaves: {self.slave_ids})"
        return f"RTU {self.serial_port} @ {self.baudrate} бод (Slaves: {self.slave_ids})"


class BusCollector:
    """
    Анализатор конфигураций устройств для автоматической группировки их по общим шинам.
    """

    @classmethod
    def collect_buses(cls, manager: "ModbusSlaveManager") -> List[BusInfo]:
        """
        Сканирует все устройства в менеджере и группирует их по уникальным шинам связи.

        :param manager: Экземпляр ModbusSlaveManager с загруженными устройствами.
        :return: Список уникальных шин BusInfo.
        """
        buses: Dict[str, BusInfo] = {}

        for dev in manager.get_all_devices():
            conn = dev.config.connection
            conn_type = conn.type.value if hasattr(conn.type, "value") else str(conn.type)
            slave_id = dev.config.slave_id

            if conn_type == "tcp" and conn.tcp:
                host = conn.tcp.host or "0.0.0.0"
                port = conn.tcp.port or 502
                bus_id = f"tcp://{host}:{port}"

                if bus_id not in buses:
                    buses[bus_id] = BusInfo(
                        bus_id=bus_id,
                        bus_type="tcp",
                        host=host,
                        port=port,
                    )
                buses[bus_id].slave_ids.append(slave_id)
                buses[bus_id].devices.append(dev)

            elif conn_type == "rtu" and conn.rtu:
                sport = conn.rtu.port
                baud = conn.rtu.baudrate or 9600
                bus_id = f"rtu://{sport}:{baud}"

                if bus_id not in buses:
                    buses[bus_id] = BusInfo(
                        bus_id=bus_id,
                        bus_type="rtu",
                        serial_port=sport,
                        baudrate=baud,
                        parity=conn.rtu.parity,
                        stop_bits=conn.rtu.stop_bits,
                        data_bits=conn.rtu.data_bits,
                    )
                buses[bus_id].slave_ids.append(slave_id)
                buses[bus_id].devices.append(dev)

            else:
                logger.warning(
                    "Устройство '%s' (Slave %d) имеет неподдерживаемый тип подключения: %s",
                    dev.config.device_id,
                    slave_id,
                    conn_type,
                )

        logger.info("Собрано %d шин связи из %d устройств", len(buses), len(manager.get_all_devices()))
        return list(buses.values())
